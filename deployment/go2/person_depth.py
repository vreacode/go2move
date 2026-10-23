"""Aligned RGB-D cache and conservative torso depth; no ROS or robot I/O."""
import hashlib
import io
import json
import math
import numpy as np


def load_depth(directory, status, jpeg, now):
    """Never associate a depth frame with a different camera/JPEG/timestamp."""
    if status.get('source') != 'realsense_d435i':
        return None
    if not status.get('running') or not 0 <= now-status.get('captured_at',0) <= 2:
        raise ValueError('RGB-D画面已过期。')
    if status.get('alignment') != 'color' or status.get('frame_id') != status.get('depth_frame_id'):
        raise ValueError('深度图未对齐彩色画面。')
    if abs(status['captured_at']-status.get('depth_captured_at',0)) > .04:
        raise ValueError('彩色与深度时间不匹配。')
    if hashlib.sha256(jpeg).hexdigest() != status.get('sha256'):
        raise ValueError('RGB-D彩色画面校验失败。')
    raw=(directory/'bundle.npz').read_bytes()
    if hashlib.sha256(raw).hexdigest() != status.get('bundle_sha256'):
        raise ValueError('RGB-D帧正在更新。')
    with np.load(io.BytesIO(raw),allow_pickle=False) as bundle:
        meta=json.loads(str(bundle['metadata'].item()))
        depth=np.array(bundle['depth_m'],dtype=np.float32,copy=True)
    if any(meta.get(key)!=status.get(key) for key in ('sha256','captured_at','depth_captured_at','frame_id')):
        raise ValueError('RGB-D帧信息不一致。')
    if depth.shape != (status.get('height'),status.get('width')):
        raise ValueError('深度和彩色图像尺寸不一致。')
    return depth


def torso_depth(depth, box, frame_id):
    """Optical-axis depth of the central torso, not map coordinates or identity."""
    if depth is None:
        return None
    if depth.ndim!=2 or len(box)!=4 or not all(math.isfinite(float(v)) for v in box):
        return None
    x1,y1,x2,y2=map(int,box);height,width=depth.shape
    x1,y1,x2,y2=max(0,x1),max(0,y1),min(width,x2),min(height,y2)
    if x2-x1<12 or y2-y1<24:
        return None
    w,h=x2-x1,y2-y1
    region=[x1+round(.35*w),y1+round(.25*h),x1+round(.65*w),y1+round(.60*h)]
    left,top,right,bottom=region;values=depth[top:bottom,left:right]
    valid=values[np.isfinite(values)]
    valid=valid[(valid>=.20)&(valid<=6.)]
    if valid.size<20 or valid.size<values.size*.5:
        return None
    median=float(np.median(valid));spread=float(np.quantile(valid,.9)-np.quantile(valid,.1))
    if spread>max(.25,.15*median):
        return None
    return {'depth_m':round(median,3),'measurement':'optical_axis_depth',
            'frame_id':frame_id,'roi':region,'valid_fraction':round(float(valid.size/values.size),3),
            'spread_m':round(spread,3),'camera_source':'realsense_d435i'}
