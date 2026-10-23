#!/usr/bin/env python3
"""Offline real-model PPE pipeline validation, isolated from live history.

Public SHWD examples exercise real YOLOX + PPE + ByteTrack + evidence storage.
They are not staged live camera results and are never written into production.
"""
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch
import hashlib
import cv2
from helmet_monitor import HelmetMonitor
from helmet_detector import HelmetDetector
from person_detector import Detector
from person_tracker import Tracker
from go2_platform_write import backup,write


def main():
    root=Path('/home/unitree/go2_ros2_ws/validation/helmet_demo_20261023')
    detector=Detector();ppe=HelmetDetector();report={'mode':'offline_public_samples','scenes':[]}
    warm=cv2.imread(str(root/'shwd_2.jpg'))
    detector.detect(warm);ppe.detect(warm)  # Keep first CUDA kernel warmup outside timestamped frames.
    for name in ('shwd_1','shwd_2','shwd_3'):
        with tempfile.TemporaryDirectory(prefix='go2-helmet-offline-') as folder:
            directory=Path(folder);camera=directory/'camera';camera.mkdir()
            searcher=SimpleNamespace(model_lock=threading.RLock(),detector=detector)
            monitor=HelmetMonitor(searcher,directory/'monitor');monitor.detector=ppe
            monitor.tracker=Tracker(frame_rate=3);monitor.active=True;monitor.session_id='offline-sample'
            raw=(root/(name+'.jpg')).read_bytes()
            # Different capture timestamps, same image bytes: stationary replay,
            # not a motion/occlusion benchmark. Exercise normal cache deduplication.
            durations=[]
            with patch('helmet_monitor.camera_directory',return_value=camera):
                for frame in range(5):
                    (camera/'latest.jpg').write_bytes(raw)
                    meta={'running':True,'captured_at':time.time(),'source':'offline_public_sample',
                          'sha256':hashlib.sha256(raw).hexdigest()}
                    (camera/'status.json').write_text(json.dumps(meta))
                    at=time.monotonic();monitor.inspect();durations.append(time.monotonic()-at)
                    time.sleep(.1)
            status=monitor.status()
            out=root/(name+'_annotated.jpg');backup(out);out.write_bytes(monitor.preview)
            item={'image':name+'.jpg','source':'https://github.com/njvisionpower/Safety-Helmet-Wearing-Dataset',
                  'counts':status['last']['counts'],'people':status['last']['people'],
                  'separate_records':status['record_count'],'frame_processing_seconds':durations}
            report['scenes'].append(item);print(name,item['counts'],'records',item['separate_records'],flush=True)
    write(root/'real_model_offline.json',json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report


if __name__=='__main__':main()
