"""Search commands, auth, and recording validation with no robot services."""
import base64
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import wave

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'apps/nano-deploy'))
from dashboard_platform.server import go2_commands as commands
from dashboard_platform.server import app as product


def wav(seconds=0.2, channels=1, rate=16000):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as output:
        output.setnchannels(channels); output.setsampwidth(2); output.setframerate(rate)
        output.writeframes(b'\0' * int(seconds * rate) * channels * 2)
    return base64.b64encode(buffer.getvalue()).decode()


class Commands(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        async def person(method, path, payload):
            self.calls.append((method, path, payload))
            return {'active': False, 'search_task': {'state': 'found_pending_confirmation'}}
        self.person = person

    async def test_movement_and_region_commands_never_call_backend(self):
        for text in ['前进', '去A区找穿红衣的人', '请左转后找人', '开始自主探索', '站起来', '开始找人然后前进']:
            result = await commands.execute_command(text, self.person)
            self.assertFalse(result['executed']); self.assertFalse(result['motion_enabled'])
        self.assertEqual([], self.calls)

    async def test_negative_and_unknown_requests_do_not_execute(self):
        for text in ['不要开始找人', '不要停止找人', '停止找人然后开始找人', '帮我烧水']:
            self.assertFalse((await commands.execute_command(text, self.person))['executed'])
        self.assertEqual([], self.calls)

    async def test_start_is_stationary_and_silent(self):
        self.assertTrue((await commands.execute_command('帮我开始找人', self.person))['executed'])
        self.assertEqual([('POST', '/start', {'mode': 'stationary', 'speak': False, 'timeout_seconds': 120})], self.calls)

    async def test_emergency_phrase_is_only_visual_stop_and_says_handheld(self):
        result = await commands.execute_command('急停', self.person)
        self.assertIn('手柄', result['reply']); self.assertEqual([('POST', '/stop', None)], self.calls)

    async def test_description_uses_existing_target_then_silent_start(self):
        await commands.execute_command('找穿红色上衣的人', self.person)
        self.assertEqual(['/description', '/start'], [row[1] for row in self.calls])
        self.assertFalse(self.calls[1][2]['speak'])

    async def test_failed_description_does_not_start_old_target(self):
        async def rejected(method, path, body):
            self.calls.append(path); raise product.HTTPException(400, 'unsupported')
        with self.assertRaises(product.HTTPException):
            await commands.execute_command('找戴帽子的人', rejected)
        self.assertEqual(['/description'], self.calls)

    async def test_capture_requests_ui_selection_and_no_robot_api(self):
        result = await commands.execute_command('记住这个人', self.person)
        self.assertEqual('capture', result['intent']); self.assertEqual([], self.calls)

    async def test_status_does_not_claim_pending_candidate_is_confirmed(self):
        result = await commands.execute_command('找人状态', self.person)
        self.assertIn('等待人工确认', result['reply']); self.assertEqual([('GET', '/status', None)], self.calls)

    def test_audio_valid_and_invalid_formats(self):
        self.assertTrue(commands.decode_audio(wav()).startswith(b'RIFF'))
        for encoded in [wav(channels=2), wav(rate=8000), wav(seconds=0), wav(seconds=13), 'invalid==', base64.b64encode(b'RIFFnotawave').decode()]:
            with self.assertRaises(product.HTTPException): commands.decode_audio(encoded)

    async def test_every_new_api_requires_auth_before_backend(self):
        with patch.object(product, 'go2_person_request') as backend, patch.object(commands, 'transcribe_audio') as speech:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=product.app), base_url='http://test', trust_env=False) as client:
                for method, path, body in [('POST','/api/go2/person/description',{'text':'红衣'}),
                    ('GET','/api/go2/person/tasks',None),('GET','/api/go2/person/history/export.json',None),
                    ('POST','/api/go2/person/history/'+'a'*32+'/review',{'status':'confirmed'}),
                    ('POST','/api/go2/task/command',{'text':'开始找人'}),
                    ('POST','/api/go2/task/transcribe',{'wav_base64':wav()})]:
                    response = await client.request(method,path,json=body)
                    self.assertEqual(401,response.status_code)
        backend.assert_not_called(); speech.assert_not_called()

    async def test_invalid_review_or_duration_never_forwards(self):
        with patch.object(product, 'go2_person_request') as backend:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=product.app),base_url='http://test',trust_env=False,
                    headers={'Authorization':'Bearer '+product.settings.auth_token}) as client:
                for payload in ({'timeout_seconds':0},{'timeout_seconds':601},{'color_threshold':1}):
                    self.assertEqual(422,(await client.post('/api/go2/person/start',json=payload)).status_code)
                self.assertEqual(422,(await client.post('/api/go2/person/history/'+'a'*32+'/review',json={'status':'automatic_match'})).status_code)
        backend.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
