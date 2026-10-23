#!/usr/bin/env python3
"""Pause must prevent media connection even if the service is restarted."""
import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock, AsyncMock
import av
import numpy as np
from native_audio import MicrophoneRecorder
import voice_hub as product
from fastapi import HTTPException

class HoldTests(unittest.TestCase):
    def test_hold_prevents_connection_and_playback(self):
        async def run():
            with tempfile.TemporaryDirectory() as directory, patch.object(product, 'RUNTIME', Path(directory)), patch.object(product, 'voice_policy', return_value={'enabled':False, 'reason':'test hold'}), patch.object(product, 'Go2MediaConnection') as connect, patch.object(product, 'speak') as speak:
                hub = product.VoiceHub()
                response = await hub.feedback('test')
                self.assertFalse(response['delivered'])
                self.assertEqual('RuntimeError: test hold', response['error'])
                connect.assert_not_called()
                speak.assert_not_called()
        asyncio.run(run())

    def test_hold_prevents_startup_and_commands(self):
        async def run():
            with tempfile.TemporaryDirectory() as directory, patch.object(product, 'RUNTIME', Path(directory)), patch.object(product, 'voice_policy', return_value={'enabled':False, 'reason':'test hold'}), patch.object(product, 'Go2MediaConnection') as connect:
                hub=product.VoiceHub()
                with patch.object(product,'hub',hub):
                    async with product.lifespan(product.app):
                        self.assertIsNone(hub.connect_task)
                        for operation in [lambda: product.command(product.TextRequest(text='开始建图')), lambda: product.listen(product.ListenRequest()), product.audio_device]:
                            with self.assertRaises(HTTPException) as caught:
                                await operation()
                            self.assertEqual(409,caught.exception.status_code)
                connect.assert_not_called()
        asyncio.run(run())


class ListenTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.runtime_patch = patch.object(product, 'RUNTIME', Path(self.directory.name))
        self.runtime_patch.start()

    async def asyncTearDown(self):
        self.runtime_patch.stop()
        self.directory.cleanup()

    def make_hub(self):
        hub = Mock()
        hub.audio_lock = asyncio.Lock()
        hub.listen_lock = asyncio.Lock()
        hub.ensure_audio = AsyncMock(return_value=Mock())
        hub.command = AsyncMock()
        hub.feedback = AsyncMock()
        return hub

    async def test_capture_failure_turns_off_microphone(self):
        hub = self.make_hub()
        connection = hub.ensure_audio.return_value
        with patch.object(product, 'hub', hub), patch.object(product, 'voice_policy', return_value={'enabled': True}), \
             patch.object(product, 'record', AsyncMock(side_effect=RuntimeError('capture failed'))):
            with self.assertRaises(HTTPException) as caught:
                await product.listen(product.ListenRequest(execute=False, speak=False))
            self.assertEqual(caught.exception.status_code, 503)
        connection.switch_audio.assert_called_once_with(False)
        hub.command.assert_not_awaited()
        hub.feedback.assert_not_awaited()

    async def test_free_sentence_transcription_does_not_use_command_grammar(self):
        hub = self.make_hub()
        hub.run_process = AsyncMock(return_value='{"text":"你好机器狗","confidence":0.91,"restricted_vocabulary":false}')
        with patch.object(product, 'hub', hub), patch.object(product, 'voice_policy', return_value={'enabled': True}), \
             patch.object(product, 'record', AsyncMock(return_value={'samples': 80000, 'rms': 1200, 'duration': 5})):
            result = await product.listen(product.ListenRequest(execute=False, speak=False))
        self.assertEqual(result['text'], '你好机器狗')
        self.assertEqual(result['intent'], 'transcription')
        self.assertNotIn('--phrase', hub.run_process.call_args.args)
        self.assertFalse(result['executed'])
        hub.command.assert_not_awaited()
        hub.feedback.assert_not_awaited()
        self.assertEqual(hub.capture, {'phase': 'idle'})
        self.assertEqual(hub.last_listen['recognition']['text'], '你好机器狗')

    async def test_command_execution_retains_restricted_vocabulary(self):
        hub = self.make_hub()
        hub.run_process = AsyncMock(return_value='{"text":"拍照","confidence":0.91}')
        hub.command.return_value = {'text':'拍照', 'intent':'photo', 'executed':True}
        with patch.object(product, 'hub', hub), patch.object(product, 'voice_policy', return_value={'enabled': True}), \
             patch.object(product, 'record', AsyncMock(return_value={'samples': 80000, 'rms': 1200})):
            await product.listen(product.ListenRequest(execute=True, speak=False))
        self.assertIn('--phrase', hub.run_process.call_args.args)
        hub.command.assert_awaited_once()
        hub.feedback.assert_not_awaited()

    async def test_noisy_uncertain_transcription_reports_hint_without_execution(self):
        hub = self.make_hub()
        hub.run_process = AsyncMock(return_value='{"text":"我","confidence":0.21}')
        with patch.object(product, 'hub', hub), patch.object(product, 'voice_policy', return_value={'enabled': True}), \
             patch.object(product, 'record', AsyncMock(return_value={'samples':80000,'rms':1100,'level_variation_db':2.6})):
            result = await product.listen(product.ListenRequest(execute=False, speak=False))
        self.assertEqual(result['intent'],'uncertain')
        self.assertIn('背景声',result['listen_hint'])
        self.assertFalse(result['executed'])
        hub.command.assert_not_awaited()
        hub.feedback.assert_not_awaited()
        self.assertTrue((product.RUNTIME/'last_listen.json').is_file())

    async def test_capture_state_tracks_actual_stages_and_rejects_concurrent_listen(self):
        hub = self.make_hub()
        capture_started, capture_release = asyncio.Event(), asyncio.Event()
        decode_started, decode_release = asyncio.Event(), asyncio.Event()
        async def capture(*args, **kwargs):
            self.assertEqual(hub.capture['phase'], 'recording')
            capture_started.set()
            await capture_release.wait()
            return {'samples':80000,'rms':1500}
        async def decode(*args, **kwargs):
            self.assertEqual(hub.capture['phase'], 'recognizing')
            decode_started.set()
            await decode_release.wait()
            return '{"text":"你好","confidence":0.9}'
        hub.run_process = AsyncMock(side_effect=decode)
        with patch.object(product,'hub',hub), patch.object(product,'voice_policy',return_value={'enabled':True}), \
             patch.object(product,'record',AsyncMock(side_effect=capture)):
            task=asyncio.create_task(product.listen(product.ListenRequest(execute=False,speak=False)))
            await asyncio.wait_for(capture_started.wait(),1)
            with self.assertRaises(HTTPException) as caught:
                await product.listen(product.ListenRequest(execute=False,speak=False))
            self.assertEqual(caught.exception.status_code,409)
            capture_release.set()
            await asyncio.wait_for(decode_started.wait(),1)
            decode_release.set()
            await asyncio.wait_for(task,1)
        self.assertEqual(hub.capture,{'phase':'idle'})

    async def test_cancelled_capture_turns_off_microphone(self):
        hub = self.make_hub()
        connection = hub.ensure_audio.return_value
        with patch.object(product, 'hub', hub), patch.object(product, 'voice_policy', return_value={'enabled': True}), \
             patch.object(product, 'record', AsyncMock(side_effect=asyncio.CancelledError())):
            with self.assertRaises(asyncio.CancelledError):
                await product.listen(product.ListenRequest(execute=False, speak=False))
        connection.switch_audio.assert_called_once_with(False)

    async def test_transcription_only_does_not_execute_or_speak(self):
        hub = self.make_hub()
        hub.run_process = AsyncMock(return_value='{"text":"拍照","confidence":0.9}')
        with patch.object(product, 'hub', hub), patch.object(product, 'voice_policy', return_value={'enabled': True}), \
             patch.object(product, 'record', AsyncMock(return_value={'samples': 32000})):
            result = await product.listen(product.ListenRequest(execute=False, speak=False))
        self.assertEqual(result['intent'], 'transcription')
        self.assertEqual(result['text'], '拍照')
        hub.ensure_audio.return_value.switch_audio.assert_called_once_with(False)
        hub.command.assert_not_awaited()
        hub.feedback.assert_not_awaited()


class RecorderTests(unittest.TestCase):
    def test_level_metrics_track_changing_audio_and_recording_stays_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder=MicrophoneRecorder(Path(directory)/'capture.wav',max_seconds=.06)
            for index, amplitude in enumerate((100,100,3000,3000)):
                pcm=(amplitude*np.sin(np.arange(320)*2*np.pi/16)).astype(np.int16).reshape(1,-1)
                frame=av.AudioFrame.from_ndarray(pcm,format='s16',layout='mono')
                frame.sample_rate=16000; frame.pts=index*320
                recorder.accept(frame)
            stats=recorder.close()
        self.assertEqual(stats['samples'],960)
        self.assertEqual(stats['input_source'],'robot_body_microphone')
        self.assertGreater(stats['level_variation_db'],20)
        self.assertGreater(stats['rms'],0)

    def test_silent_audio_has_finite_level_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder=MicrophoneRecorder(Path(directory)/'quiet.wav',max_seconds=.02)
            frame=av.AudioFrame.from_ndarray(np.zeros((1,320),dtype=np.int16),format='s16',layout='mono')
            frame.sample_rate=16000
            recorder.accept(frame)
            stats=recorder.close()
        self.assertEqual(stats['rms'],0)
        self.assertEqual(stats['level_variation_db'],0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
