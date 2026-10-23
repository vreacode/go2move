#!/usr/bin/env python3
"""Cache standard RealSense ROS RGB + aligned depth with matching CameraInfo.

Reuses realsense2_camera and ROS message_filters, as in the go2_robot sensor
bringup. This process only subscribes; no base-link extrinsics are invented.
"""
import argparse
import hashlib
import io
import json
import math
import os
from pathlib import Path
import signal
import time
import cv2
import numpy as np


def stamp(message):
    return message.header.stamp.sec+message.header.stamp.nanosec/1e9


def decode_image(message):
    formats={'rgb8':('u1',3),'bgr8':('u1',3),'16UC1':('>u2' if message.is_bigendian else '<u2',1),
             '32FC1':('>f4' if message.is_bigendian else '<f4',1)}
    if message.encoding not in formats:
        raise ValueError('Unsupported RGB-D encoding')
    dtype,channels=formats[message.encoding];dtype=np.dtype(dtype)
    if not 1<=message.width<=1920 or not 1<=message.height<=1080:
        raise ValueError('Invalid RGB-D dimensions')
    row=message.width*channels*dtype.itemsize
    if message.step<row or len(message.data)<message.step*message.height:
        raise ValueError('Truncated RGB-D image')
    shape=(message.height,message.width,channels) if channels==3 else (message.height,message.width)
    strides=(message.step,channels*dtype.itemsize,dtype.itemsize) if channels==3 else (message.step,dtype.itemsize)
    image=np.ndarray(shape,dtype=dtype,buffer=message.data,strides=strides).copy()
    if message.encoding=='rgb8':image=cv2.cvtColor(image,cv2.COLOR_RGB2BGR)
    if channels==1:image=image.astype(np.float32)*(.001 if message.encoding=='16UC1' else 1.)
    return image


def encode_pair(color,depth,info,now):
    stamps=[stamp(item) for item in (color,depth,info)]
    if not all(math.isfinite(value) and 0<=now-value<=2 for value in stamps) or max(stamps)-min(stamps)>.04:
        raise ValueError('RGB-D timestamps stale, future or unsynchronized')
    if not color.header.frame_id or any(item.header.frame_id!=color.header.frame_id for item in (depth,info)):
        raise ValueError('RGB-D optical frames differ')
    if color.encoding not in ('rgb8','bgr8') or depth.encoding not in ('16UC1','32FC1'):
        raise ValueError('RGB-D encoding mismatch')
    if any((item.width,item.height)!=(color.width,color.height) for item in (depth,info)):
        raise ValueError('RGB-D grids differ')
    intrinsics=list(info.k)
    if len(intrinsics)!=9 or not all(math.isfinite(v) for v in intrinsics) or intrinsics[0]<=0 or intrinsics[4]<=0:
        raise ValueError('Missing RGB-D intrinsics')
    bgr=decode_image(color);depth_m=decode_image(depth)
    ok,jpeg=cv2.imencode('.jpg',bgr,[cv2.IMWRITE_JPEG_QUALITY,95])
    if not ok:raise ValueError('RGB-D JPEG encoding failed')
    raw=jpeg.tobytes()
    metadata={'source':'realsense_d435i','running':True,'captured_at':stamps[0],
        'depth_captured_at':stamps[1],'frame_id':color.header.frame_id,'depth_frame_id':depth.header.frame_id,
        'alignment':'color','width':color.width,'height':color.height,'intrinsics':intrinsics,
        'sha256':hashlib.sha256(raw).hexdigest(),'mount_extrinsics_verified':False}
    stream=io.BytesIO();np.savez_compressed(stream,depth_m=depth_m,metadata=json.dumps(metadata))
    bundle=stream.getvalue();metadata['bundle_sha256']=hashlib.sha256(bundle).hexdigest()
    return raw,bundle,metadata


def atomic(path,payload):
    temporary=path.with_name('.'+path.name+'.tmp');temporary.write_bytes(payload);os.replace(temporary,path)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=Path(__file__).with_name('runtime')/'rgbd_camera')
    parser.add_argument('--fps',type=float,default=5.)
    args=parser.parse_args()
    if not 0<args.fps<=15:parser.error('fps must be within (0,15]')
    import fcntl
    cache_lock=open('/tmp/go2-rgbd-cache.lock','a')
    fcntl.flock(cache_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    import rclpy
    import message_filters
    from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
    from sensor_msgs.msg import Image,CameraInfo
    rclpy.init();node=rclpy.create_node('go2_person_rgbd_cache',enable_rosout=False)
    args.output_dir.mkdir(parents=True,exist_ok=True);started=time.monotonic();last=0.;frames=0;rejected=0;last_stamps=None
    status={'source':'realsense_d435i','running':True,'frames':0,'captured_at':0,'error':'等待同步RGB-D画面。'}
    atomic(args.output_dir/'status.json',(json.dumps(status)+'\n').encode())
    def receive(color,depth,info):
        nonlocal last,frames,rejected,last_stamps,status
        if time.monotonic()-last<1/args.fps:return
        try:
            current=(stamp(color),stamp(depth))
            if last_stamps and any(value<=previous for value,previous in zip(current,last_stamps)):
                raise ValueError('RGB-D timestamps repeated or moved backwards')
            raw,bundle,meta=encode_pair(color,depth,info,time.time())
            atomic(args.output_dir/'bundle.npz',bundle);atomic(args.output_dir/'latest.jpg',raw)
            frames+=1;last=time.monotonic();last_stamps=current
            status=dict(meta,frames=frames,fps=round(frames/(last-started),2),rejected_pairs=rejected,error=None)
            atomic(args.output_dir/'status.json',(json.dumps(status)+'\n').encode())
        except (ValueError,OSError) as error:
            rejected+=1;status['error']=str(error)
    topics=[('/camera/color/image_raw',Image),('/camera/aligned_depth_to_color/image_raw',Image),('/camera/color/camera_info',CameraInfo)]
    # Follow observations must favor recent frames over draining a backlog.
    qos=QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT, history=HistoryPolicy.KEEP_LAST)
    subscribers=[message_filters.Subscriber(node,kind,topic,qos_profile=qos) for topic,kind in topics]
    synchronizer=message_filters.ApproximateTimeSynchronizer(subscribers,queue_size=3,slop=.04)
    synchronizer.registerCallback(receive)
    stopped=[False];signal.signal(signal.SIGTERM,lambda *_:stopped.__setitem__(0,True))
    try:
        while rclpy.ok() and not stopped[0]:rclpy.spin_once(node,timeout_sec=.1)
    except KeyboardInterrupt:pass
    finally:
        status['running']=False;atomic(args.output_dir/'status.json',(json.dumps(status)+'\n').encode())
        node.destroy_node()
        if rclpy.ok():rclpy.shutdown()


if __name__=='__main__':main()
