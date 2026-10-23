"""Go2 LAN media only: no movement, DDS control, cloud or firmware changes.

Local signaling/validation adapted from legion1581/unitree_webrtc_connect
commit e0abc5780761539eff89a382a79319e5cf6ad1f4 (MIT).
https://github.com/legion1581/unitree_webrtc_connect

MIT License — Copyright (c) 2025 Konstantin Severov
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:
The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
import asyncio
import base64
import hashlib
import inspect
import json
import logging
import os
import re
import socket
import shutil
import tempfile
import uuid
import wave
from types import SimpleNamespace
import time
from pathlib import Path


# Upstream __init__.py patch: Go2 bundles transports but expects one ICE credential pair.
import aioice
class _UnitreeICEConnection(aioice.Connection):
    local_username = aioice.utils.random_string(4)
    local_password = aioice.utils.random_string(22)
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.local_username = _UnitreeICEConnection.local_username
        self.local_password = _UnitreeICEConnection.local_password
aioice.Connection = _UnitreeICEConnection
from aiortc import RTCPeerConnection, RTCConfiguration, RTCSessionDescription
from aiortc import AudioStreamTrack
from fractions import Fraction
import av
import numpy as np
from audiohub_vendor.pub_sub import WebRTCDataChannelPubSub
from audiohub_vendor.audiohub import WebRTCAudioHub
from aiortc.mediastreams import MediaStreamError


class Go2MediaError(RuntimeError):
    pass


class _MediaPubSub(WebRTCDataChannelPubSub):
    """Original request/response resolver, restricted to VUI and audiohub."""
    allowed = {'rt/api/vui/request': {1002, 1004},
               'rt/api/audiohub/request': {1001, 1002, 1009, 1010, 2001}}

    def __init__(self, channel, owner):
        super().__init__(channel)
        self.owner = owner
        self.last_ack = None
        self.sequence = 0

    async def publish_request_new(self, topic, options=None):
        options = dict(options or {})
        api = options.get('api_id')
        if api not in self.allowed.get(topic, set()):
            raise Go2MediaError('Only VUI read queries and audiohub playback requests are allowed')
        parameter = options.get('parameter', {})
        parameter = json.loads(parameter) if isinstance(parameter, str) else parameter
        if api == 1009 and parameter.get('unique_id') not in self.owner._owned_audio_ids:
            raise Go2MediaError('Only this process-created audio files may be removed')
        if topic == 'rt/api/audiohub/request' and api == 2001:
            filename = parameter.get('file_name', '')
            prompt_name = re.fullmatch(r'go2_voice_pack_z[fm][0-9]{3}_(hello|ready|uncertain|found)_[0-9a-f]{8}', filename)
            if not filename.startswith('go2_feedback_') and not prompt_name:
                raise Go2MediaError('Audio uploads must use a generated feedback or prompt-pack filename')
        self.sequence += 1
        request_id = int(time.time() * 1000) % 2147483648 + self.sequence
        options['id'] = request_id
        try:
            response = await asyncio.wait_for(super().publish_request_new(topic, options), timeout=6)
            code = response.get('data', {}).get('header', {}).get('status', {}).get('code')
            self.last_ack = {'api_id': api, 'request_id': request_id, 'code': code,
                             'received_at': time.time()}
            if code != 0:
                raise Go2MediaError('%s API %s returned status %s' % (topic, api, code))
            return response
        finally:
            # Prevent a late reply from resolving a cancelled/expired future.
            self.future_resolver.pending_callbacks.pop(request_id, None)
            self.future_resolver.chunk_data_storage.pop(request_id, None)

    async def publish(self, topic, data=None, msg_type=None):
        if topic not in self.allowed:
            raise Go2MediaError('Non-media RPC is forbidden')
        return await super().publish(topic, data, msg_type)


def _rpc_data(response):
    data = response.get('data', {}).get('data', '')
    return json.loads(data) if isinstance(data, str) and data else data


def local_answer(ip, offer, aes_128_key=None):
    # Delegate to the vendored original LAN auth implementation.
    from native_webrtc_signaling import send_sdp_to_local_peer
    # Offer only the interface selected by the existing route to the robot.
    # Extra WLAN/Tailscale/Docker candidates can confuse the robot's ICE stack.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect((ip, 9991))
        local_ip = probe.getsockname()[0]
    lines = offer.sdp.splitlines()
    local_candidates = [line for line in lines if line.startswith('a=candidate:') and line.split()[4] == local_ip]
    if local_candidates:
        lines = [line for line in lines if not line.startswith('a=candidate:') or line in local_candidates]
    local_sdp = '\r\n'.join(lines) + '\r\n'
    payload = json.dumps({'id': 'STA_localNetwork', 'sdp': local_sdp,
                          'type': offer.type, 'token': ''})
    response = send_sdp_to_local_peer(ip, payload, aes_128_key=aes_128_key)
    if response is None:
        raise Go2MediaError('Go2 LAN signaling returned no answer')
    return json.loads(response)


class _SpeakerTrack(AudioStreamTrack):
    """A permanent negotiated sender: silence between locally decoded clips.

    MediaPlayer EOF terminates aiortc's RTP loop. Keeping one live track allows
    repeated feedback on the same connection without renegotiating the robot.
    """
    def __init__(self):
        super().__init__()
        self.samples = np.empty(0, dtype=np.int16)
        self.position = 0
        self.pts = 0
        self.started = None
        self.done = asyncio.Event()
        self.done.set()

    def load(self, samples):
        self.samples = samples
        self.position = 0
        self.done.clear()

    def clear(self):
        self.samples = np.empty(0, dtype=np.int16)
        self.position = 0
        self.done.set()

    async def recv(self):
        if self.readyState != 'live':
            raise MediaStreamError
        if self.started is None:
            self.started = time.monotonic()
        await asyncio.sleep(max(0, self.started + self.pts / 48000 - time.monotonic()))
        frame = av.AudioFrame(format='s16', layout='mono', samples=960)
        output = np.zeros(960, dtype=np.int16)
        count = min(960, len(self.samples) - self.position)
        if count > 0:
            output[:count] = self.samples[self.position:self.position + count]
            self.position += count
            if self.position >= len(self.samples):
                self.done.set()
        frame.planes[0].update(output.tobytes())
        frame.sample_rate = 48000
        frame.time_base = Fraction(1, 48000)
        frame.pts = self.pts
        self.pts += 960
        return frame


def _decode_audio(path):
    resampler = av.AudioResampler(format='s16', layout='mono', rate=48000)
    chunks = []
    with av.open(str(path)) as container:
        for frame in container.decode(audio=0):
            for converted in resampler.resample(frame):
                chunks.append(converted.to_ndarray().reshape(-1).copy())
        for converted in resampler.resample(None):
            chunks.append(converted.to_ndarray().reshape(-1).copy())
    if not chunks:
        raise Go2MediaError('File contains no decoded audio samples')
    return np.concatenate(chunks)


class Go2MediaConnection:
    """One LAN WebRTC peer. Share it for camera and audio; Go2 allows one peer.

    Callbacks receive decoded av AudioFrame/VideoFrame objects. Audio playback
    uses a permanent negotiated audio sender. Only media and heartbeat
    messages are sent; no sport/stance/remote-mode APIs exist in this adapter.
    """
    def __init__(self, ip='192.168.123.161', aes_128_key=None):
        self.ip = ip
        self.aes_128_key = aes_128_key or os.getenv('GO2_AES_128_KEY')
        self.pc = None
        self.channel = None
        self.datachannel = None
        self._owned_audio_ids = set()
        self.audiohub_playing = False
        self.audio_callbacks = []
        self.video_callbacks = []
        self.audio_frames = 0
        self.video_frames = 0
        self._ready = None
        self._tasks = set()
        self._heartbeat = None
        self._speaker_track = None
        self._audio_sender = None
        self.audio_enabled = False
        self.video_enabled = False
        self._play_lock = asyncio.Lock()

    def _send(self, kind, data, topic=''):
        if not self.channel or self.channel.readyState != 'open':
            raise Go2MediaError('Media data channel is not open')
        self.channel.send(json.dumps({'type': kind, 'topic': topic, 'data': data}))

    def _spawn(self, coro):
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _read_track(self, track):
        try:
            while True:
                frame = await track.recv()
                callbacks = self.audio_callbacks if track.kind == 'audio' else self.video_callbacks
                if track.kind == 'audio':
                    self.audio_frames += 1
                else:
                    self.video_frames += 1
                for callback in tuple(callbacks):
                    result = callback(frame)
                    if inspect.isawaitable(result):
                        await result
        except (MediaStreamError, asyncio.CancelledError):
            pass
        except Exception:
            logging.exception('Go2 %s callback failed', track.kind)

    async def _beat(self):
        try:
            while True:
                self._send('heartbeat', {'timeInStr': time.strftime('%Y-%m-%d %H:%M:%S'),
                                         'timeInNum': int(time.time())})
                await asyncio.sleep(2)
        except asyncio.CancelledError:
            pass

    async def connect(self, timeout=20):
        self._ready = asyncio.Event()
        self.pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        self.channel = self.pc.createDataChannel('data')
        self.datachannel = SimpleNamespace(pub_sub=_MediaPubSub(self.channel, self))
        audio_transceiver = self.pc.addTransceiver('audio', direction='sendrecv')
        self._audio_sender = audio_transceiver.sender
        self._speaker_track = _SpeakerTrack()
        self.pc.addTransceiver('video', direction='recvonly')

        @self.channel.on('message')
        def message(raw):
            if not isinstance(raw, str):
                return
            try:
                msg = json.loads(raw)
                if msg.get('topic', '').startswith(('rt/api/vui/', 'rt/api/audiohub/')):
                    self.datachannel.pub_sub.run_resolve(msg)
                if msg.get('type') == 'validation':
                    challenge = msg.get('data')
                    if challenge == 'Validation Ok.':
                        self._ready.set()
                    elif isinstance(challenge, str):
                        # Upstream validation.py: Unitree challenges before the standard DCEP open event.
                        self.channel._setReadyState('open')
                        key = base64.b64encode(hashlib.md5(('UnitreeGo2_' + challenge).encode()).digest()).decode()
                        self._send('validation', key)
                elif msg.get('type') == 'rtc_inner_req':
                    info = msg.get('info', {})
                    if info.get('req_type') == 'rtt_probe_send_from_mechine':
                        self._send('rtc_inner_req', info)
            except (ValueError, Go2MediaError):
                logging.exception('Invalid media channel message')

        @self.pc.on('track')
        def track_received(track):
            self._spawn(self._read_track(track))

        try:
            async def negotiate():
                offer = await self.pc.createOffer()
                await self.pc.setLocalDescription(offer)
                answer = await asyncio.get_running_loop().run_in_executor(
                    None, local_answer, self.ip, self.pc.localDescription, self.aes_128_key)
                if answer.get('sdp') == 'reject':
                    raise Go2MediaError('Go2 media is busy; disconnect other WebRTC client/phone app.')
                # The robot lists unreachable Wi-Fi/link-local candidates before its LAN candidate.
                # Keep the verified signaling IP when offered, so wired media does not wait
                # on unrelated interfaces. No IP rewriting or network configuration changes.
                lines = answer['sdp'].splitlines()
                candidates = [line for line in lines if line.startswith('a=candidate:')]
                lan = [line for line in candidates if len(line.split()) > 4 and line.split()[4] == self.ip]
                if lan:
                    lines = [line for line in lines if not line.startswith('a=candidate:') or line in lan]
                remote_sdp = '\r\n'.join(lines) + '\r\n'
                await self.pc.setRemoteDescription(RTCSessionDescription(sdp=remote_sdp, type=answer['type']))
                await self._ready.wait()
            await asyncio.wait_for(negotiate(), timeout)
            self._audio_sender.replaceTrack(self._speaker_track)
            self._heartbeat = self._spawn(self._beat())
            self.switch_video(False)
            return self
        except Exception:
            await self.close()
            raise

    def switch_audio(self, enabled=True):
        self._send('aud', 'on' if enabled else 'off')
        self.audio_enabled = enabled

    def switch_video(self, enabled=True):
        self._send('vid', 'on' if enabled else 'off')
        self.video_enabled = enabled

    async def audio_device(self):
        """Read-only queries through this existing peer; never change volume/switch."""
        result = {'queried_at': time.time(), 'read_only': True}
        for name, topic, api in [('vui_switch', 'rt/api/vui/request', 1002),
                                 ('volume', 'rt/api/vui/request', 1004),
                                 ('audio_list', 'rt/api/audiohub/request', 1001),
                                 ('play_mode', 'rt/api/audiohub/request', 1010)]:
            try:
                response = await self.datachannel.pub_sub.publish_request_new(topic, {'api_id':api})
                result[name] = {'ok':True, 'data':_rpc_data(response),
                                'ack':dict(self.datachannel.pub_sub.last_ack)}
            except Exception as exc:
                result[name] = {'ok':False, 'error':type(exc).__name__ + ': ' + str(exc)[:200]}
        return result

    async def play_audiohub(self, path):
        """Original upstream WAV upload/play with per-request robot status ACK."""
        path = Path(path).resolve()
        with wave.open(str(path), 'rb') as source:
            duration = source.getnframes() / source.getframerate()
        hub = WebRTCAudioHub(self)
        unique_id = None
        delivery = None
        async with self._play_lock:
            with tempfile.TemporaryDirectory(prefix='go2-audiohub-') as folder:
                filename = 'go2_feedback_' + uuid.uuid4().hex
                uploaded = Path(folder)/(filename + '.wav')
                shutil.copyfile(path, uploaded)
                try:
                    await hub.upload_audio_file(str(uploaded))
                    upload_ack = dict(self.datachannel.pub_sub.last_ack)
                    for attempt in range(3):
                        response = await hub.get_audio_list()
                        audio_list = _rpc_data(response).get('audio_list', [])
                        item = next((item for item in audio_list if item.get('CUSTOM_NAME') == filename), None)
                        if item:
                            unique_id = item.get('UNIQUE_ID')
                            break
                        await asyncio.sleep(.2)
                    if not unique_id:
                        raise Go2MediaError('Uploaded task-owned WAV was not present in the fresh robot audio list')
                    self._owned_audio_ids.add(unique_id)
                    await hub.play_by_uuid(unique_id)
                    play_ack = dict(self.datachannel.pub_sub.last_ack)
                    self.audiohub_playing = True
                    await asyncio.sleep(duration + .4)
                    delivery = {'path':str(path), 'duration':duration, 'transport':'audiohub',
                            'acknowledged':True, 'unique_id':unique_id,
                            'upload_ack':upload_ack, 'play_ack':play_ack,
                            'audibility_verified':False}
                    return delivery
                finally:
                    self.audiohub_playing = False
                    if unique_id in self._owned_audio_ids:
                        cleanup = {'unique_id':unique_id, 'removed':False}
                        try:
                            await hub.delete_record(unique_id)
                            self._owned_audio_ids.discard(unique_id)
                            cleanup.update(removed=True, ack=dict(self.datachannel.pub_sub.last_ack))
                        except Exception as exc:
                            cleanup['error'] = type(exc).__name__ + ': ' + str(exc)[:180]
                            logging.warning('Task-owned feedback audio cleanup failed: %s', unique_id)
                        if delivery is not None:
                            delivery['cleanup'] = cleanup

    async def play_file(self, path, duration=None):
        """Deliver a local WAV/MP3 on the persistent speaker RTP track.

        Returns sender-side delivery statistics. Acoustic playback is separately
        verified by microphone loopback; this method cannot promise audibility.
        """
        path = Path(path).resolve()
        if not path.is_file():
            raise FileNotFoundError(str(path))
        async with self._play_lock:
            samples = await asyncio.get_running_loop().run_in_executor(None, _decode_audio, path)
            playback_seconds = len(samples) / 48000
            before = await self._audio_sender.getStats()
            old = next((v for v in before.values() if v.type == 'outbound-rtp' and v.kind == 'audio'), None)
            old_packets = old.packetsSent if old else 0
            old_bytes = old.bytesSent if old else 0
            self.switch_audio(True)
            self._speaker_track.load(samples)
            try:
                await asyncio.wait_for(self._speaker_track.done.wait(), playback_seconds + 5)
                await asyncio.sleep(0.3)
                stats = await self._audio_sender.getStats()
                sent = next((v for v in stats.values() if v.type == 'outbound-rtp' and v.kind == 'audio'), None)
                return {'path': str(path), 'duration': playback_seconds,
                        'packets_sent': sent.packetsSent - old_packets if sent else 0,
                        'bytes_sent': sent.bytesSent - old_bytes if sent else 0}
            finally:
                self._speaker_track.clear()

    async def close(self):
        if self.channel and self.channel.readyState == 'open':
            if self.audio_enabled:
                self.switch_audio(False)
            if self.video_enabled:
                self.switch_video(False)
        if self._speaker_track:
            self._speaker_track.stop()
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self.pc:
            await self.pc.close()
        self.pc = None
        self.channel = None
