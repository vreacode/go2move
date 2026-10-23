#!/usr/bin/env python3
"""Isolated patrol state-machine and API regressions: no ROS, hardware or network."""
import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
import httpx
from pydantic import ValidationError
import person_patrol as product


def route_payload(**changes):
    value = {'name':'Test area', 'frame_id':'map', 'points':[
        {'name':'Door', 'x':1, 'y':2, 'yaw':0},
        {'name':'Window', 'x':3, 'y':4, 'yaw':1},
        {'name':'Hall', 'x':5, 'y':6, 'yaw':2}]}
    value.update(changes)
    return value


def route():
    return dict(route_payload(), id='a'*32)


class FakeNavigation:
    def __init__(self, events, outcomes=None, blocked=False):
        self.events = events
        self.outcomes = list(outcomes or [])
        self.blocked = blocked
        self.entered = asyncio.Event()
        self.cancel_count = 0

    async def go(self, point):
        self.events.append(('depart',point['name']))
        self.entered.set()
        if self.blocked:
            await asyncio.Event().wait()
        outcome = self.outcomes.pop(0) if self.outcomes else True
        if isinstance(outcome, Exception):
            raise outcome
        if outcome:
            self.events.append(('arrival',point['name']))
        return outcome

    async def cancel(self):
        self.cancel_count += 1
        self.events.append(('cancel',None))


class FakeObservation:
    def __init__(self, events, outcomes=None, blocked=False):
        self.events = events
        self.outcomes = list(outcomes or [])
        self.blocked = blocked
        self.entered = asyncio.Event()
        self.target_ids = []

    async def observe(self, point, target_id):
        self.events.append(('observe',point['name']))
        self.target_ids.append(target_id)
        self.entered.set()
        if self.blocked:
            await asyncio.Event().wait()
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = []
        self.clock = patch.object(product,'time',SimpleNamespace(time=lambda:1000.0))
        self.clock.start()

    async def asyncTearDown(self):
        self.clock.stop()

    def runner(self, navigation=None, observation=None, **kwargs):
        self.navigation = navigation or FakeNavigation(self.events)
        self.observation = observation or FakeObservation(self.events)
        return product.PatrolRunner(self.navigation,self.observation,'original-target',**kwargs)

    def assert_safe_report(self, report):
        self.assertEqual('simulation',report['mode'])
        self.assertFalse(report['motion_enabled'])
        self.assertFalse(report['physical_execution'])
        self.assertEqual('simulation',report['evidence_source'])
        self.assertEqual(1,self.navigation.cancel_count)
        self.assertIn('completed_at',report)

    async def test_each_arrival_precedes_observation_and_next_point(self):
        runner = self.runner()
        report = await runner.run(route())
        self.assertTrue(report['ok'])
        self.assertFalse(report['found'])
        self.assertNotIn('evidence',report)
        self.assertEqual([(event,name) for name in ['Door','Window','Hall']
            for event in ['depart','arrival','observe']]+[('cancel',None)],self.events)
        self.assertEqual(['original-target']*3,self.observation.target_ids)
        self.assertEqual(['navigating','arrived','observing','not_found']*3,
                         [step['state'] for step in report['steps']])
        self.assert_safe_report(report)

    async def test_fresh_matching_evidence_stops_at_first_discovery(self):
        evidence = {'target_id':'original-target','suspected_target':True,'captured_at':1000.0}
        runner = self.runner(observation=FakeObservation(self.events,[None,evidence]))
        report = await runner.run(route())
        self.assertTrue(report['ok'])
        self.assertTrue(report['found'])
        self.assertEqual(evidence,report['evidence'])
        self.assertEqual(['Door','Window'],[name for event,name in self.events if event=='depart'])
        self.assertEqual('found',report['steps'][-1]['state'])
        self.assert_safe_report(report)

    async def test_unsuccessful_arrival_never_observes_or_advances(self):
        runner = self.runner(navigation=FakeNavigation(self.events,[False]))
        report = await runner.run(route())
        self.assertFalse(report['ok'])
        self.assertIn('Door',report['error'])
        self.assertEqual([('depart','Door'),('cancel',None)],self.events)
        self.assert_safe_report(report)

    async def test_navigation_exception_never_observes_or_advances(self):
        runner = self.runner(navigation=FakeNavigation(self.events,[RuntimeError('mock nav failure')]))
        report = await runner.run(route())
        self.assertFalse(report['ok'])
        self.assertIn('mock nav failure',report['error'])
        self.assertEqual([('depart','Door'),('cancel',None)],self.events)
        self.assert_safe_report(report)

    async def test_navigation_timeout_cancels_and_does_not_advance(self):
        runner = self.runner(navigation=FakeNavigation(self.events,blocked=True),navigation_timeout=.02)
        report = await runner.run(route())
        self.assertFalse(report['ok'])
        self.assertTrue(report['error'])
        self.assertEqual([('depart','Door'),('cancel',None)],self.events)
        self.assert_safe_report(report)

    async def test_observation_exception_and_timeout_do_not_advance(self):
        for observation in [FakeObservation(self.events,[RuntimeError('mock observation failure')]),
                            FakeObservation(self.events,blocked=True)]:
            with self.subTest(blocked=observation.blocked):
                self.events.clear()
                runner = self.runner(observation=observation,observation_timeout=.02)
                report = await runner.run(route())
                self.assertFalse(report['ok'])
                self.assertTrue(report['error'])
                self.assertEqual([('depart','Door'),('arrival','Door'),('observe','Door'),('cancel',None)],self.events)
                self.assert_safe_report(report)

    async def test_target_change_rejects_result_even_without_match(self):
        for matched in [False,True]:
            with self.subTest(matched=matched):
                self.events.clear()
                runner = self.runner(observation=FakeObservation(self.events,[
                    {'target_id':'replacement-target','suspected_target':matched,'captured_at':1000.0}]))
                report = await runner.run(route())
                self.assertFalse(report['ok'])
                self.assertFalse(report['found'])
                self.assertIn('目标已更换',report['error'])
                self.assertEqual(['Door'],[name for event,name in self.events if event=='depart'])
                self.assert_safe_report(report)

    async def test_stale_prearrival_future_and_missing_timestamps_are_rejected(self):
        for stamp in [997.0,999.99,1000.2,None]:
            with self.subTest(stamp=stamp):
                self.events.clear()
                evidence = {'target_id':'original-target','suspected_target':True}
                if stamp is not None:
                    evidence['captured_at'] = stamp
                runner = self.runner(observation=FakeObservation(self.events,[evidence]))
                report = await runner.run(route())
                self.assertFalse(report['ok'])
                self.assertFalse(report['found'])
                self.assertIn('过期',report['error'])
                self.assertNotIn('evidence',report)
                self.assertEqual(['Door'],[name for event,name in self.events if event=='depart'])
                self.assert_safe_report(report)

    async def test_task_cancel_during_navigation_calls_cancel(self):
        runner = self.runner(navigation=FakeNavigation(self.events,blocked=True))
        task = asyncio.create_task(runner.run(route()))
        await asyncio.wait_for(self.navigation.entered.wait(),timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(1,self.navigation.cancel_count)
        self.assertEqual('cancelled',runner.steps[-1]['state'])
        self.assertEqual([('depart','Door'),('cancel',None)],self.events)

    async def test_task_cancel_during_observation_calls_cancel(self):
        runner = self.runner(observation=FakeObservation(self.events,blocked=True))
        task = asyncio.create_task(runner.run(route()))
        await asyncio.wait_for(self.observation.entered.wait(),timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(1,self.navigation.cancel_count)
        self.assertEqual('cancelled',runner.steps[-1]['state'])
        self.assertEqual(['Door'],[name for event,name in self.events if event=='depart'])


class StoreAndAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='go2-patrol-test-')
        self.root = Path(self.temporary.name)
        self.backups = []
        def collect_backup(path):
            path = Path(path)
            self.assertTrue(self.root in path.parents,'All writes must remain isolated')
            self.backups.append((path,path.read_bytes() if path.exists() else None))
        self.patches = [patch('go2_platform_write.backup',collect_backup),patch.object(product,'BASE',self.root)]
        for item in self.patches:
            item.start()
        self.app = FastAPI()
        self.preparation = product.attach_patrol(self.app)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://isolated.test')

    async def asyncTearDown(self):
        await self.client.aclose()
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    async def test_valid_routes_normalize_and_persist_with_prewrite_backups(self):
        first = self.preparation.store.save(product.SearchRoute(**route_payload(name=' Test area ')))
        self.assertEqual('Test area',first['name'])
        self.assertRegex(first['id'],r'^[0-9a-f]{32}$')
        self.assertEqual([(self.preparation.store.path,None)],self.backups)
        before = self.preparation.store.path.read_bytes()
        second = self.preparation.store.save(product.SearchRoute(**route_payload(name='Second area')))
        self.assertNotEqual(first['id'],second['id'])
        self.assertEqual(before,self.backups[-1][1])
        reopened = product.RouteStore(self.preparation.store.directory)
        self.assertEqual([first,second],reopened.routes())
        self.assertEqual(first,reopened.get(first['id']))
        self.assertEqual([],list(self.root.rglob('*.tmp.json')))
        with self.assertRaises(HTTPException) as error:
            reopened.get('f'*32)
        self.assertEqual(404,error.exception.status_code)

    async def test_strict_route_validation_rejects_invalid_coordinates_and_duplicates(self):
        invalid = [route_payload(name=' '),route_payload(frame_id='odom'),route_payload(points=[]),
                   route_payload(points=route_payload()['points'][:1]*13),route_payload(extra='ignored')]
        for field,value in [('x',float('nan')),('y',float('inf')),('x',1001),('yaw',4),('name',' ')]:
            point = dict(route_payload()['points'][0],**{field:value})
            invalid.append(route_payload(points=[point]))
        points = route_payload()['points']
        invalid.extend([route_payload(points=[points[0],dict(points[1],name=' Door ')]),
                        route_payload(points=[points[0],dict(points[0],name='Other')])])
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    product.SearchRoute(**payload)
        self.assertFalse(self.preparation.store.path.exists())
        self.assertEqual([],self.backups)

    async def test_route_capacity_is_enforced_without_rewriting_file(self):
        for number in range(20):
            self.preparation.store.save(product.SearchRoute(**route_payload(name='Area '+str(number))))
        before = self.preparation.store.path.read_bytes()
        with self.assertRaisesRegex(ValueError,'20'):
            self.preparation.store.save(product.SearchRoute(**route_payload()))
        self.assertEqual(before,self.preparation.store.path.read_bytes())
        self.assertEqual(20,len(self.backups))

    async def test_reports_are_backed_up_and_restored_after_reload(self):
        store = self.preparation.store
        report = {'mode':'simulation','motion_enabled':False,'ok':True,'found':False}
        store.report(report)
        path = store.directory/'last_report.json'
        before = path.read_bytes()
        replacement = dict(report,ok=False,error='Mock failure')
        store.report(replacement)
        self.assertEqual((path,before),self.backups[-1])
        self.assertEqual(replacement,product.RouteStore(store.directory).last_report)
        self.assertEqual([],list(self.root.rglob('*.tmp.json')))

    async def test_rehearsal_is_ordered_persisted_and_contains_no_fake_discovery(self):
        response = await self.client.post('/patrol/routes',json=route_payload())
        self.assertEqual(200,response.status_code)
        saved = response.json()
        response = await self.client.post('/patrol/rehearse',json={'route_id':saved['id']})
        self.assertEqual(200,response.status_code)
        report = response.json()
        self.assertTrue(report['ok'])
        self.assertFalse(report['found'])
        self.assertFalse(report['motion_enabled'])
        self.assertFalse(report['physical_execution'])
        self.assertNotIn('evidence',report)
        self.assertEqual(['Door','Window','Hall'],[step['point'] for step in report['steps'] if step['state']=='arrived'])
        self.assertEqual(report,product.RouteStore(self.preparation.store.directory).last_report)
        status = (await self.client.get('/patrol/status')).json()
        self.assertFalse(status['live_ready'])
        self.assertFalse(status['motion_enabled'])
        self.assertEqual(report,status['last_report'])

    async def test_live_run_returns_409_and_never_calls_navigation_or_subprocess(self):
        with patch.object(product.PatrolRunner,'run',AsyncMock(side_effect=AssertionError('Runner must not execute'))) as run, \
             patch.object(product.RehearsalNavigation,'go',AsyncMock(side_effect=AssertionError('Navigation must not execute'))) as go, \
             patch.object(product.asyncio,'create_subprocess_exec',AsyncMock(side_effect=AssertionError('No subprocess'))) as subprocess:
            response = await self.client.post('/patrol/run',json={'route_id':'a'*32})
            self.assertEqual(409,response.status_code)
            self.assertEqual(product.BLOCKED,response.json()['detail'])
            run.assert_not_awaited()
            go.assert_not_awaited()
            subprocess.assert_not_awaited()
        self.assertEqual([],self.backups)
        self.assertFalse(self.preparation.store.path.exists())

    async def test_api_rejects_invalid_route_and_id_without_writes(self):
        for payload in [route_payload(frame_id='odom'),route_payload(points=[]),route_payload(extra=True)]:
            response = await self.client.post('/patrol/routes',json=payload)
            self.assertEqual(422,response.status_code)
        for payload in [{'route_id':'bad'},{'route_id':'a'*32,'execute':True}]:
            response = await self.client.post('/patrol/run',json=payload)
            self.assertEqual(422,response.status_code)
        self.assertEqual([],self.backups)


if __name__ == '__main__':
    unittest.main(verbosity=2)
