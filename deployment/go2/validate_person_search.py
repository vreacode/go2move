#!/usr/bin/env python3
"""Actual models, isolated public frames. Never replaces the real camera source."""
import asyncio
import base64
import hashlib
import json
from pathlib import Path
import tempfile
import time

import cv2
import numpy as np
import person_search as module
from go2_platform_write import write


def main():
    models = Path('/home/unitree/go2_ros2_ws/models/person_search')
    report = {'source':'public upstream image and recorded native empty scene',
              'physical_person_search_test':False, 'motion_commands_sent':0, 'frames':[]}
    image = cv2.imread(str(models/'public_bus.jpg'))
    reference = (models/'public_person_crop.jpg').read_bytes()
    with tempfile.TemporaryDirectory(prefix='go2-person-validation-') as folder:
        module.RUNTIME = Path(folder)/'results'
        module.CAMERA = Path(folder)/'input'
        module.RUNTIME.mkdir()
        module.CAMERA.mkdir()
        searcher = module.Searcher()
        module.searcher = searcher
        report['target'] = searcher.set_target(module.TargetRequest(
            image_base64=base64.b64encode(reference).decode(), name='公开软件样例'))
        asyncio.run(module.start(module.SearchRequest(speak=False)))

        def inspect(frame, age=0):
            ok, encoded = cv2.imencode('.jpg', frame)
            assert ok
            raw = encoded.tobytes()
            (module.CAMERA/'latest.jpg').write_bytes(raw)
            (module.CAMERA/'status.json').write_text(json.dumps({
                'running':True, 'captured_at':time.time()-age,
                'sha256':hashlib.sha256(raw).hexdigest()}))
            return searcher.inspect_frame()

        for shift in [0, 2, 4, 6, 8, 10]:
            moved = cv2.warpAffine(image, np.float32([[1,0,shift],[0,1,0]]),
                                   (image.shape[1],image.shape[0]), borderMode=cv2.BORDER_REPLICATE)
            result = inspect(moved)
            assert result is not None and len(result['candidates']) >= 1
            report['frames'].append(result)
        assert all(item['suspected_target'] for item in report['frames'][2:]), 'Multi-frame match failed'
        ids = [item['candidates'][0]['track_id'] for item in report['frames']]
        assert len(set(ids)) == 1, 'Target track changed during the small translation'
        response = asyncio.run(module.preview())
        assert response.media_type == 'image/jpeg' and response.body == searcher.preview_bytes
        assert cv2.imdecode(np.frombuffer(response.body,np.uint8),cv2.IMREAD_COLOR).shape == (540,960,3)
        report['preview_bytes'] = len(response.body)
        report['negative_scene'] = inspect(cv2.imread(str(models/'native_empty_scene.jpg')))
        assert not report['negative_scene']['candidates']
        assert not report['negative_scene']['suspected_target']
        try:
            inspect(image, age=5)
        except RuntimeError:
            report['stale_source_rejected'] = True
        else:
            raise AssertionError('Stale source was accepted')
        asyncio.run(module.stop())
        assert not searcher.status()['active'] and searcher.status()['last'] is None
        report['passed'] = True
        write(models/'person_pipeline_validation.json', json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'passed':True, 'positive_frames':len(ids),
                          'similarities':[round(item['candidates'][0]['similarity'],3) for item in report['frames']],
                          'inference_ms':[item['inference_ms'] for item in report['frames']],
                          'negative_scene_candidates':len(report['negative_scene']['candidates'])},ensure_ascii=False))
        searcher.detector.close()
        searcher.extractor.close()


if __name__ == '__main__':
    main()
