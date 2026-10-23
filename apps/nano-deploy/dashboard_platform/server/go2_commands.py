"""Small allowlisted search commands and NX-local ASR; no robot SDK imports."""
import asyncio
import base64
import binascii
import io
import json
from pathlib import Path
import re
import wave

from fastapi import HTTPException

DEPLOYMENT = Path(__file__).resolve().parents[4] / 'deployment/go2'
ASR_PYTHON = '/home/unitree/go2_voice_venv/bin/python'
MAX_AUDIO_BYTES = 12 * 16000 * 2 + 65536
_asr_lock = asyncio.Lock()


def parse_command(text):
    text = re.sub(r'[\s，。！？,.!?]', '', text).strip()
    if not text or len(text) > 160:
        return {'intent': 'unsupported', 'reply': '请输入简短的找人指令。'}
    for prefix in ('请帮我', '帮我', '请', '助手'):
        if text.startswith(prefix):
            text = text[len(prefix):]
    if text in ('急停', '停下', '别动'):
        return {'intent': 'stop', 'reply': '已停止视觉找人任务。机身急停请使用手柄。'}
    if text in ('停止', '停止找人', '停止查找', '结束找人', '取消找人'):
        return {'intent': 'stop', 'reply': '已停止找人任务。'}
    if text in ('拍照', '记住这个人', '拍这个人', '拍照记人'):
        return {'intent': 'capture', 'reply': '请在拍摄画面中选定目标人物。'}
    if text in ('开始找人', '开始查找', '找人', '寻找目标', '找这个人', '寻找这个人'):
        return {'intent': 'start', 'reply': '开始原地找人，最多观察两分钟。'}
    if text in ('状态', '报告状态', '查看状态', '找人状态'):
        return {'intent': 'status', 'reply': ''}
    if text in ('帮助', '你会什么', '支持什么'):
        return {'intent': 'help', 'reply': '可以说：拍照、开始找人、停止找人、找人状态，或找穿红色上衣的人。'}
    # A language request can never arm a movement service, even after cooling.
    if any(word in text for word in ('前进', '后退', '左转', '右转', '转圈', '旋转', '导航', '站', '趴', '走', '探索', '巡航', '巡检', '跟随', '追踪', '到', '去', '区')):
        return {'intent': 'motion_blocked', 'reply': '移动、扫描和区域巡航仍暂停；可以拍照找人或在当前画面按上衣颜色查找。'}
    if text.startswith(('找', '寻找', '搜寻')):
        return {'intent': 'description', 'description': text, 'reply': '开始按上衣颜色查找候选人，最多观察两分钟。'}
    return {'intent': 'unsupported', 'reply': '暂不支持这句话。可说“开始找人”“停止找人”或“找穿红色上衣的人”。'}


async def execute_command(text, person_request):
    """person_request is the existing authenticated loopback proxy adapter."""
    result = dict(parse_command(text), text=text, executed=False, motion_enabled=False)
    intent = result['intent']
    if intent == 'start':
        result['result'] = await person_request('POST', '/start', {'mode': 'stationary', 'speak': False, 'timeout_seconds': 120})
    elif intent == 'stop':
        result['result'] = await person_request('POST', '/stop', None)
    elif intent == 'description':
        await person_request('POST', '/description', {'text': result['description']})
        result['result'] = await person_request('POST', '/start', {'mode': 'stationary', 'speak': False, 'timeout_seconds': 120})
    elif intent == 'status':
        status = await person_request('GET', '/status', None)
        result['result'] = status
        task = status.get('search_task') or {}
        labels = {'running': '正在查找', 'found_pending_confirmation': '发现候选，等待人工确认',
                  'completed': '目标已由人工确认', 'timed_out': '观察时间已到',
                  'interrupted': '上次任务中断', 'stopped': '找人已停止'}
        result['reply'] = labels.get(task.get('state'), '正在查找' if status.get('active') else '当前没有运行中的找人任务') + '。'
    else:
        return result
    result['executed'] = True
    return result


def decode_audio(encoded):
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(400, '录音编码无效。') from exc
    if len(payload) > MAX_AUDIO_BYTES:
        raise HTTPException(413, '录音最长12秒。')
    try:
        with wave.open(io.BytesIO(payload), 'rb') as recording:
            frames = recording.getnframes()
            if (recording.getcomptype() != 'NONE' or recording.getframerate() != 16000
                    or recording.getnchannels() != 1 or recording.getsampwidth() != 2
                    or not 1600 <= frames <= 12 * 16000):
                raise ValueError('format')
            if len(recording.readframes(frames)) != frames * 2:
                raise ValueError('truncated')
    except (wave.Error, EOFError, ValueError) as exc:
        raise HTTPException(400, '需要0.1至12秒、16kHz单声道16位PCM WAV录音。') from exc
    return payload


async def transcribe_audio(encoded):
    payload = decode_audio(encoded)
    if _asr_lock.locked():
        raise HTTPException(409, '正在识别上一段录音，请稍后重试。')
    async with _asr_lock:
        process = None
        try:
            process = await asyncio.create_subprocess_exec(ASR_PYTHON,
                str(DEPLOYMENT / 'offline_voice_asr.py'), '--format', 'wav', '--max-seconds', '12',
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, _stderr = await asyncio.wait_for(process.communicate(payload), 35)
            if process.returncode:
                raise HTTPException(503, '本地语音识别暂不可用，请直接输入指令。')
            result = json.loads(stdout.decode())
            return {'text': result.get('text', ''), 'confidence': result.get('confidence', 0),
                    'engine': 'vosk', 'offline': True, 'executed': False,
                    'reply': '请检查识别文字后点击发送。' if result.get('text') else '未识别到清晰语音，请重试或输入文字。'}
        except (OSError, ValueError, asyncio.TimeoutError) as exc:
            raise HTTPException(503, '语音识别未完成，请重试或输入文字。') from exc
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
