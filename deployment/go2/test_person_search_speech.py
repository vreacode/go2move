#!/usr/bin/env python3
"""Speech lifecycle with mocked HTTP and temporary files; no physical audio."""
import asyncio
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import httpx
import numpy as np
import person_search as product


class MockSpeechHTTP:
    def __init__(self):
        self.outcomes = []
        self.requests = []
        self.block = False
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    def client(self, **kwargs):
        owner = self
        class Client:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                return False
            async def post(self, url, **options):
                owner.requests.append({'url':url, **options})
                owner.entered.set()
                if owner.block:
                    await owner.release.wait()
                outcome = owner.outcomes.pop(0) if owner.outcomes else {'delivered':True}
                if isinstance(outcome, Exception):
                    raise outcome
                return httpx.Response(200,json=outcome,request=httpx.Request('POST',url))
        return Client()


class SpeechTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='go2-speech-test-')
        root = Path(self.temporary.name)
        self.runtime,self.camera = root/'runtime',root/'camera'
        self.runtime.mkdir()
        self.camera.mkdir()
        self.now = 1000.0
        self.searcher = product.Searcher()
        self.searcher.target = np.zeros(512,dtype=np.float32)
        self.searcher.target[0] = 1
        self.searcher.target_info = {'id':'reference-id','name':'Isolated target'}
        self.http = MockSpeechHTTP()
        self.patches = [patch.object(product,'RUNTIME',self.runtime),
                        patch.object(product,'CAMERA',self.camera),
                        patch.object(product,'searcher',self.searcher),
                        patch.object(product,'time',SimpleNamespace(time=lambda:self.now,
                            monotonic=lambda:self.now,sleep=time.sleep)),
                        patch.object(product.httpx,'AsyncClient',self.http.client),
                        patch.object(product,'voice_policy',lambda:{'enabled':True,'reason':'isolated mocked speech test'}),
                        patch('go2_platform_write.backup',lambda *_:None)]
        for item in self.patches:
            item.start()
        await product.start(product.SearchRequest(speak=True))

    async def asyncTearDown(self):
        task=self.searcher.speech_task
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    def frame(self,event='event-one',**changes):
        result={'generation':self.searcher.generation,'captured_at':self.now,
                'suspected_target':True,'event_id':event,'target_id':self.searcher.target_info['id']}
        result.update(changes)
        self.searcher.last=result
        return result

    def speech_state(self):
        speech=self.searcher.speech
        return speech.get('state',speech.get('status')) if speech else None

    async def finish(self):
        task=self.searcher.speech_task
        self.assertIsNotNone(task,'Valid results must enqueue a speech task')
        await asyncio.wait_for(asyncio.shield(task),timeout=1)

    def assert_speech(self,state,delivered,event='event-one'):
        self.assertEqual(state,self.speech_state())
        speech=self.searcher.speech
        self.assertEqual(delivered,speech['delivered'])
        self.assertEqual(self.searcher.generation,speech['generation'])
        self.assertEqual(event,speech['event_id'])
        self.assertEqual(speech,self.searcher.status()['speech'])

    async def test_http_200_undelivered_fails_then_retries_after_cooldown(self):
        self.http.outcomes=[{'delivered':False,'error':'Mock audio unavailable'},{'delivered':True}]
        self.searcher.maybe_announce(self.frame())
        await self.finish()
        self.assert_speech('failed',False)
        self.assertTrue(self.searcher.speech['error'])
        self.now+=1
        self.searcher.maybe_announce(self.frame())
        await asyncio.sleep(0)
        self.assertEqual(1,len(self.http.requests))
        self.now+=5
        self.searcher.maybe_announce(self.frame())
        await self.finish()
        self.assertEqual(2,len(self.http.requests))
        self.assert_speech('sent',True)

    async def test_http_exception_is_visible_and_retry_succeeds(self):
        self.http.outcomes=[httpx.ConnectError('Mock service offline'),{'delivered':True}]
        self.searcher.maybe_announce(self.frame())
        await self.finish()
        self.assert_speech('failed',False)
        self.assertIn('Mock service offline',self.searcher.speech['error'])
        self.now+=6
        self.searcher.maybe_announce(self.frame())
        await self.finish()
        self.assert_speech('sent',True)

    async def test_speech_survives_next_frame_replacing_last(self):
        self.http.block=True
        result=self.frame()
        self.searcher.maybe_announce(result)
        await asyncio.wait_for(self.http.entered.wait(),timeout=1)
        self.assertEqual('sending',self.speech_state())
        self.now+=.5
        replacement=self.frame()
        self.assertIsNot(result,replacement)
        self.http.release.set()
        await self.finish()
        self.assertIs(replacement,self.searcher.last)
        self.assert_speech('sent',True)
        self.now+=.5
        self.frame(suspected_target=False)
        self.assert_speech('sent',True)

    async def test_success_deduplicates_same_event_and_speaks_new_event(self):
        self.searcher.maybe_announce(self.frame())
        await self.finish()
        self.assert_speech('sent',True)
        self.now+=1
        self.searcher.maybe_announce(self.frame())
        await asyncio.sleep(0)
        self.assertEqual(1,len(self.http.requests))
        self.now+=5
        self.searcher.maybe_announce(self.frame(event='event-two'))
        await self.finish()
        self.assertEqual(2,len(self.http.requests))
        self.assert_speech('sent',True,event='event-two')

    async def test_restart_resets_success_dedup_and_failure_cooldown(self):
        self.searcher.maybe_announce(self.frame())
        await self.finish()
        await product.stop()
        await product.start(product.SearchRequest(speak=True))
        self.assertIsNone(self.searcher.speech)
        self.http.outcomes=[{'delivered':False,'error':'Mock failure'}]
        self.searcher.maybe_announce(self.frame())
        await self.finish()
        self.assertEqual(2,len(self.http.requests))
        self.assert_speech('failed',False)
        await product.stop()
        await product.start(product.SearchRequest(speak=True))
        self.assertIsNone(self.searcher.speech)
        self.searcher.maybe_announce(self.frame())
        await self.finish()
        self.assertEqual(3,len(self.http.requests))
        self.assert_speech('sent',True)

    async def test_new_target_clears_old_speech(self):
        self.http.outcomes=[{'delivered':False,'error':'Mock failure'}]
        self.searcher.maybe_announce(self.frame())
        await self.finish()
        self.searcher.store_target(np.zeros((64,96,3),np.uint8),[8,8,64,56],
            self.searcher.target.copy(),'Replacement','upload')
        self.assertIsNone(self.searcher.speech)
        self.assertFalse(self.searcher.active)
        await product.start(product.SearchRequest(speak=True))
        self.searcher.maybe_announce(self.frame())
        await self.finish()
        self.assertEqual(2,len(self.http.requests))
        self.assert_speech('sent',True)

    async def test_disabled_stopped_old_generation_stale_and_unmatched_drop(self):
        result=self.frame()
        self.searcher.speak=False
        self.searcher.maybe_announce(result)
        self.searcher.speak=True
        self.searcher.active=False
        self.searcher.maybe_announce(result)
        self.searcher.active=True
        self.searcher.maybe_announce(self.frame(generation=self.searcher.generation-1))
        self.searcher.maybe_announce(self.frame(captured_at=self.now-3))
        self.searcher.maybe_announce(self.frame(suspected_target=False))
        await asyncio.sleep(0)
        self.assertEqual([],self.http.requests)
        self.assertIsNone(self.searcher.speech_task)
        self.assertIsNone(self.searcher.speech)

    async def test_stop_or_new_generation_during_http_discards_old_completion(self):
        self.http.block=True
        old=self.frame()
        self.searcher.maybe_announce(old)
        await asyncio.wait_for(self.http.entered.wait(),timeout=1)
        await product.stop()
        await product.start(product.SearchRequest(speak=True))
        self.http.release.set()
        task=self.searcher.speech_task
        if task:
            try:
                await asyncio.wait_for(asyncio.shield(task),timeout=1)
            except asyncio.CancelledError:
                pass
        self.assertIsNone(self.searcher.speech)
        self.assertGreater(self.searcher.generation,old['generation'])


    async def test_stop_during_http_does_not_leave_sending_forever(self):
        self.http.block=True
        self.searcher.maybe_announce(self.frame())
        await asyncio.wait_for(self.http.entered.wait(),timeout=1)
        task=self.searcher.speech_task
        await product.stop()
        self.http.release.set()
        try:
            await asyncio.wait_for(asyncio.shield(task),timeout=1)
        except asyncio.CancelledError:
            pass
        self.assertFalse(self.searcher.active)
        self.assertNotEqual('sending',self.speech_state(),
            'Completed stale-generation request must not remain sending after stop')

    async def test_result_expiring_after_queue_does_not_leave_sending_forever(self):
        self.assertTrue(self.searcher.maybe_announce(self.frame()))
        self.now+=3
        await self.finish()
        self.assertEqual([],self.http.requests)
        self.assertNotEqual('sending',self.speech_state(),
            'Dropped expired queued request must finalize or clear sending state')


    async def test_disabled_voice_policy_blocks_http_even_if_speak_enabled(self):
        self.assertTrue(self.searcher.speak)
        with patch.object(product,'voice_policy',return_value={'enabled':False,'reason':'isolated hold'}):
            self.assertFalse(self.searcher.maybe_announce(self.frame()))
            await asyncio.sleep(0)
            self.assertEqual([],self.http.requests)
            self.assertIsNone(self.searcher.speech_task)
            self.assertIsNone(self.searcher.speech)
            self.assertFalse(self.searcher.status()['voice_policy']['enabled'])
            await product.start(product.SearchRequest(speak=True))
            self.assertFalse(self.searcher.speak)


if __name__=='__main__':
    unittest.main(verbosity=2)
