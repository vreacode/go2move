"""Persistent reference library; selecting a reference never starts a search."""
import copy
import hashlib
import json
from pathlib import Path
import re
import threading
import time
import uuid


class PersonTargets:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.index = self.directory/'targets.json'
        self.lock = threading.RLock()
        # Fail closed on corrupt storage rather than replacing a user's list.
        self.items = json.loads(self.index.read_text()) if self.index.exists() else {}
        if not isinstance(self.items, dict):
            raise ValueError('目标列表格式损坏，请从备份恢复。')

    @staticmethod
    def validate_id(target_id):
        if not isinstance(target_id, str) or not re.fullmatch(r'[0-9a-f]{32}', target_id):
            raise FileNotFoundError('目标不存在。')

    def commit(self, items):
        temporary = self.index.with_name('.targets.'+uuid.uuid4().hex+'.tmp')
        try:
            temporary.write_text(json.dumps(items, ensure_ascii=False, indent=2)+'\n')
            temporary.replace(self.index)
        finally:
            temporary.unlink(missing_ok=True)
        self.items = items

    def add(self, info, photo, reference):
        target_id = info['id']
        self.validate_id(target_id)
        with self.lock:
            if target_id in self.items:
                # Tombstones prevent deleted references from being re-imported.
                return False
            if hashlib.sha256(photo).hexdigest() != info['image_sha256']:
                raise ValueError('目标照片校验失败。')
            folder = self.directory/target_id
            folder.mkdir(exist_ok=True)
            (folder/'original.jpg').write_bytes(photo)
            (folder/'reference.jpg').write_bytes(reference)
            items = copy.deepcopy(self.items)
            items[target_id] = dict(info)
            self.commit(items)
            return True

    def get(self, target_id):
        self.validate_id(target_id)
        with self.lock:
            item = self.items.get(target_id)
            if item is None or item.get('deleted_at'):
                raise FileNotFoundError('目标不存在或已删除。')
            return copy.deepcopy(item)

    def image(self, target_id, original=False):
        with self.lock:
            item = self.get(target_id)
            raw = (self.directory/target_id/('original.jpg' if original else 'reference.jpg')).read_bytes()
            if original and hashlib.sha256(raw).hexdigest() != item['image_sha256']:
                raise ValueError('保存的目标照片校验失败。')
            return raw

    def list(self):
        with self.lock:
            items = [copy.deepcopy(item) for item in self.items.values() if not item.get('deleted_at')]
        items.sort(key=lambda item: item.get('uploaded_at', 0), reverse=True)
        return {'items': items, 'total': len(items)}

    def delete(self, target_id):
        from go2_platform_write import backup
        with self.lock:
            self.get(target_id)
            # Back up the index before removal. Images remain recoverable, but
            # deleted references disappear from all list/select/image routes.
            backup(self.index)
            items = copy.deepcopy(self.items)
            items[target_id]['deleted_at'] = time.time()
            self.commit(items)
        return {'deleted': True, 'id': target_id}

    def import_existing(self, runtime, history):
        """Keep the current reference and recover older references from evidence."""
        import cv2
        import numpy as np
        runtime = Path(runtime)
        if (runtime/'target.json').exists() and (runtime/'target.jpg').exists():
            info = json.loads((runtime/'target.json').read_text())
            if info['id'] not in self.items:
                raw = (runtime/'target.jpg').read_bytes()
                image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
                if image is None:
                    raise ValueError('当前目标照片无法读取。')
                x1,y1,x2,y2 = info['bbox']
                ok, encoded = cv2.imencode('.jpg', image[y1:y2,x1:x2])
                if not ok:
                    raise ValueError('当前目标缩略图生成失败。')
                info.setdefault('image_sha256', hashlib.sha256(raw).hexdigest())
                self.add(info, raw, encoded.tobytes())
        for event in history.export()['items']:
            target_id = event['target_id']
            if target_id in self.items or not re.fullmatch(r'[0-9a-f]{32}', target_id):
                continue
            raw = history.image(event['id'], 'target')
            image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                continue
            height,width = image.shape[:2]
            info = {'id':target_id, 'name':event['target_name'], 'kind':event['target_kind'],
                    'bbox':[0,0,width,height], 'width':width, 'height':height,
                    'source':'history', 'uploaded_at':event['found_at'],
                    'image_sha256':hashlib.sha256(raw).hexdigest(),
                    'score_kind':event['score_kind']}
            if event['target_kind'] == 'description':
                from person_attributes import parse_description
                info.update(parse_description(event['description']))
            self.add(info, raw, raw)
