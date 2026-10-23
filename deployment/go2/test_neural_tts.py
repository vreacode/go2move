"""Offline tests for corrupt audio rejection, cache isolation, and library ownership."""
import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import numpy as np
from fastapi import HTTPException
import neural_tts as tts
import native_webrtc as media
import voice_hub as voice


class SynthesisTests(unittest.TestCase):
    def test_invalid_audio_never_becomes_playable_wav(self):
        for samples in ([0., float('nan')], [float('inf')], [], [0., 0.]):
            with self.subTest(samples=samples), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'speech.wav'
                model = Mock()
                model.generate.return_value = SimpleNamespace(samples=samples, sample_rate=24000)
                with patch.object(tts, 'CACHE', Path(directory) / 'cache'), \
                     patch.object(tts, '_load_model', return_value=model):
                    with self.assertRaises(RuntimeError):
                        tts.synthesize('你好', output, 'kokoro_zf001')
                self.assertFalse(output.exists())
                self.assertFalse(list(Path(directory).rglob('*.wav')))

    def test_cache_reuses_same_voice_but_separates_other_voices_and_repairs_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / 'cache'
            model = Mock()
            model.generate.return_value = SimpleNamespace(
                samples=np.array([0., .2, -.2, .1]), sample_rate=24000)
            with patch.object(tts, 'CACHE', cache), patch.object(tts, '_load_model', return_value=model):
                first = tts.synthesize('你好', Path(directory) / 'one.wav', 'kokoro_zf001')
                second = tts.synthesize('你好', Path(directory) / 'two.wav', 'kokoro_zf001')
                self.assertFalse(first['cached'])
                self.assertTrue(second['cached'])
                self.assertEqual(model.generate.call_count, 1)
                self.assertEqual(Path(first['path']).read_bytes(), Path(second['path']).read_bytes())
                tts.synthesize('你好', Path(directory) / 'three.wav', 'kokoro_zf002')
                self.assertEqual(model.generate.call_count, 2)
                for path in cache.glob('*.wav'):
                    path.write_bytes(b'broken')
                repaired = tts.synthesize('你好', Path(directory) / 'four.wav', 'kokoro_zf001')
                self.assertFalse(repaired['cached'])
                self.assertEqual(model.generate.call_count, 3)

    def test_selection_persists_and_rejects_invalid_or_uninstalled_voices(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(tts, 'SETTINGS', Path(directory) / 'settings.json'), \
             patch.object(tts, 'model_available', return_value=True):
            self.assertEqual(tts.selected_voice(), 'espeak_child')
            self.assertEqual(tts.select_voice('kokoro_zf003')['voice_id'], 'kokoro_zf003')
            with self.assertRaises(ValueError):
                tts.select_voice('../../arbitrary')
            self.assertEqual(tts.selected_voice(), 'kokoro_zf003')
            with patch.object(tts, 'model_available', return_value=False):
                with self.assertRaises(ValueError):
                    tts.select_voice('kokoro_zf001')
                tts.select_voice('espeak_child')
            tts.SETTINGS.write_text('{"voice_id": []}')
            self.assertEqual(tts.selected_voice(), 'espeak_child')

    def test_partial_install_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for name in ('model.onnx', 'voices.bin', 'tokens.txt', 'lexicon-zh.txt'):
                (folder / name).touch()
            with patch.object(tts, 'MODEL', folder), patch.object(tts, 'PACKAGES', folder):
                self.assertFalse(tts.model_available())


class ImportTests(unittest.IsolatedAsyncioTestCase):
    async def test_reimport_preserves_user_library_and_never_plays_or_deletes(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            clip_path = folder / 'hello.wav'
            clip_path.write_bytes(b'fixture-WAV')
            bank = {'voice_id': 'kokoro_zf001', 'voice_label': 'fixture voice', 'clips': [{
                'id': 'hello', 'label': '打招呼', 'text': '你好', 'path': str(clip_path),
                'robot_name': 'go2_voice_pack_zf001_hello_abcd1234', 'sha256': 'fixture'}]}
            library = [{'CUSTOM_NAME': 'user-song', 'UNIQUE_ID': 'user-uuid'}]
            adapter = Mock()
            async def listing():
                return {'data': {'data': json.dumps({'audio_list': library})}}
            async def upload(path):
                self.assertEqual(Path(path).read_bytes(), clip_path.read_bytes())
                library.append({'CUSTOM_NAME': Path(path).stem, 'UNIQUE_ID': 'prompt-uuid'})
            adapter.get_audio_list = AsyncMock(side_effect=listing)
            adapter.upload_audio_file = AsyncMock(side_effect=upload)
            adapter.play_by_uuid = AsyncMock()
            adapter.delete_record = AsyncMock()
            connection = Mock()
            connection._owned_audio_ids = set()
            with patch.object(voice, 'RUNTIME', folder), \
                 patch.object(voice, 'voice_policy', return_value={'enabled': True}), \
                 patch.object(voice, 'prepare_bank', return_value=bank), \
                 patch.object(voice, 'WebRTCAudioHub', return_value=adapter):
                hub = voice.VoiceHub()
                hub.ensure_audio = AsyncMock(return_value=connection)
                first = await hub.import_voice_pack()
                second = await hub.import_voice_pack()
            self.assertEqual(first['imported_count'], 1)
            self.assertFalse(first['clips'][0]['reused'])
            self.assertTrue(second['clips'][0]['reused'])
            self.assertFalse(second['played'])
            adapter.upload_audio_file.assert_awaited_once()
            adapter.play_by_uuid.assert_not_awaited()
            adapter.delete_record.assert_not_awaited()
            self.assertEqual(connection._owned_audio_ids, set())
            self.assertEqual(library[0], {'CUSTOM_NAME': 'user-song', 'UNIQUE_ID': 'user-uuid'})
            self.assertEqual(len(library), 2)

    async def test_paused_audio_cannot_import_or_connect(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(voice, 'RUNTIME', Path(directory)), \
             patch.object(voice, 'voice_policy', return_value={'enabled': False, 'reason': 'held'}), \
             patch.object(voice, 'prepare_bank') as bank, patch.object(voice, 'Go2MediaConnection') as connect:
            with self.assertRaises(HTTPException) as caught:
                await voice.VoiceHub().import_voice_pack()
            self.assertEqual(caught.exception.status_code, 409)
            bank.assert_not_called()
            connect.assert_not_called()


class MediaOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_prompt_upload_allowed_but_foreign_upload_and_persistent_delete_rejected(self):
        owner = SimpleNamespace(_owned_audio_ids=set())
        adapter = media._MediaPubSub(Mock(), owner)
        ack = {'data': {'header': {'status': {'code': 0}}}}
        with patch.object(media.WebRTCDataChannelPubSub, 'publish_request_new', AsyncMock(return_value=ack)) as rpc:
            await adapter.publish_request_new('rt/api/audiohub/request', {
                'api_id': 2001, 'parameter': {'file_name': 'go2_voice_pack_zf001_hello_abcd1234'}})
            rpc.assert_awaited_once()
            for name in ('user-song', 'go2_voice_pack_user-song', 'go2_voice_pack_zf001_other_abcd1234'):
                with self.subTest(name=name), self.assertRaises(media.Go2MediaError):
                    await adapter.publish_request_new('rt/api/audiohub/request', {
                        'api_id': 2001, 'parameter': {'file_name': name}})
            with self.assertRaises(media.Go2MediaError):
                await adapter.publish_request_new('rt/api/audiohub/request', {
                    'api_id': 1009, 'parameter': {'unique_id': 'persistent-prompt-uuid'}})
            with self.assertRaises(media.Go2MediaError):
                await adapter.publish_request_new('rt/api/sport/request', {'api_id': 1008})
            self.assertEqual(rpc.await_count, 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
