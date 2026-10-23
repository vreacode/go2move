"""Stationary multi-person PPE monitoring, evidence and ByteTrack association.

Reuses the current camera cache and person detector. No speed, gait, audio, ROS
publisher or SDK command is emitted. Track IDs describe camera tracks, not identity.
"""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import threading
import time
import uuid

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from fastapi import HTTPException, Query
from fastapi.responses import Response
from person_camera import camera_directory
from person_depth import load_depth, torso_depth
from helmet_rules import HelmetRules, iou


def atomic(path, data):
    path=Path(path);temporary=path.with_name('.'+path.name+'.tmp')
    temporary.write_bytes(data);temporary.replace(path)


class HelmetEvidence:
    def __init__(self, directory):
        self.directory=Path(directory);self.path=self.directory/'records.json'
        self.lock=threading.RLock()
        self.items=json.loads(self.path.read_text()) if self.path.exists() else []
        self.directory.mkdir(parents=True,exist_ok=True)

    def record(self, session, person, stamp, snapshot, crop, source):
        with self.lock:
            # Publish only durable state. A failed disk write must not turn into
            # an in-memory "saved" record or increment the observed count.
            items=deepcopy(self.items)
            entry=next((r for r in items if r['session_id']==session and r['track_id']==person['track_id']),None)
            if entry is None:
                if len(self.items)>=500:raise ValueError('已达到500条记录，请先导出并归档。')
                entry={'id':uuid.uuid4().hex,'session_id':session,'track_id':person['track_id'],
                       'found_at':stamp,'last_seen_at':stamp,'observations':0,'review_status':'suspected',
                       'note':'模型识别的未戴安全帽候选，需人工复核。', 'camera_source':source,
                       'confidence':person['confidence'],'depth':person.get('depth')}
                folder=self.directory/entry['id'];folder.mkdir()
                atomic(folder/'snapshot.jpg',snapshot);atomic(folder/'crop.jpg',crop)
                items.insert(0,entry)
            entry.update(last_seen_at=stamp,observations=entry['observations']+1,
                         last_confidence=person['confidence'],last_depth=person.get('depth'))
            atomic(self.path,(json.dumps(items,ensure_ascii=False,indent=2)+'\n').encode())
            self.items=items
            return dict(entry)

    def list(self):
        with self.lock:return {'items':[dict(item) for item in self.items[:100]],'total':len(self.items)}

    def image(self,event_id,kind):
        if not re.fullmatch('[0-9a-f]{32}',event_id) or kind not in ('snapshot','crop'):
            raise FileNotFoundError('记录不存在。')
        with self.lock:
            if not any(r['id']==event_id for r in self.items):raise FileNotFoundError('记录不存在。')
            return (self.directory/event_id/(kind+'.jpg')).read_bytes()


class HelmetMonitor:
    def __init__(self, searcher, directory=None):
        self.searcher=searcher;self.lock=threading.RLock()
        self.directory=Path(directory or Path(__file__).with_name('runtime')/'helmet_monitor')
        self._evidence=None
        self.detector=None;self.tracker=None;self.rules=HelmetRules()
        self.active=False;self.loading=False;self.session_id=None;self.generation=0
        self.last=None;self.preview=None;self.last_hash=None;self.error=None;self.frames=0
        self.font=ImageFont.truetype('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',18)
        self.fonts={1:self.font}

    @property
    def evidence(self):
        if self._evidence is None:self._evidence=HelmetEvidence(self.directory/'evidence')
        return self._evidence

    def load(self):
        self.searcher.load(need_embeddings=False)
        if self.detector is None:
            from helmet_detector import HelmetDetector
            self.detector=HelmetDetector()

    def start(self):
        from person_tracker import Tracker
        with self.lock:
            self.generation+=1;self.active=True;self.session_id=uuid.uuid4().hex
            self.rules=HelmetRules();self.tracker=Tracker(frame_rate=3)
            self.last=self.preview=self.last_hash=None
            self.frames=0;self.error=None
            return self.status()

    def deactivate(self):
        with self.lock:
            self.generation+=1;self.active=False

    def stop(self):
        self.deactivate()
        return self.status()

    def status(self):
        with self.lock:
            last=self.last
            fresh=bool(last and 0<=time.time()-last['captured_at']<=2)
            if not fresh:last=None
            return {'active':self.active,'loading':self.loading,'models_ready':self.detector is not None,
                    'session_id':self.session_id,'frames':self.frames,'last':last,'error':self.error,
                    'motion_enabled':False,'physical_execution':False,
                    'record_count':self.evidence.list()['total'],
                    'model_source':getattr(self.detector,'model_source',None),
                    'model_backend':getattr(self.detector,'backend',None),
                    'model_sha256':getattr(self.detector,'model_sha256',None),'tracker':'ByteTrack',
                    'identity_note':'编号只用于本轮画面追踪；长遮挡或离开画面后编号可能变化。',
                    'evidence_note':'连续三帧明确未戴安全帽才记录；头部不可见或归属不明确时待确认。'}

    def inspect(self):
        with self.lock:
            if not self.active:return
            generation=self.generation
        directory=camera_directory()
        meta=json.loads((directory/'status.json').read_text());stamp=float(meta.get('captured_at',0))
        if not meta.get('running') or not 0<=time.time()-stamp<=2:
            raise ValueError('相机画面中断或过期，请检查相机服务。')
        raw=(directory/'latest.jpg').read_bytes();digest=hashlib.sha256(raw).hexdigest()
        if digest!=meta.get('sha256'):return
        # A stationary person can produce identical JPEG pixels in distinct
        # captures. Deduplicate by capture time too, so three real new frames
        # still confirm an event while polling one cached frame never does.
        if digest==self.last_hash and stamp==self.rules.last_stamp:return
        image=cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_COLOR)
        if image is None:raise ValueError('相机画面无法解码。')
        source=meta.get('source','native_camera');depth=None
        try:depth=load_depth(directory,meta,raw,time.time())
        except (OSError,ValueError,TypeError,KeyError):pass  # RGB PPE stays usable without ranging.
        started=time.monotonic()
        with self.searcher.model_lock:
            bodies=self.searcher.detector.detect(image)
        helmets=self.detector.detect(image)
        # Retain PPE person boxes missed by the general person model, with no duplicates.
        for candidate in helmets:
            if candidate['label']=='Person' and candidate['confidence']>=.5 and not any(
                    iou(candidate['bbox'],p['bbox'])>.45 for p in bodies):
                bodies.append(candidate)
        with self.lock:
            if not self.active or generation!=self.generation:return
            tracked=self.tracker.update(bodies,image.shape)
            now=time.time()
            people=self.rules.update(tracked,helmets,stamp,now)
            for person in people:
                person['depth']=torso_depth(depth,person['bbox'],meta.get('frame_id'))
            counts={key:sum(p['state']==key for p in people) for key in ('helmet','no_helmet','unknown')}
            view=self.annotate(image,people,counts,stamp)
            ok,jpeg=cv2.imencode('.jpg',view,[cv2.IMWRITE_JPEG_QUALITY,90])
            if not ok:raise ValueError('安全帽预览编码失败。')
            preview=jpeg.tobytes();recording_error=None
            for person in people:
                if not person['confirmed_violation']:continue
                x1,y1,x2,y2=person['bbox'];h,w=image.shape[:2]
                x1,x2=max(0,int(x1)),min(w,int(x2));y1,y2=max(0,int(y1)),min(h,int(y2))
                crop=image[y1:y2,x1:x2]
                if crop.size==0:continue
                ok,crop_jpeg=cv2.imencode('.jpg',crop)
                if not ok:continue
                try:
                    event=self.evidence.record(self.session_id,person,stamp,preview,crop_jpeg.tobytes(),source)
                    person['event_id']=event['id']
                except (OSError,ValueError) as exc:recording_error=str(exc)[:180]
            self.last={'captured_at':stamp,'processed_at':now,'people':people,'counts':counts,'total':len(people),
                       'camera_source':source,'inference_ms':round((time.monotonic()-started)*1000,1),
                       'recording_error':recording_error,'motion_enabled':False}
            self.frames+=1;self.last_hash=digest;self.preview=preview;self.error=None
            self.directory.mkdir(parents=True,exist_ok=True)
            atomic(self.directory/'latest.jpg',preview)
            atomic(self.directory/'last.json',(json.dumps(self.last,ensure_ascii=False)+'\n').encode())

    def annotate(self,image,people,counts,stamp):
        view=image.copy()
        scale=max(1,round(view.shape[1]/960))
        if scale not in self.fonts:self.fonts[scale]=ImageFont.truetype('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',18*scale)
        font=self.fonts[scale]
        colors={'helmet':(55,165,35),'no_helmet':(40,40,225),'unknown':(0,165,235)}
        labels={'helmet':'已戴安全帽','no_helmet':'未戴安全帽 · 目标','unknown':'待确认'}
        for person in people:
            box=[int(v) for v in person['bbox']];color=colors[person['state']]
            cv2.rectangle(view,tuple(box[:2]),tuple(box[2:]),color,2)
            trail=np.asarray(person['trail'],np.int32)
            if len(trail)>1:cv2.polylines(view,[trail],False,color,2)
        pil=Image.fromarray(cv2.cvtColor(view,cv2.COLOR_BGR2RGB));draw=ImageDraw.Draw(pil)
        draw.rectangle((0,0,view.shape[1],58*scale),fill=(15,23,42))
        text='多人安全帽检测  总人数 %d  佩戴 %d  未戴 %d  待确认 %d'%(len(people),counts['helmet'],counts['no_helmet'],counts['unknown'])
        draw.text((8*scale,3*scale),text,font=font,fill='white')
        draw.text((8*scale,29*scale),time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(stamp))+'  仅画面追踪',font=font,fill=(170,200,230))
        for p in people:
            x,y=int(p['bbox'][0]),max(60*scale,int(p['bbox'][1])-25*scale)
            color=colors[p['state']][::-1]
            text='#%d %s'%(p['track_id'],labels[p['state']])
            draw.text((x,y),text,font=font,fill=color,stroke_width=scale,stroke_fill=(255,255,255))
        return cv2.cvtColor(np.asarray(pil),cv2.COLOR_RGB2BGR)


def attach_helmet(app,searcher,stop_scan):
    monitor=searcher.helmet

    @app.get('/helmet/status')
    async def status():return monitor.status()

    @app.post('/helmet/start')
    async def start():
        with monitor.lock:
            if monitor.loading:raise HTTPException(409,'安全帽模型正在加载，请稍后。')
            monitor.loading=True
            monitor.generation+=1
            generation=monitor.generation
        try:
            await stop_scan()
            with searcher.lock:
                searcher.follow.stop('切换到安全帽多人追踪。')
                searcher.tasks.finish('stopped','切换到安全帽多人追踪。')
                searcher.active=False;searcher.speak=False;searcher.generation+=1
            await asyncio.get_running_loop().run_in_executor(None,monitor.load)
            with monitor.lock:
                if generation!=monitor.generation:return monitor.status()
                monitor.loading=False
                return monitor.start()
        except Exception as exc:
            monitor.error=str(exc)[:200]
            raise HTTPException(503,'安全帽模型未就绪：'+monitor.error) from exc
        finally:monitor.loading=False

    @app.post('/helmet/stop')
    async def stop():return monitor.stop()

    @app.get('/helmet/preview.jpg')
    async def preview():
        with monitor.lock:
            if not monitor.status()['last'] or not monitor.preview:
                raise HTTPException(503,'等待新的多人检测画面。')
            return Response(monitor.preview,media_type='image/jpeg',headers={'Cache-Control':'no-store'})

    @app.get('/helmet/records')
    async def records():return monitor.evidence.list()

    @app.get('/helmet/export.json')
    async def export():
        with monitor.evidence.lock:
            content={'schema_version':1,'exported_at':time.time(),'items':monitor.evidence.items,
                     'identity_semantics':'session_camera_track','review_required':True}
            return Response(json.dumps(content,ensure_ascii=False,indent=2),media_type='application/json',
                            headers={'Cache-Control':'no-store','Content-Disposition':'attachment; filename="helmet-records.json"'})

    @app.get('/helmet/records/{event_id}/{kind}.jpg')
    async def image(event_id:str,kind:str):
        try:return Response(monitor.evidence.image(event_id,kind),media_type='image/jpeg',headers={'Cache-Control':'no-store'})
        except FileNotFoundError as exc:raise HTTPException(404,'记录图片不存在。') from exc
