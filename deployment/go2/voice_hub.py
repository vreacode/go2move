#!/usr/bin/env python3
"""Local Go2 camera/voice assistant; upstream media and offline ASR only.

Expose through the existing authenticated Dashboard, never directly to LAN.
Nonzero movement/posture/navigation commands have no execution path.
"""
import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import uuid

import av
import numpy as np
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from native_audio import record, speak
from native_webrtc import Go2MediaConnection, _rpc_data
from audiohub_vendor.audiohub import WebRTCAudioHub
from media_policy import voice_policy
from neural_tts import prepare_bank, prime as prime_tts, select_voice, voice_status

BASE = Path(__file__).resolve().parent
RUNTIME = BASE / 'runtime' / 'voice'
RUNTIME.mkdir(parents=True, exist_ok=True)
ASR_PYTHON = '/home/unitree/go2_voice_venv/bin/python'
MODEL = Path('/home/unitree/go2_ros2_ws/models/vosk-model-small-cn-0.22')
PHRASES = ['拍照', '截图', '报告状态', '查看状态', '开始录像', '停止录像',
           '开始建图', '停止建图', '保存地图', '帮助', '停止', '急停',
           '前进', '后退', '左转', '右转', '站立', '趴下', '导航',
           '找人', '开始找人', '停止找人', '寻找目标', '找这个人', '找人状态']

class TextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=160)
    speak: bool = True

class ListenRequest(BaseModel):
    seconds: float = Field(default=5., ge=2., le=10.)
    execute: bool = True
    speak: bool = True

class VoiceSelection(BaseModel):
    voice_id: str = Field(min_length=1, max_length=64)

class _FeedbackLevels:
    """Only aggregate PCM levels in memory; no microphone audio is retained."""
    def __init__(self, connection):
        self.connection = connection
        self.resampler = av.AudioResampler(format='s16', layout='mono', rate=16000)
        self.enabled = False
        self.error = None
        self.levels = {name: {'samples': 0, 'frames': 0, 'sum_squared': 0., 'peak': 0}
                       for name in ('baseline', 'playback')}

    def accept(self, frame):
        if not self.enabled:
            return
        try:
            track = getattr(self.connection, '_speaker_track', None)
            phase = 'playback' if getattr(self.connection, 'audiohub_playing', False) or (track is not None and len(track.samples)) else 'baseline'
            level = self.levels[phase]
            for converted in self.resampler.resample(frame):
                samples = converted.to_ndarray().reshape(-1).astype(np.float64)
                if not len(samples):
                    continue
                level['samples'] += len(samples)
                level['frames'] += 1
                level['sum_squared'] += float(np.dot(samples, samples))
                level['peak'] = max(level['peak'], int(np.max(np.abs(samples))))
        except Exception as exc:
            # Diagnostics must never interrupt the shared microphone receiver.
            self.error = type(exc).__name__ + ': ' + str(exc)[:180]

    def summary(self):
        result = {'sample_rate': 16000, 'pcm_bits': 16, 'audio_saved': False,
                  'audibility_verified': False, 'error': self.error}
        for phase, level in self.levels.items():
            result[phase] = {key: level[key] for key in ('samples', 'frames', 'peak')}
            result[phase]['rms'] = round((level['sum_squared'] / level['samples']) ** .5, 2) if level['samples'] else 0.
            result[phase]['duration'] = round(level['samples'] / 16000, 3)
        return result

class VoiceHub:
    def __init__(self):
        self.connection = None
        self.connect_lock = asyncio.Lock()
        self.audio_lock = asyncio.Lock()
        self.listen_lock = asyncio.Lock()
        self.command_lock = asyncio.Lock()
        self.error = None
        self.last = None
        self.last_listen = None
        self.capture = {'phase': 'idle'}
        self.last_speech = None
        try:
            restored = json.loads((RUNTIME/'last_speech.json').read_text())
            if isinstance(restored, dict):
                self.last_speech = restored
        except (OSError, ValueError):
            pass
        try:
            restored = json.loads((RUNTIME/'last_listen.json').read_text())
            if isinstance(restored, dict):
                self.last_listen = restored
        except (OSError, ValueError):
            pass
        self.connect_task = None

    async def ensure_audio(self):
        policy = voice_policy()
        if not policy['enabled']:
            raise RuntimeError(policy['reason'])
        async with self.connect_lock:
            if self.connection and self.connection.pc and self.connection.pc.connectionState == 'connected':
                return self.connection
            if self.connection:
                with suppress(Exception):
                    await self.connection.close()
            connection = Go2MediaConnection('192.168.123.161')
            try:
                await connection.connect(timeout=25)
                connection.switch_video(False)
                connection.switch_audio(False)
                self.connection = connection
                self.error = None
                return connection
            except Exception as exc:
                self.connection = None
                self.error = '%s: %s' % (type(exc).__name__, str(exc))
                raise RuntimeError('原生音频连接失败：' + self.error) from exc

    async def prime(self):
        with suppress(Exception):
            await self.ensure_audio()
        with suppress(Exception):
            await asyncio.get_running_loop().run_in_executor(None, prime_tts)

    async def import_voice_pack(self):
        policy = voice_policy()
        if not policy['enabled']:
            raise HTTPException(status_code=409, detail=policy['reason'])
        try:
            bank = await asyncio.get_running_loop().run_in_executor(None, prepare_bank)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        records = []
        async with self.audio_lock:
            connection = await self.ensure_audio()
            audiohub = WebRTCAudioHub(connection)
            listed = _rpc_data(await audiohub.get_audio_list())
            existing = {item.get('CUSTOM_NAME'): item for item in listed.get('audio_list', [])}
            with tempfile.TemporaryDirectory(prefix='go2-voice-pack-') as directory:
                for clip in bank['clips']:
                    name = clip['robot_name']
                    item = existing.get(name)
                    reused = item is not None
                    if item is None:
                        uploaded = Path(directory) / (name + '.wav')
                        shutil.copyfile(clip['path'], uploaded)
                        await audiohub.upload_audio_file(str(uploaded))
                        for _ in range(3):
                            listed = _rpc_data(await audiohub.get_audio_list())
                            item = next((value for value in listed.get('audio_list', [])
                                         if value.get('CUSTOM_NAME') == name), None)
                            if item:
                                break
                            await asyncio.sleep(.2)
                    if not item or not item.get('UNIQUE_ID'):
                        raise RuntimeError('机器狗没有确认导入提示音：' + clip['label'])
                    # Imported prompts persist in the robot library; no playback or deletion here.
                    records.append({'id': clip['id'], 'label': clip['label'], 'text': clip['text'],
                                    'robot_name': name, 'unique_id': item['UNIQUE_ID'],
                                    'sha256': clip['sha256'], 'reused': reused})
        result = {'voice_id': bank['voice_id'], 'voice_label': bank['voice_label'],
                  'clips': records, 'imported_count': len(records), 'played': False}
        (RUNTIME / 'imported_voice_pack.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        return result

    async def feedback(self, text):
        async with self.audio_lock:
            started_at = time.time()
            connection, levels = None, None
            cancelled = False
            try:
                connection = await self.ensure_audio()
                levels = _FeedbackLevels(connection)
                connection.audio_callbacks.append(levels.accept)
                connection.switch_audio(True)
                await asyncio.sleep(.2)  # Allow the existing microphone channel to resume.
                levels.enabled = True
                await asyncio.sleep(.35)  # Quiet pre-playback baseline; no extra sound.
                speech = await speak(text, connection=connection)
                delivered = speech['delivery'].get('acknowledged') is True
                result = {'delivered': delivered, **speech}
                self.error = None if delivered else '机器狗没有确认音频播放请求。'
                if self.error:
                    result['error'] = self.error
            except asyncio.CancelledError:
                cancelled = True
                self.error = 'CancelledError: speech interrupted'
                result = {'delivered': False, 'error': self.error}
            except Exception as exc:
                self.error = '%s: %s' % (type(exc).__name__, str(exc))
                result = {'delivered': False, 'error': self.error}
            finally:
                if connection:
                    if levels and levels.accept in connection.audio_callbacks:
                        connection.audio_callbacks.remove(levels.accept)
                    with suppress(Exception):
                        connection.switch_audio(False)
            result.update(text=text, started_at=started_at, completed_at=time.time(),
                          verification='robot_audiohub_ack', audibility_verified=False)
            if levels:
                result['microphone_levels'] = levels.summary()
            self.last_speech = result
            try:
                temporary = RUNTIME/'last_speech.tmp.json'
                temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
                temporary.replace(RUNTIME/'last_speech.json')
            except OSError as exc:
                result['persistence_error'] = str(exc)[:180]
            if cancelled:
                raise asyncio.CancelledError()
            return result

    async def agent(self, path, body=None):
        token = os.getenv('AGENT_TOKEN', '')
        if not token:
            raise RuntimeError('Agent token is not configured')
        async with httpx.AsyncClient(trust_env=False, timeout=15) as client:
            response = await client.post('http://127.0.0.1:9001' + path,
                                         headers={'Authorization': 'Bearer ' + token}, json=body or {})
            response.raise_for_status()
            return response.json()

    async def person(self, method, path, body=None):
        async with httpx.AsyncClient(trust_env=False, timeout=20) as client:
            response = await client.request(method, 'http://127.0.0.1:8768' + path, json=body)
            if response.status_code >= 400:
                raise RuntimeError(response.json().get('detail', '找人服务执行失败。'))
            return response.json()

    async def run_process(self, *args, timeout=35, env=None):
        process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                       stderr=asyncio.subprocess.PIPE, env=env)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            process.terminate()
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(process.wait(), 3)
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise
        if process.returncode:
            raise RuntimeError((stderr or stdout).decode(errors='replace')[-1200:])
        return stdout.decode()

    async def action(self, text):
        normalized = re.sub(r'[\s，。！？,.!?]', '', text)
        if normalized.startswith('助手'):
            normalized = normalized[2:]
        result = {'text': text, 'intent': 'unknown', 'executed': False}
        area = re.search(r'(?:去|到)([A-Za-z0-9一二三四五六七八九十]{1,8})区.*?(?:找|寻找)', normalized)
        if area:
            return dict(result, intent='area_search_request', area=area.group(1)+'区',
                        requires_onsite_confirmation=True, blocked_by='navigation_not_verified',
                        reply='已识别区域找人任务，需先完成导航验证和现场授权；当前可以原地找人。')
        # Any motion request stays a request; neither old authorization nor speech arms motors.
        if normalized not in ['停止导航', '停下', '别动'] and any(word in normalized for word in ['前进', '后退', '左转', '右转', '导航', '站立', '趴下', '坐下', '行走', '跳跃', '跑步', '探索', '巡检', '跟随', '转向']):
            return dict(result, intent='motion_request', requires_onsite_confirmation=True,
                        reply='已识别运动请求，需要现场确认后再执行。')
        if normalized in ['拍照', '截图']:
            data = await self.agent('/media/snapshot', {'stream_mode': 'visible_raw'})
            result.update(intent='snapshot', executed=True, reply='照片已保存。', result=data)
        elif normalized in ['开始录像', '录像']:
            data = await self.agent('/media/recording/start', {'stream_mode': 'visible_raw'})
            result.update(intent='record_start', executed=True, reply='开始录像。', result=data)
        elif normalized in ['停止录像', '结束录像']:
            data = await self.agent('/media/recording/stop')
            result.update(intent='record_stop', executed=True, reply='录像已保存。', result=data)
        elif normalized in ['报告状态', '查看状态', '状态']:
            data = self.status()
            reply = ('机身摄像头%s，原生语音%s。运动控制尚未授权。' %
                     ('在线' if data['camera']['connected'] else '离线',
                      '在线' if data['audio']['connected'] else '离线'))
            result.update(intent='status', executed=True, reply=reply, result=data)
        elif normalized in ['帮助', '你会什么']:
            result.update(intent='help', executed=True,
                          reply='可以拍照，录像，报告状态，原地找人，开始建图，保存地图，以及停止。', result={'commands': PHRASES})
        elif normalized in ['找人', '开始找人', '寻找目标', '寻找这个人', '找这个人']:
            data = await self.person('POST', '/start', {'mode':'stationary', 'speak':True})
            result.update(intent='person_search_start', executed=True,
                          reply='开始原地观察，发现疑似目标后会提示。', result=data)
        elif normalized == '停止找人':
            data = await self.person('POST', '/stop')
            result.update(intent='person_search_stop', executed=True, reply='已停止找人。', result=data)
        elif normalized == '找人状态':
            data = await self.person('GET', '/status')
            last = data.get('last') or {}
            reply = '发现疑似目标，请现场确认。' if last.get('suspected_target') else ('正在观察，尚未确认候选。' if data.get('active') else '当前没有正在执行的找人任务。')
            result.update(intent='person_search_status', executed=True, reply=reply, result=data)
        elif normalized == '开始建图':
            await self.run_process('systemctl', '--user', 'start', 'go2-platform.service', timeout=12)
            result.update(intent='mapping_start', executed=True, reply='已启动雷达建图，机器狗保持不动。')
        elif normalized == '停止建图':
            # Require an upstream map/graph checkpoint before shutting down SLAM.
            saved = await self.save_map()
            await self.run_process('systemctl', '--user', 'stop', 'go2-platform.service', timeout=20)
            result.update(intent='mapping_stop', executed=True, reply='地图已保存，建图已停止。', result=saved)
        elif normalized == '保存地图':
            data = await self.save_map()
            result.update(intent='map_save', executed=True, reply='地图和位姿图已保存。', result=data)
        elif normalized in ['停止', '急停', '停止导航', '停下', '别动']:
            with suppress(Exception):
                await self.person('POST', '/stop')
            env = os.environ.copy()
            env['CYCLONEDDS_HOME'] = '/home/unitree/cyclonedds_ws/install/cyclonedds'
            env['LD_LIBRARY_PATH'] = env['CYCLONEDDS_HOME'] + '/lib' + (':' + env['LD_LIBRARY_PATH'] if env.get('LD_LIBRARY_PATH') else '')
            stdout = await self.run_process('/home/unitree/go2_sdk2_venv/bin/python', str(BASE / 'native_stop.py'), timeout=10, env=env)
            data = json.loads(stdout)
            result.update(intent='stop', executed=data['stopped'], reply='停止指令已发送。', result=data)
        else:
            result['reply'] = '没有匹配的指令。可以说拍照、报告状态或者帮助。'
        return result

    async def save_map(self):
        folder = '/home/unitree/go2_ros2_ws/maps/voice-' + datetime.now().strftime('%Y%m%d-%H%M%S')
        # The command text is constant except an internally generated timestamp.
        command = 'source /home/unitree/go2move/deployment/go2/source_go2_platform.sh\nexport CYCLONEDDS_URI=/home/unitree/go2move/ros2/go2_bringup/config/dds/test.xml\nexec python3 /home/unitree/go2move/deployment/go2/save_platform_map.py --directory ' + folder
        stdout = await self.run_process('/bin/bash', '-c', command, timeout=35)
        return json.loads(stdout)

    def status(self):
        path = BASE / 'runtime/native_camera/status.json'
        try:
            camera = json.loads(path.read_text())
            camera['connected'] = bool(camera.get('running') and time.time() - camera.get('captured_at', 0) < 2)
        except (OSError, ValueError):
            camera = {'connected': False}
        connection = self.connection
        audio = {'connected': bool(connection and connection.pc and connection.pc.connectionState == 'connected'),
                 'mic_frames': connection.audio_frames if connection else 0, 'last_error': self.error,
                 'receiving': bool(connection and connection.audio_enabled), 'input_source': 'robot_body_microphone'}
        return {'voice_policy':voice_policy(), 'camera': camera, 'audio': audio, 'asr': {'model_available': (MODEL/'am/final.mdl').exists(),
                'offline': True}, 'tts': voice_status(), 'motion_enabled': False,
                'last': self.last, 'last_speech': self.last_speech,
                'last_listen': self.last_listen, 'capture': self.capture, 'commands': PHRASES}

    async def command(self, payload):
        async with self.command_lock:
            try:
                response = await self.action(payload.text)
            except Exception as exc:
                response = {'text': payload.text, 'intent': 'error', 'executed': False,
                            'reply': '执行失败：' + str(exc)[:180]}
            if payload.speak:
                response['speech'] = await self.feedback(response['reply'])
            self.last = response
            (RUNTIME/'last.json').write_text(json.dumps(response, ensure_ascii=False, indent=2)+'\n')
            return response

hub = VoiceHub()

@asynccontextmanager
async def lifespan(app):
    hub.connect_task = asyncio.create_task(hub.prime()) if voice_policy()['enabled'] else None
    yield
    if hub.connect_task:
        hub.connect_task.cancel()
        with suppress(asyncio.CancelledError):
            await hub.connect_task
    if hub.connection:
        with suppress(Exception):
            await hub.connection.close()

app = FastAPI(title='Go2 local voice assistant', lifespan=lifespan)

@app.get('/status')
async def status():
    return hub.status()

@app.get('/audio/device')
async def audio_device():
    if not voice_policy()['enabled']:
        raise HTTPException(status_code=409, detail=voice_policy()['reason'])
    connection = hub.connection
    if not connection or not connection.pc or connection.pc.connectionState != 'connected':
        raise HTTPException(status_code=503, detail='Existing shared audio connection is unavailable')
    async with hub.audio_lock:
        return await connection.audio_device()

@app.post('/say')
async def say(payload: TextRequest):
    return await hub.feedback(payload.text)

@app.post('/select')
async def choose_voice(payload: VoiceSelection):
    async with hub.audio_lock:
        try:
            return select_voice(payload.voice_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

@app.post('/pack/import')
async def import_voice_pack():
    try:
        return await hub.import_voice_pack()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail='提示音导入失败：' + str(exc)[:180]) from exc

@app.post('/command')
async def command(payload: TextRequest):
    if not voice_policy()['enabled']:
        raise HTTPException(status_code=409, detail=voice_policy()['reason'])
    return await hub.command(payload)

@app.post('/listen')
async def listen(payload: ListenRequest):
    if not voice_policy()['enabled']:
        raise HTTPException(status_code=409, detail=voice_policy()['reason'])
    if hub.listen_lock.locked():
        raise HTTPException(status_code=409, detail='正在收听或识别上一段录音，请稍后重试。')
    async with hub.listen_lock:
        return await _listen_once(payload)


async def _listen_once(payload):
    try:
        async with hub.audio_lock:
            hub.capture = {'phase': 'connecting'}
            connection = await hub.ensure_audio()
            path = RUNTIME/('mic-'+str(time.time_ns())+'-'+uuid.uuid4().hex[:6]+'.wav')
            try:
                hub.capture = {'phase': 'recording', 'started_at': time.time(), 'seconds': payload.seconds}
                recorded = await record(path, seconds=payload.seconds, connection=connection)
            finally:
                with suppress(Exception):
                    connection.switch_audio(False)
        hub.capture = {'phase': 'recognizing'}
        argv = [ASR_PYTHON, str(BASE/'offline_voice_asr.py'), str(path), '--max-seconds', '12']
        # The web button only transcribes for manual review, so ordinary sentences
        # must not be forced into the fixed command vocabulary.
        if payload.execute:
            for phrase in PHRASES:
                argv += ['--phrase', phrase]
        recognition = json.loads(await hub.run_process(*argv, timeout=25))
        text = recognition.get('text', '')
        # Unknown or uncertain speech never triggers an action.
        if not text or '[unk]' in text or recognition.get('confidence', 0) < .65:
            response = {'text': text, 'intent': 'uncertain', 'executed': False, 'reply': '没有听清，请再说一次。'}
            if payload.speak:
                response['speech'] = await hub.feedback(response['reply'])
        elif payload.execute:
            response = await hub.command(TextRequest(text=text, speak=payload.speak))
        else:
            response = {'text': text, 'intent': 'transcription', 'executed': False, 'reply': text}
        response.update(recognition=recognition, recording=recorded)
        if response.get('intent') == 'uncertain':
            if recorded.get('rms', 0) < 60:
                hint = '收到的声音较小，请靠近机器狗的机身麦克风，正常说话后重试。'
            elif recorded.get('level_variation_db', 100) < 4:
                hint = '收到的声音变化较小，人声可能被背景声盖住；请靠近机器狗、降低周围噪声后重试。'
            else:
                hint = '人声未能可靠转写，请靠近机器狗，说一句简短的普通话后重试。'
            response['listen_hint'] = hint
        hub.last_listen = {'completed_at': time.time(), 'intent': response.get('intent'),
                           'executed': response.get('executed', False), 'recognition': recognition,
                           'recording': recorded, 'listen_hint': response.get('listen_hint')}
        with suppress(OSError):
            temporary = RUNTIME/'last_listen.tmp.json'
            temporary.write_text(json.dumps(hub.last_listen, ensure_ascii=False, indent=2)+'\n')
            temporary.replace(RUNTIME/'last_listen.json')
        return response
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)[:300]) from exc
    finally:
        hub.capture = {'phase': 'idle'}
