#!/usr/bin/env python3
"""Reference-photo retrieval with an explicitly gated, bounded scan controller."""
import asyncio
import base64
import binascii
from contextlib import asynccontextmanager, suppress
import hashlib
import json
from pathlib import Path
import threading
import time
import uuid
from media_policy import voice_policy
from person_attributes import parse_description, color_fraction, description_image
from person_camera import camera_directory
from person_depth import load_depth, torso_depth
from person_follow import FollowController
from helmet_monitor import HelmetMonitor

import cv2
import httpx
import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

BASE = Path(__file__).resolve().parent
RUNTIME = BASE / 'runtime' / 'person_search'
CAMERA = camera_directory()
MODELS = Path('/home/unitree/go2_ros2_ws/models/person_search')
RUNTIME.mkdir(parents=True, exist_ok=True)


class TargetRequest(BaseModel):
    image_base64: str = Field(min_length=16, max_length=8_000_000)
    name: str = Field(default='目标人物', min_length=1, max_length=32)


class DescriptionRequest(BaseModel):
    text: str = Field(min_length=2, max_length=128)
    name: str = Field(default='', max_length=32)


class SearchRequest(BaseModel):
    mode: str = 'stationary'
    similarity_threshold: float = Field(default=.75, ge=.5, le=.99)
    speak: bool = False
    timeout_seconds: int = Field(default=120, ge=30, le=600)
    color_threshold: float = Field(default=.35, ge=.15, le=.9)


class CaptureRequest(BaseModel):
    name: str = Field(default='目标人物', min_length=1, max_length=32)


class CaptureSelection(CaptureRequest):
    capture_id: str = Field(min_length=1, max_length=64)
    index: int = Field(ge=0, le=99)


class Searcher:
    def __init__(self):
        self.lock = threading.RLock()
        self.follow = FollowController()
        self.helmet = HelmetMonitor(self)
        self.follow_task = None
        self.model_lock = threading.Lock()
        self.detector = self.extractor = None
        self.tracker = None
        self.reset_tracker = True
        self.target = None
        self.target_info = None
        self.active = False
        self.threshold = .75
        self.speak = True
        self.error = None
        self.last = None
        self.preview_bytes = None
        self.generation = 0
        self.frames = 0
        self.last_hash = None
        self.streak = 0
        self.streak_box = None
        self.last_track_id = None
        self.last_announcement = 0
        self.last_speech_attempt = 0
        self.announced_event_id = None
        self.speech = None
        self.task = None
        self.speech_task = None
        self.pending_capture = None
        self.reference_jpeg = None
        self._history = None
        self._targets = None
        self.current_event_id = None
        self.current_event_track_id = None
        self.rejected_track_ids = set()
        self.last_recorded_match_at = 0
        self.recording_error = None
        self.depth_error = None
        self.restore_task = None
        self._tasks = None

    @property
    def tasks(self):
        if self._tasks is None:
            from person_tasks import SearchTasks
            self._tasks = SearchTasks(RUNTIME/'tasks.json', clock=lambda: time.time())
        return self._tasks

    def check_task_progress(self):
        with self.lock:
            task = self.tasks.current
            if task and task['state'] == 'found_pending_confirmation' and task.get('found_event_id'):
                try:
                    event = self.history.get(task['found_event_id'])
                    action = self.tasks.apply_review(event.get('review_status', event.get('status')))
                    if action in ('completed', 'stopped'):
                        self.active = False
                        self.generation += 1
                    elif action == 'running':
                        # Human rejection applies to this track for this task.
                        # Invalidate any inference already running on the old selection.
                        self.rejected_track_ids.add(event['track_id'])
                        self.generation += 1
                        self.streak, self.streak_box, self.last_track_id = 0, None, None
                        self.current_event_id, self.current_event_track_id = None, None
                        self.last, self.preview_bytes, self.last_hash = None, None, None
                except (FileNotFoundError, AttributeError):
                    pass
            if self.tasks.expire():
                self.active = False
                self.generation += 1
                self.streak, self.streak_box, self.last_track_id = 0, None, None
            return self.tasks.current

    @property
    def history(self):
        if self._history is None:
            from person_history import PersonHistory
            self._history = PersonHistory(RUNTIME/'history')
        return self._history

    @property
    def targets(self):
        with self.lock:
            if self._targets is None:
                from person_targets import PersonTargets
                library = PersonTargets(RUNTIME/'targets')
                library.import_existing(RUNTIME, self.history)
                self._targets = library
            return self._targets

    def select_target(self, target_id):
        info = self.targets.get(target_id)
        raw = self.targets.image(target_id, original=True)
        image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError('目标照片无法读取。')
        box = self.clip_box(info['bbox'], image)
        description = parse_description(info['description']) if info.get('kind') == 'description' else None
        self.load(need_embeddings=description is None)
        if description:
            feature = {'color':description['color']}
        else:
            x1,y1,x2,y2 = box
            with self.model_lock:
                feature = self.extractor.embed(image[y1:y2,x1:x2])
        with self.lock:
            self.targets.get(target_id)  # Reject a deletion during embedding.
            return self.store_target(image, box, feature, info['name'], info['source'],
                                     description=description, saved_info=info)

    def clear_target(self):
        self.tasks.finish('stopped', '当前目标已删除。')
        self.follow.stop('当前目标已删除。')
        self.generation += 1
        self.active = False
        self.target = self.target_info = self.reference_jpeg = None
        self.pending_capture = self.last = self.preview_bytes = None
        self.streak = 0
        self.current_event_id = self.current_event_track_id = None
        self.speech = None
        for name in ('target.jpg', 'target.json'):
            (RUNTIME/name).unlink(missing_ok=True)

    @staticmethod
    def reference_preview(image, box):
        x1, y1, x2, y2 = box
        crop = image[y1:y2, x1:x2]
        height, width = crop.shape[:2]
        resized = cv2.resize(crop,(max(1,round(width*min(1,320/height))),min(height,320)))
        ok, encoded = cv2.imencode('.jpg',resized)
        if not ok:
            raise RuntimeError('目标缩略图生成失败。')
        return encoded.tobytes()

    def restore_target(self):
        """Keep the recorded person across restarts; never resume the search."""
        metadata, photo = RUNTIME/'target.json', RUNTIME/'target.jpg'
        if not metadata.is_file() or not photo.is_file():
            return
        try:
            self.targets  # Import old references before the active file changes.
            info = json.loads(metadata.read_text())
            raw = photo.read_bytes()
            if info.get('image_sha256') and hashlib.sha256(raw).hexdigest() != info['image_sha256']:
                raise ValueError('保存的目标照片与信息不一致，请重新拍照。')
            image = cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_COLOR)
            if image is None or image.shape[:2] != (info['height'],info['width']):
                raise ValueError('保存的目标照片无法读取，请重新拍照。')
            box = self.clip_box(info['bbox'],image)
            kind = info.get('kind', 'photo')
            if kind == 'description':
                attributes = parse_description(info['description'])
                if attributes['color'] != info.get('color'):
                    raise ValueError('保存的颜色描述与属性不一致。')
                self.load(need_embeddings=False)
                feature = {'color': attributes['color']}
            elif kind == 'photo':
                self.load()
                x1,y1,x2,y2 = box
                with self.model_lock:
                    feature = self.extractor.embed(image[y1:y2,x1:x2])
            else:
                raise ValueError('保存的目标类型不受支持。')
            info['kind'] = kind
            info.setdefault('score_kind', 'color_fraction' if kind == 'description' else 'appearance_similarity')
            reference = self.reference_preview(image,box)
            with self.lock:
                try:
                    self.targets.get(info['id'])
                except FileNotFoundError:
                    return  # Deleted while the saved reference was loading.
                # A newly supplied target takes precedence. Inference generation
                # changes (stop/helmet mode) must not discard the saved reference.
                # Restoring a reference never resumes a search or movement task.
                if self.target is None:
                    self.target,self.target_info,self.reference_jpeg = feature,info,reference
                    self.active = False
        except Exception as exc:
            self.error = '恢复目标失败：'+str(exc)[:180]

    def load(self, need_embeddings=True):
        with self.model_lock:
            if self.detector is None:
                from person_detector import Detector
                self.detector = Detector()
            if self.tracker is None:
                from person_tracker import Tracker
                self.tracker = Tracker(frame_rate=5)
            if need_embeddings and self.extractor is None:
                from person_reid import Extractor
                self.extractor = Extractor()
            self.error = None

    def set_description(self, payload):
        attributes = parse_description(payload.text)
        self.load(need_embeddings=False)
        image = description_image(attributes['color'])
        name = payload.name.strip() or attributes['color_label'] + '色上衣人物'
        return self.store_target(image, [0, 0, image.shape[1], image.shape[0]],
            {'color': attributes['color']}, name, source='description', description=attributes)

    def set_target(self, payload):
        encoded = payload.image_base64.split(',', 1)[-1]
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError('照片编码无效。') from exc
        if len(raw) > 6_000_000:
            raise ValueError('照片须小于 6 MB。')
        # Bound JPEG/PNG dimensions before OpenCV allocates decoded pixels.
        from PIL import Image
        import io
        try:
            with Image.open(io.BytesIO(raw)) as probe:
                if probe.width * probe.height > 24_000_000 or min(probe.size) < 32:
                    raise ValueError('照片尺寸不适合人物匹配。')
                probe.verify()
        except Exception as exc:
            raise ValueError('请上传有效的单人全身或半身照片（最大 2400 万像素）。') from exc
        image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError('照片解码失败。')
        self.load()
        with self.model_lock:
            detections = self.detector.detect(image)
            if len(detections) != 1:
                raise ValueError('照片检测到 %d 人，请使用清晰的单人全身或半身照片。' % len(detections))
            box = self.clip_box(detections[0]['bbox'], image)
            x1, y1, x2, y2 = box
            feature = self.extractor.embed(image[y1:y2, x1:x2])
        return self.store_target(image, box, feature, payload.name, source='upload')

    def store_target(self, image, box, feature, name, source, capture_id=None, captured_at=None, description=None, saved_info=None):
        from go2_platform_write import backup
        reference = self.reference_preview(image,box)
        ok, encoded = cv2.imencode('.jpg',image)
        if not ok:
            raise RuntimeError('目标照片保存失败。')
        photo = encoded.tobytes()
        with self.lock:
            self.follow.stop('参考目标已改变，请重新启动预览。')
            self.helmet.deactivate()
            if capture_id is not None:
                self.check_capture(capture_id)
            info = {'id': uuid.uuid4().hex, 'name': name, 'bbox': box, 'source':source,
                    'uploaded_at': time.time(), 'width': image.shape[1], 'height': image.shape[0],
                    'image_sha256':hashlib.sha256(photo).hexdigest(),
                    'kind': 'description' if description else 'photo',
                    'score_kind': 'color_fraction' if description else 'appearance_similarity'}
            if description:
                info.update(description)
            if captured_at is not None:
                info['captured_at'] = captured_at
            if saved_info:
                info['id'] = saved_info['id']
                info['uploaded_at'] = saved_info['uploaded_at']
                if 'captured_at' in saved_info:
                    info['captured_at'] = saved_info['captured_at']
            self.targets.add(info, photo, reference)
            for path in [RUNTIME/'target.jpg', RUNTIME/'target.json']:
                backup(path)
            temporary_photo = RUNTIME/'target.tmp.jpg'
            temporary_photo.write_bytes(photo)
            temporary_photo.replace(RUNTIME/'target.jpg')
            temporary_metadata = RUNTIME/'target.tmp.json'
            temporary_metadata.write_text(json.dumps(info, ensure_ascii=False, indent=2)+'\n')
            temporary_metadata.replace(RUNTIME/'target.json')
            self.tasks.finish('stopped', '目标已更换。')
            self.generation += 1
            self.target, self.target_info = (dict(feature) if description else np.asarray(feature)), info
            self.reference_jpeg = reference
            self.current_event_id = None
            self.current_event_track_id = None
            self.rejected_track_ids.clear()
            self.last_recorded_match_at = 0
            self.pending_capture = None
            self.active = False
            self.last = None
            self.preview_bytes = None
            self.streak = 0
            self.streak_box = None
            self.last_track_id = None
            self.reset_tracker = True
            self.last_hash = None
            self.last_announcement = 0
            self.last_speech_attempt = 0
            self.announced_event_id = None
            self.speech = None
        return {'target': info, 'motion_enabled': False, 'selection_required':False}

    @staticmethod
    def camera_source():
        info=json.loads((CAMERA/'status.json').read_text())
        return 'realsense_d435i' if info.get('source')=='realsense_d435i' else 'native_camera'

    @staticmethod
    def camera_frame():
        # Capture the image and its own metadata, not an older preview JPEG.
        for _ in range(4):
            try:
                status = json.loads((CAMERA/'status.json').read_text())
                captured_at = float(status.get('captured_at', 0))
                if not status.get('running') or not 0 <= time.time()-captured_at < 2:
                    raise ValueError('摄像头当前不在线，请恢复画面后重拍。')
                raw = (CAMERA/'latest.jpg').read_bytes()
                if hashlib.sha256(raw).hexdigest() != status.get('sha256'):
                    time.sleep(.02)
                    continue
                image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
                if image is None:
                    raise ValueError('摄像头画面解码失败，请重拍。')
                return image, captured_at
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError('暂时没有摄像头画面，请稍后重拍。') from exc
        raise ValueError('相机正在更新画面，请再拍一次。')

    def capture_target(self, payload):
        # Model loading may take seconds; obtain the fresh image afterwards.
        self.load()
        with self.model_lock:
            image, captured_at = self.camera_frame()
            detections = self.detector.detect(image)
            people = []
            for item in detections:
                try:
                    box = self.clip_box(item['bbox'], image)
                except ValueError:
                    continue
                people.append({'index':len(people), 'bbox':box, 'confidence':float(item['confidence'])})
            if not people:
                raise ValueError('画面里没有清晰的人物，请站到镜头前、露出半身或全身后重拍。')
            if len(people) == 1:
                box = people[0]['bbox']
                x1, y1, x2, y2 = box
                feature = self.extractor.embed(image[y1:y2, x1:x2])
                return self.store_target(image, box, feature, payload.name, source=self.camera_source(), captured_at=captured_at)
        height, width = image.shape[:2]
        preview_width = min(width, 960)
        preview = cv2.resize(image, (preview_width, max(1, round(height*preview_width/width))))
        ok, encoded = cv2.imencode('.jpg', preview)
        if not ok:
            raise RuntimeError('拍照预览生成失败。')
        capture_id = uuid.uuid4().hex
        with self.lock:
            self.generation += 1
            self.active = False
            self.last, self.preview_bytes = None, None
            self.tasks.finish('stopped', '重新拍照选择目标。')
            self.pending_capture = {'id':capture_id, 'expires':time.monotonic()+120,
                                    'image':image, 'people':people, 'captured_at':captured_at}
        return {'selection_required':True, 'capture_id':capture_id, 'captured_at':captured_at,
                'expires_in':120, 'width':width, 'height':height,
                'preview_base64':base64.b64encode(encoded.tobytes()).decode(), 'people':people}

    def check_capture(self, capture_id):
        pending = self.pending_capture
        if not pending or pending['id'] != capture_id or time.monotonic() > pending['expires']:
            raise ValueError('这次拍照已过期或已被替换，请重新拍照。')
        return pending

    def select_capture(self, payload):
        with self.lock:
            pending = self.check_capture(payload.capture_id)
            if payload.index >= len(pending['people']):
                raise ValueError('请选择画面中的人物框。')
            image, captured_at = pending['image'], pending['captured_at']
            box = pending['people'][payload.index]['bbox']
        with self.model_lock:
            x1, y1, x2, y2 = box
            feature = self.extractor.embed(image[y1:y2, x1:x2])
            return self.store_target(image, box, feature, payload.name, source=self.camera_source(),
                                     capture_id=payload.capture_id, captured_at=captured_at)

    @staticmethod
    def clip_box(box, image):
        height, width = image.shape[:2]
        x1, y1, x2, y2 = map(int, box)
        box = [max(0, x1), max(0, y1), min(width, x2), min(height, y2)]
        if box[2] - box[0] < 12 or box[3] - box[1] < 24:
            raise ValueError('人物过小，无法匹配。')
        return box

    def inspect_frame(self):
        self.check_task_progress()
        with self.lock:
            if not (self.active or self.follow.active) or self.target is None:
                return None
            feature = self.target.copy()
            target_info = dict(self.target_info)
            score_kind = 'color_fraction' if target_info.get('kind') == 'description' else 'appearance_similarity'
            target_id = self.target_info['id']
            generation = self.generation
            threshold = self.threshold
            rejected_tracks = set(self.rejected_track_ids)
        # Files are atomic at capture; confirm the image belongs to this status.
        status = json.loads((CAMERA/'status.json').read_text())
        captured_at = float(status.get('captured_at', 0))
        if not status.get('running') or not 0 <= time.time() - captured_at <= 2:
            raise RuntimeError('摄像头画面已中断。')
        raw = (CAMERA/'latest.jpg').read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != status.get('sha256') or digest == self.last_hash:
            return None
        image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError('实时画面解码失败。')
        depth,depth_error=None,None
        try:
            depth=load_depth(CAMERA,status,raw,time.time())
        except (OSError,ValueError,KeyError,TypeError) as exc:
            depth_error=str(exc)[:180]
        started = time.monotonic()
        candidates = []
        with self.model_lock:
            from person_tracker import Tracker
            if self.reset_tracker:
                self.tracker = Tracker(frame_rate=5)
                self.reset_tracker = False
            detections, crops = [], []
            observed = self.detector.detect(image)
            for detection in self.tracker.update(observed, image.shape):
                try:
                    box = self.clip_box(detection['bbox'], image)
                except ValueError:
                    continue
                x1, y1, x2, y2 = box
                detections.append((detection, box))
                crops.append(image[y1:y2, x1:x2])
            if score_kind == 'color_fraction':
                for detection, box in detections:
                    score, region = color_fraction(image, box, target_info['color'])
                    candidates.append({'bbox': box, 'track_id': detection['track_id'],
                        'score': score, 'similarity': score, 'color_fraction': score,
                        'score_kind': score_kind, 'upper_body_bbox': region,
                        'detection_confidence': float(detection['confidence'])})
            else:
                embeddings = self.extractor.embed_many(crops)
                for (detection, box), embedding in zip(detections, embeddings):
                    similarity = float(np.dot(feature, embedding))
                    candidates.append({'bbox': box, 'track_id':detection['track_id'], 'similarity': similarity,
                        'score': similarity, 'score_kind': score_kind,
                        'detection_confidence': float(detection['confidence'])})
        for candidate in candidates:
            candidate['camera_source']=status.get('source','native_camera')
            candidate['depth']=torso_depth(depth,candidate['bbox'],status.get('frame_id'))
            if candidate['track_id'] in rejected_tracks:
                candidate['excluded_by_review'] = True
        candidates.sort(key=lambda item: (item['track_id'] in rejected_tracks, -item['similarity']))
        eligible = [item for item in candidates if item['track_id'] not in rejected_tracks]
        best = eligible[0] if eligible else None
        margin = best['similarity'] - eligible[1]['similarity'] if len(eligible) > 1 else 1.
        matched = bool(best and best['similarity'] >= threshold and
                       (score_kind == 'color_fraction' or margin >= .06))
        for candidate in candidates:
            x1, y1, x2, y2 = candidate['bbox']
            color = (0, 190, 255) if candidate is best and matched else (255, 180, 0)
            cv2.rectangle(image, (x1, y1), (x2, y2), color, 3)
            cv2.putText(image, 'track %d %s %.2f' % (candidate['track_id'], 'color' if score_kind == 'color_fraction' else 'appearance', candidate['similarity']),
                        (x1, max(20, y1-8)), cv2.FONT_HERSHEY_SIMPLEX, .65, color, 2)
            if candidate['depth']:
                cv2.putText(image,'Z %.2f m'%candidate['depth']['depth_m'],(x1,min(image.shape[0]-8,y1+24)),
                            cv2.FONT_HERSHEY_SIMPLEX,.60,color,2)
        preview = cv2.resize(image, (960, max(1, round(960*image.shape[0]/image.shape[1]))))
        encoded, buffer = cv2.imencode('.jpg', preview)
        if not encoded:
            raise RuntimeError('人物预览编码失败。')
        preview_bytes = buffer.tobytes()
        self.check_task_progress()
        with self.lock:
            if not (self.active or self.follow.active) or self.generation != generation or self.target_info['id'] != target_id:
                self.reset_tracker = True
                return None
            if time.time() - captured_at > 2:
                raise RuntimeError('人物结果已过期，等待新画面。')
            self.streak = self.streak + 1 if matched and self.last_track_id == best['track_id'] else int(matched)
            self.streak_box = best['bbox'] if matched else None
            self.last_track_id = best['track_id'] if matched else None
            self.last_hash = digest
            self.frames += 1
            result = {'captured_at': captured_at, 'processed_at': time.time(),
                      'target_id': target_id, 'generation':generation, 'candidates': candidates, 'streak': self.streak,
                      'suspected_target': matched and self.streak >= 3,
                      'inference_ms': round((time.monotonic()-started)*1000, 1),
                      'similarity_threshold': threshold, 'match_threshold': threshold, 'score_kind': score_kind,
                      'target_kind': target_info.get('kind', 'photo'), 'motion_enabled': False,
                      'camera_source':status.get('source','native_camera'),'depth_error':depth_error}
            intrinsics = status.get('intrinsics') or []
            result['camera_geometry'] = {'width': status.get('width'), 'height': status.get('height'),
                'frame_id': status.get('frame_id'), 'fx': intrinsics[0] if len(intrinsics) == 9 else None,
                'cx': intrinsics[2] if len(intrinsics) == 9 else None}
            if self.active and result['suspected_target']:
                try:
                    # Each uninterrupted appearance is one record, not one per frame.
                    if (captured_at - self.last_recorded_match_at > 5 or
                            best['track_id'] != self.current_event_track_id):
                        self.current_event_id = None
                    if self.reference_jpeg is None:
                        raise RuntimeError('目标参考图未就绪，请重新拍照。')
                    event = self.history.record(self.target_info,best,captured_at,preview_bytes,
                                                self.reference_jpeg,self.current_event_id)
                    self.current_event_id = event['id']
                    self.current_event_track_id = best['track_id']
                    self.last_recorded_match_at = captured_at
                    self.recording_error = None
                    result['event_id'] = event['id']
                    self.tasks.found(event)
                except Exception as exc:
                    self.recording_error = '发现记录保存失败：'+str(exc)[:180]
            temporary = RUNTIME/'latest.tmp.jpg'
            temporary.write_bytes(preview_bytes)
            temporary.replace(RUNTIME/'latest.jpg')
            (RUNTIME/'last.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
            self.last, self.preview_bytes, self.error = result, preview_bytes, None
            self.depth_error=depth_error
        return result

    def maybe_announce(self, result):
        """Queue once per encounter; only successful delivery suppresses retries."""
        with self.lock:
            if (not result or not self.active or not self.speak or not voice_policy()['enabled'] or
                    result.get('generation') != self.generation or
                    not result.get('suspected_target') or
                    not 0 <= time.time()-result['captured_at'] <= 2):
                return False
            if self.speech_task is not None and not self.speech_task.done():
                return False
            event_id = result.get('event_id') or '%s:%s' % (self.generation,self.last_track_id)
            if self.announced_event_id == event_id:
                return False
            if self.last_speech_attempt and time.monotonic()-self.last_speech_attempt < 5:
                return False
            self.last_speech_attempt = time.monotonic()
            self.speech = {'state':'sending','delivered':False,'generation':self.generation,
                           'event_id':event_id,'attempted_at':time.time(),'error':None}
            self.speech_task = asyncio.create_task(self.announce(dict(result,event_id=event_id)))
            return True

    async def announce(self, result):
        try:
            if not self.active or self.generation != result['generation']:
                return
            if not 0 <= time.time()-result['captured_at'] <= 2:
                raise RuntimeError('观测已过期，本次播报取消；等待新画面。')
            async with httpx.AsyncClient(trust_env=False, timeout=30) as client:
                response = await client.post('http://127.0.0.1:8767/say', json={'text':'发现疑似目标，请现场确认。'})
                response.raise_for_status()
                delivery = response.json()
            if not delivery.get('delivered'):
                raise RuntimeError(delivery.get('error') or '语音服务未成功发送播报。')
            outcome = dict(delivery,state='sent',error=None)
        except Exception as exc:
            outcome = {'state':'failed','delivered':False,'error':str(exc)[:180] or type(exc).__name__}
        with self.lock:
            if self.generation != result['generation'] or not self.active:
                return
            self.speech = dict(self.speech or {},**outcome,completed_at=time.time())
            if outcome['delivered']:
                self.last_announcement = time.time()
                self.announced_event_id = result['event_id']
            # A following camera frame must not erase the announcement outcome.
            temporary = RUNTIME/'speech.tmp.json'
            temporary.write_text(json.dumps(self.speech,ensure_ascii=False,indent=2)+'\n')
            temporary.replace(RUNTIME/'speech.json')

    async def follow_watchdog(self):
        # Independent of model latency and browser polling. Preview only.
        while True:
            with self.lock:
                self.follow.update(self.last, (self.target_info or {}).get('id'), time.time())
            await asyncio.sleep(.1)

    async def run(self):
        while True:
            helmet_frame=False
            try:
                self.check_task_progress()
                helmet_frame=self.helmet.active
                if helmet_frame:
                    await asyncio.get_running_loop().run_in_executor(None,self.helmet.inspect)
                elif (self.active or self.follow.active) and self.target is not None:
                    result = await asyncio.get_running_loop().run_in_executor(None, self.inspect_frame)
                    self.maybe_announce(result)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if helmet_frame:
                    self.helmet.error=str(exc)[:250]
                    self.helmet.last=None
                else:
                    self.error = str(exc)[:250]
                    self.streak = 0
                    self.streak_box = None
                    self.last = None
            with self.lock:
                self.follow.update(self.last, (self.target_info or {}).get('id'), time.time())
            await asyncio.sleep(.1 if (self.active or self.follow.active or self.helmet.active) else .5)

    def status(self):
        search_task = self.check_task_progress()
        with self.lock:
            last = self.last
            if last and time.time()-last['captured_at'] > 2:
                last = None
            try:
                history = self.history.list(1)
            except Exception as exc:
                history = {'total':0,'items':[]}
                self.recording_error = '发现记录暂不可读取：'+str(exc)[:180]
            try:
                camera = json.loads((CAMERA/'status.json').read_text())
                source=camera.get('source','native_camera')
                camera = {'connected':bool(camera.get('running') and 0 <= time.time()-camera.get('captured_at',0) < 2),
                          'width':camera.get('width'), 'height':camera.get('height'), 'fps':camera.get('fps'),
                          'source':source,'label':'D435i 彩色＋深度' if source=='realsense_d435i' else '机身摄像头',
                          'depth_aligned':camera.get('alignment')=='color','mount_extrinsics_verified':False}
            except (OSError,ValueError):
                camera = {'connected':False}
            return {'models_ready': self.detector is not None and (self.extractor is not None or
                        (self.target_info or {}).get('kind') == 'description'),
                    'search_task': search_task,
                    'target': self.target_info, 'active': self.active, 'mode': 'stationary',
                    'frames': self.frames, 'last': last, 'error': self.error,
                    'tracker':'ByteTrack' if self.tracker is not None else None,
                    'detector_backend':getattr(self.detector, 'backend', None),
                    'camera':camera, 'history_count':history['total'],
                    'follow':self.follow.update(last, (self.target_info or {}).get('id'), time.time()),
                    'latest_event':history['items'][0] if history['items'] else None,
                    'recording_error':self.recording_error,'depth_error':self.depth_error,'speech':self.speech,'speak':self.speak,
                    'voice_policy':voice_policy(),
                    'motion_enabled': False, 'navigation_ready': False,
                    'similarity_note': '上衣颜色占比受光照和遮挡影响，不是身份确认。' if
                        (self.target_info or {}).get('kind') == 'description' else '外观相似度不是身份确认置信度。'}


searcher = Searcher()


@asynccontextmanager
async def lifespan(app):
    searcher.tasks  # Marks unfinished persisted jobs interrupted; never resumes.
    searcher.restore_task = asyncio.get_running_loop().run_in_executor(None,searcher.restore_target)
    searcher.task = asyncio.create_task(searcher.run())
    searcher.follow_task = asyncio.create_task(searcher.follow_watchdog())
    yield
    searcher.helmet.deactivate()
    searcher.follow.stop('服务退出，跟随预览结束。')
    searcher.follow_task.cancel()
    with suppress(asyncio.CancelledError):
        await searcher.follow_task
    await scanner.stop()
    await patrol.execution.cancel()
    searcher.task.cancel()
    with suppress(asyncio.CancelledError):
        await searcher.task
    if searcher.speech_task:
        searcher.speech_task.cancel()
        with suppress(asyncio.CancelledError):
            await searcher.speech_task


app = FastAPI(title='Go2 stationary person search', lifespan=lifespan)


@app.get('/status')
async def status():
    return searcher.status()


@app.post('/description')
async def description(payload: DescriptionRequest):
    # Reject unsupported requests before changing a current search.
    try:
        parse_description(payload.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await scanner.stop()
    try:
        return await asyncio.get_running_loop().run_in_executor(None, searcher.set_description, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail='颜色筛选目标未就绪：'+str(exc)[:180]) from exc


@app.get('/tasks')
async def tasks(limit: int = Query(default=20, ge=1, le=100)):
    searcher.check_task_progress()
    return searcher.tasks.list(limit)


@app.post('/target')
async def target(payload: TargetRequest):
    await scanner.stop()
    try:
        return await asyncio.get_running_loop().run_in_executor(None, searcher.set_target, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail='人物模型尚未就绪：'+str(exc)[:180]) from exc


@app.post('/capture')
async def capture(payload: CaptureRequest):
    await scanner.stop()
    try:
        return await asyncio.get_running_loop().run_in_executor(None, searcher.capture_target, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail='拍照记录目标失败：'+str(exc)[:180]) from exc


@app.post('/capture/select')
async def select_capture(payload: CaptureSelection):
    await scanner.stop()
    try:
        return await asyncio.get_running_loop().run_in_executor(None, searcher.select_capture, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail='目标记录失败：'+str(exc)[:180]) from exc


@app.post('/start')
async def start(payload: SearchRequest):
    await scanner.stop()
    return begin_stationary_search(payload)


def begin_stationary_search(payload):
    if payload.mode != 'stationary':
        raise HTTPException(status_code=409, detail='指定区域巡检和自主探索须先完成导航验证与现场运动授权。')
    if searcher.target is None:
        raise HTTPException(status_code=409, detail='请先拍照、上传照片或设置单色上衣描述。')
    with searcher.lock:
        searcher.follow.stop('切换到静止找人。')
        searcher.helmet.deactivate()
        searcher.tasks.start(searcher.target_info, payload.timeout_seconds)
        searcher.generation += 1
        threshold = payload.color_threshold if searcher.target_info.get('kind') == 'description' else payload.similarity_threshold
        searcher.threshold, searcher.speak, searcher.active = threshold, payload.speak and voice_policy()['enabled'], True
        searcher.last, searcher.streak, searcher.streak_box = None, 0, None
        searcher.preview_bytes, searcher.last_hash = None, None
        searcher.last_track_id, searcher.reset_tracker = None, True
        searcher.current_event_id, searcher.last_recorded_match_at = None, 0
        searcher.current_event_track_id = None
        searcher.rejected_track_ids.clear()
        searcher.last_announcement, searcher.last_speech_attempt = 0, 0
        searcher.announced_event_id, searcher.speech = None, None
    return searcher.status()


@app.post('/stop')
async def stop():
    await scanner.stop()
    with searcher.lock:
        searcher.follow.stop()
        searcher.helmet.deactivate()
        searcher.tasks.finish('stopped', '用户停止查找。')
        searcher.generation += 1
        searcher.active = False
        searcher.last, searcher.streak, searcher.streak_box = None, 0, None
        searcher.preview_bytes = None
        searcher.speech = None
    return searcher.status()


@app.get('/preview.jpg')
async def preview():
    with searcher.lock:
        last = searcher.status()['last']
        if not last or not searcher.preview_bytes:
            raise HTTPException(status_code=503, detail='等待新的找人结果。')
        content = searcher.preview_bytes
    return Response(content=content, media_type='image/jpeg', headers={'Cache-Control':'no-store'})


@app.get('/camera.jpg')
async def camera_preview():
    try:
        image,_ = await asyncio.get_running_loop().run_in_executor(None,searcher.camera_frame)
        image = cv2.resize(image,(960,max(1,round(image.shape[0]*960/image.shape[1]))))
        ok,encoded = cv2.imencode('.jpg',image)
        if not ok:
            raise ValueError('实时画面编码失败。')
        return Response(content=encoded.tobytes(),media_type='image/jpeg',headers={'Cache-Control':'no-store'})
    except Exception as exc:
        raise HTTPException(status_code=503,detail=str(exc)[:180]) from exc


@app.get('/target.jpg')
async def target_preview():
    with searcher.lock:
        if searcher.reference_jpeg is None:
            raise HTTPException(status_code=404,detail='尚未设置目标。')
        return Response(content=searcher.reference_jpeg,media_type='image/jpeg',headers={'Cache-Control':'no-store'})


@app.get('/history')
async def history(limit: int = Query(default=20,ge=1,le=100)):
    return searcher.history.list(limit)


@app.get('/history/{event_id}/{kind}.jpg')
async def history_image(event_id: str,kind: str):
    try:
        content = searcher.history.image(event_id,kind)
        return Response(content=content,media_type='image/jpeg',headers={'Cache-Control':'no-store'})
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404,detail='记录图片不存在。') from exc


# Reuse the existing authenticated Dashboard proxy and local person service.
from person_patrol import attach_patrol
patrol = attach_patrol(app)

from person_scan import attach_scan
async def activate_scan_search():
    return begin_stationary_search(SearchRequest(speak=False))
scanner = attach_scan(app, searcher, activate_scan_search)

from person_history_actions import attach_history_actions
attach_history_actions(app, searcher)

from person_target_actions import attach_target_actions
attach_target_actions(app, searcher, scanner)

from person_follow import attach_follow
attach_follow(app, searcher, scanner.stop)

from helmet_monitor import attach_helmet
attach_helmet(app, searcher, scanner.stop)
