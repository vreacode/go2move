"""Local Kokoro voices, bounded WAV caching, and a small reusable prompt bank."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import threading
import time
import wave

import numpy as np

BASE = Path(__file__).resolve().parent
RUNTIME = BASE / 'runtime' / 'tts'
MODEL = RUNTIME / 'kokoro-multi-lang-v1_1'
PACKAGES = RUNTIME / 'python'
SETTINGS = BASE / 'config' / 'tts_settings.json'
CACHE = RUNTIME / 'cache'
MODEL_SHA256 = 'a3f4c73d043860e3fd2e5b06f36795eb81de0fc8e8de6df703245edddd87dbad'
VOICES = {
    'kokoro_zf001': {'label': '中文女声 001', 'sid': 3, 'engine': 'kokoro'},
    'kokoro_zf002': {'label': '中文女声 002', 'sid': 4, 'engine': 'kokoro'},
    'kokoro_zf003': {'label': '中文女声 003', 'sid': 5, 'engine': 'kokoro'},
    'kokoro_zm009': {'label': '中文男声 009', 'sid': 58, 'engine': 'kokoro'},
    'kokoro_zm010': {'label': '中文男声 010', 'sid': 59, 'engine': 'kokoro'},
    'espeak_child': {'label': '原合成童声', 'engine': 'espeak'},
}
PROMPTS = {
    'hello': {'label': '打招呼', 'text': '你好，我是小机器狗。'},
    'ready': {'label': '准备就绪', 'text': '我准备好了。'},
    'uncertain': {'label': '没有听清', 'text': '没听清，请再说一次。'},
    'found': {'label': '发现提示', 'text': '发现疑似目标，请确认。'},
}
_MODEL_LOCK = threading.Lock()
_SETTINGS_LOCK = threading.Lock()
_tts = None


def model_available():
    return all((MODEL / name).is_file() for name in
               ('model.onnx', 'voices.bin', 'tokens.txt', 'lexicon-zh.txt',
                'number-zh.fst', 'date-zh.fst', 'phone-zh.fst')) \
        and (MODEL / 'espeak-ng-data').is_dir() \
        and (PACKAGES / 'sherpa_onnx').is_dir() \
        and (PACKAGES / 'sherpa_onnx' / 'lib').is_dir()


def selected_voice():
    try:
        value = json.loads(SETTINGS.read_text()).get('voice_id', 'espeak_child')
    except (OSError, ValueError, AttributeError):
        value = 'espeak_child'
    return value if isinstance(value, str) and value in VOICES else 'espeak_child'


def voice_status():
    voice_id = selected_voice()
    available = model_available()
    return {'voice_id': voice_id, 'voice_label': VOICES[voice_id]['label'],
            'engine': VOICES[voice_id]['engine'], 'model_available': available,
            'model': 'Kokoro v1.1 Chinese FP32', 'chinese_voice_count': 100,
            'voices': [{'id': key, 'label': value['label'],
                        'available': available or value['engine'] == 'espeak'}
                       for key, value in VOICES.items()]}


def select_voice(voice_id):
    if voice_id not in VOICES:
        raise ValueError('不支持这个音色。')
    if VOICES[voice_id]['engine'] == 'kokoro' and not model_available():
        raise ValueError('成熟中文音色包尚未就绪。')
    with _SETTINGS_LOCK:
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        temporary = SETTINGS.with_suffix('.tmp.json')
        temporary.write_text(json.dumps({'voice_id': voice_id}, ensure_ascii=False, indent=2) + '\n')
        temporary.replace(SETTINGS)
    return voice_status()


def _load_model():
    global _tts
    if _tts is None:
        if not model_available():
            raise RuntimeError('Chinese neural voice resources are not installed')
        if str(PACKAGES) not in sys.path:
            sys.path.insert(0, str(PACKAGES))
        import sherpa_onnx
        config = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                kokoro=sherpa_onnx.OfflineTtsKokoroModelConfig(
                    model=str(MODEL / 'model.onnx'), voices=str(MODEL / 'voices.bin'),
                    tokens=str(MODEL / 'tokens.txt'), data_dir=str(MODEL / 'espeak-ng-data'),
                    lexicon=str(MODEL / 'lexicon-zh.txt'), lang='zh'),
                num_threads=4, provider='cpu'),
            rule_fsts=','.join(str(MODEL / name) for name in
                              ('number-zh.fst', 'date-zh.fst', 'phone-zh.fst')))
        if not config.validate():
            raise RuntimeError('Invalid Chinese neural TTS model configuration')
        _tts = sherpa_onnx.OfflineTts(config)
    return _tts


def prime():
    if VOICES[selected_voice()]['engine'] == 'kokoro':
        with _MODEL_LOCK:
            _load_model()


def _wav_info(path):
    with wave.open(str(path), 'rb') as source:
        if source.getnchannels() != 1 or source.getsampwidth() != 2:
            raise ValueError('Expected mono 16-bit PCM WAV')
        sample_rate, samples = source.getframerate(), source.getnframes()
        if not samples or sample_rate != 24000:
            raise ValueError('Invalid neural speech WAV')
    return {'sample_rate': sample_rate, 'duration': samples / sample_rate}


def synthesize(text, output, voice_id=None):
    voice_id = voice_id or selected_voice()
    if voice_id not in VOICES or VOICES[voice_id]['engine'] != 'kokoro':
        raise ValueError('Expected a supported Kokoro voice')
    if not isinstance(text, str) or not text.strip() or len(text) > 1000:
        raise ValueError('Speech text must contain 1 to 1000 characters')
    key = hashlib.sha256(json.dumps([MODEL_SHA256, voice_id, text], ensure_ascii=False).encode()).hexdigest()
    cached = CACHE / (key + '.wav')
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with _MODEL_LOCK:
        hit = cached.is_file()
        if hit:
            try:
                info = _wav_info(cached)
            except (OSError, ValueError, EOFError, wave.Error):
                hit = False
        if not hit:
            audio = _load_model().generate(text=text, sid=VOICES[voice_id]['sid'], speed=1.0)
            samples = np.asarray(audio.samples, dtype=np.float32)
            if samples.ndim != 1 or not len(samples) or not np.all(np.isfinite(samples)):
                raise RuntimeError('Neural TTS returned invalid audio samples')
            peak = float(np.max(np.abs(samples)))
            if peak <= 0:
                raise RuntimeError('Neural TTS returned silent audio')
            if peak > .95:
                samples = samples * (.95 / peak)
            CACHE.mkdir(parents=True, exist_ok=True)
            temporary = cached.with_suffix('.tmp.wav')
            with wave.open(str(temporary), 'wb') as target:
                target.setnchannels(1)
                target.setsampwidth(2)
                target.setframerate(audio.sample_rate)
                target.writeframes((samples * 32767).astype('<i2').tobytes())
            info = _wav_info(temporary)
            temporary.replace(cached)
            # Only remove this module's oldest content-addressed cache files.
            for old in sorted(CACHE.glob('*.wav'), key=lambda path: path.stat().st_mtime)[:-128]:
                if len(old.stem) == 64 and old != cached:
                    old.unlink()
        if output != cached.resolve():
            shutil.copyfile(cached, output)
    return {'path': str(output), **info, 'voice': voice_id, 'voice_label': VOICES[voice_id]['label'],
            'engine': 'kokoro', 'offline': True, 'cached': hit,
            'synthesis_seconds': round(time.monotonic() - started, 3)}


def prepare_bank(voice_id=None):
    voice_id = voice_id or selected_voice()
    if voice_id not in VOICES or VOICES[voice_id]['engine'] != 'kokoro':
        raise ValueError('请选择成熟中文音色后再导入提示音。')
    folder = RUNTIME / 'voice_banks' / voice_id
    folder.mkdir(parents=True, exist_ok=True)
    clips = []
    for clip_id, prompt in PROMPTS.items():
        output = folder / (clip_id + '.wav')
        speech = synthesize(prompt['text'], output, voice_id)
        checksum = hashlib.sha256(output.read_bytes()).hexdigest()
        clips.append({'id': clip_id, **prompt, **speech, 'sha256': checksum,
                      'robot_name': 'go2_voice_pack_' + voice_id[len('kokoro_'):] + '_' + clip_id + '_' + checksum[:8]})
    manifest = {'voice_id': voice_id, 'voice_label': VOICES[voice_id]['label'], 'clips': clips}
    (folder / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    return manifest
