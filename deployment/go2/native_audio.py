#!/usr/bin/env python3
"""Go2 native microphone, speaker and offline Chinese synthesis.

Run with /home/unitree/go2move/.venv/bin/python. Mic audio stays local.
Uses the existing aiortc/av media stack and system libespeak-ng.
"""
import argparse
import asyncio
import ctypes
import ctypes.util
import json
import logging
import sys
import tempfile
import threading
import wave
from pathlib import Path

import av
import numpy as np

from native_webrtc import Go2MediaConnection
from neural_tts import VOICES, selected_voice, synthesize as synthesize_neural

_TTS_LOCK = threading.Lock()


def synthesize_text(text, path, voice='cmn+f4', rate=165, pitch=75):
    """Synthesize local Chinese speech, using a bright childlike default voice."""
    if not text.strip():
        raise ValueError('Speech text is empty')
    if len(text) > 1000:
        raise ValueError('Speech text exceeds 1000 characters')
    if not 0 <= int(pitch) <= 99:
        raise ValueError('Speech pitch must be between 0 and 99')
    library = ctypes.util.find_library('espeak-ng')
    if not library:
        raise RuntimeError('libespeak-ng is unavailable')
    with _TTS_LOCK:
        engine = ctypes.CDLL(library)
        engine.espeak_Initialize.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        engine.espeak_SetVoiceByName.argtypes = [ctypes.c_char_p]
        engine.espeak_SetParameter.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
        engine.espeak_Synth.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint,
                                       ctypes.c_int, ctypes.c_uint, ctypes.c_uint,
                                       ctypes.POINTER(ctypes.c_uint), ctypes.c_void_p]
        callback_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(ctypes.c_short), ctypes.c_int, ctypes.c_void_p)
        engine.espeak_SetSynthCallback.argtypes = [callback_type]
        sample_rate = engine.espeak_Initialize(2, 0, None, 0)  # synchronous retrieval
        if sample_rate <= 0:
            raise RuntimeError('Offline TTS initialization failed')
        pieces = []

        @callback_type
        def collect(samples, count, events):
            if count > 0 and samples:
                pieces.append(ctypes.string_at(samples, count * 2))
            return 0

        try:
            if engine.espeak_SetVoiceByName(voice.encode()) != 0:
                raise ValueError('Offline TTS voice unavailable: %s' % voice)
            engine.espeak_SetSynthCallback(collect)
            engine.espeak_SetParameter(1, int(rate), 0)  # espeakRATE
            engine.espeak_SetParameter(3, int(pitch), 0)  # espeakPITCH
            encoded = text.encode('utf-8') + b'\x00'
            buffer = ctypes.create_string_buffer(encoded)
            result = engine.espeak_Synth(buffer, len(encoded), 0, 1, 0, 1, None, None)
            if result != 0:
                raise RuntimeError('Offline TTS synthesis failed: %s' % result)
            engine.espeak_Synchronize()
        finally:
            engine.espeak_Terminate()
        if not pieces:
            raise RuntimeError('Offline TTS returned no samples')
        path = Path(path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), 'wb') as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            output.writeframes(b''.join(pieces))
        return {'path': str(path), 'sample_rate': sample_rate,
                'duration': sum(map(len, pieces)) / (2 * sample_rate),
                'voice': voice, 'rate': int(rate), 'pitch': int(pitch), 'engine': 'espeak-ng'}


class MicrophoneRecorder:
    """Decoded microphone capture, resampled to ASR-friendly 16 kHz mono."""
    def __init__(self, path, sample_rate=16000, max_seconds=None):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.output = wave.open(str(self.path), 'wb')
        self.output.setnchannels(1)
        self.output.setsampwidth(2)
        self.output.setframerate(sample_rate)
        self.resampler = av.AudioResampler(format='s16', layout='mono', rate=sample_rate)
        self.sample_rate = sample_rate
        self.max_samples = round(sample_rate * max_seconds) if max_seconds else None
        self.samples = 0
        self.frames = 0
        self.sum_squared = 0.0
        self.peak = 0
        self.frame_rms = []
        self.closed = False

    def accept(self, frame):
        if self.closed or (self.max_samples is not None and self.samples >= self.max_samples):
            return
        for converted in self.resampler.resample(frame):
            samples = converted.to_ndarray().reshape(-1).astype(np.int16)
            if self.max_samples is not None:
                samples = samples[:self.max_samples - self.samples]
            if not len(samples):
                continue
            self.output.writeframesraw(samples.tobytes())
            wide = samples.astype(np.float64)
            self.sum_squared += float(np.dot(wide, wide))
            self.peak = max(self.peak, int(np.max(np.abs(wide))))
            if len(self.frame_rms) < 1000:
                self.frame_rms.append(float(np.mean(wide * wide) ** .5))
            self.samples += len(samples)
            self.frames += 1

    def close(self):
        if not self.closed:
            self.output.close()
            self.closed = True
        quiet, loud = np.percentile(self.frame_rms, [10, 95]) if self.frame_rms else (0., 0.)
        variation = 20 * np.log10(max(loud, 1.) / max(quiet, 1.))
        return {'path': str(self.path), 'frames': self.frames, 'samples': self.samples,
                'sample_rate': self.sample_rate, 'duration': self.samples / self.sample_rate,
                'rms': (self.sum_squared / self.samples) ** 0.5 if self.samples else 0,
                'peak': self.peak, 'level_variation_db': round(float(variation), 2),
                'input_source': 'robot_body_microphone'}


async def speak(text, connection=None, ip='192.168.123.161'):
    """Speak via an existing shared connection, or one short standalone peer."""
    own_connection = connection is None
    connection = connection or Go2MediaConnection(ip)
    try:
        if own_connection:
            await connection.connect()
            connection.switch_video(False)
        with tempfile.TemporaryDirectory(prefix='go2-speech-') as directory:
            path = Path(directory) / 'speech.wav'
            voice_id = selected_voice()
            synthesized, fallback_reason = None, None
            if VOICES[voice_id]['engine'] == 'kokoro':
                neural_path = Path(directory) / 'neural.wav'
                try:
                    synthesized = await asyncio.wait_for(
                        asyncio.get_running_loop().run_in_executor(None, synthesize_neural, text, neural_path, voice_id),
                        timeout=40)
                except Exception as exc:
                    fallback_reason = type(exc).__name__ + ': ' + str(exc)[:200]
                    logging.warning('Neural speech failed; using offline backup voice: %s', fallback_reason)
            if synthesized is not None:
                delivered = await connection.play_audiohub(neural_path)
                return dict(synthesized, delivery=delivered)
            # libespeak-ng may deadlock on its second Initialize/Synchronize
            # cycle in one process. Keep each synthesis in a short-lived child.
            process = await asyncio.create_subprocess_exec(
                sys.executable, str(Path(__file__).resolve()), 'synth', text,
                '--output', str(path), '--legacy', stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError) as error:
                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=1)
                    except asyncio.TimeoutError:
                        process.kill()
                        await process.wait()
                if isinstance(error, asyncio.CancelledError):
                    raise
                raise RuntimeError('Offline TTS subprocess timed out after 10 seconds') from error
            if process.returncode != 0:
                detail = stdout.decode(errors='replace').strip() or stderr.decode(errors='replace').strip()
                raise RuntimeError('Offline TTS subprocess failed: ' + detail[-500:])
            synthesized = json.loads(stdout.decode())
            if fallback_reason:
                synthesized['fallback_reason'] = fallback_reason
            delivered = await connection.play_audiohub(path)
            return dict(synthesized, **{'delivery': delivered})
    finally:
        if own_connection:
            await connection.close()


async def record(path, seconds=6, connection=None, ip='192.168.123.161'):
    own_connection = connection is None
    connection = connection or Go2MediaConnection(ip)
    recorder = MicrophoneRecorder(path, max_seconds=seconds)
    connection.audio_callbacks.append(recorder.accept)
    try:
        if own_connection:
            await connection.connect()
            connection.switch_video(False)
        connection.switch_audio(True)
        await asyncio.sleep(seconds + 0.5)
        result = recorder.close()
        if not result['samples']:
            raise RuntimeError('Go2 connection succeeded but microphone returned no frames')
        return result
    finally:
        connection.audio_callbacks.remove(recorder.accept)
        recorder.close()
        if own_connection:
            await connection.close()


async def main(args):
    if args.command == 'synth':
        if not args.legacy and VOICES[selected_voice()]['engine'] == 'kokoro':
            return synthesize_neural(args.text, args.output)
        return synthesize_text(args.text, args.output)
    if args.command == 'record':
        return await record(args.output, args.seconds, ip=args.ip)
    if args.command == 'say':
        return await speak(args.text, ip=args.ip)
    if args.command == 'play':
        connection = Go2MediaConnection(args.ip)
        try:
            await connection.connect()
            connection.switch_video(False)
            return await connection.play_file(args.file)
        finally:
            await connection.close()
    if args.command == 'probe':
        connection = Go2MediaConnection(args.ip)
        try:
            await connection.connect()
            connection.switch_video(False)
            return {'connected': True, 'ip': args.ip, 'peer': connection.pc.connectionState,
                    'data_channel': connection.channel.readyState}
        finally:
            await connection.close()


if __name__ == '__main__':
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ip', default='192.168.123.161')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('probe')
    synth = commands.add_parser('synth')
    synth.add_argument('text')
    synth.add_argument('--output', required=True)
    synth.add_argument('--legacy', action='store_true', help='Use the original lightweight childlike voice')
    say = commands.add_parser('say')
    say.add_argument('text')
    play = commands.add_parser('play')
    play.add_argument('file')
    capture = commands.add_parser('record')
    capture.add_argument('--output', required=True)
    capture.add_argument('--seconds', type=float, default=6)
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(main(args)), ensure_ascii=False))
    except Exception as error:
        print(json.dumps({'ok': False, 'error': str(error), 'type': type(error).__name__}, ensure_ascii=False))
        raise SystemExit(1)
