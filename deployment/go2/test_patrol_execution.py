#!/usr/bin/env python3
"""Temporary route files and fake action/observer tests; no ROS, SDK or network."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
import httpx
import patrol_execution as product
from person_patrol import RouteStore, SearchRoute


def route(points=3):
    return {'id':'a'*32,'name':'Synthetic area','frame_id':'map','points':[
        {'name':'Point '+str(index),'x':float(index),'y':0.,'yaw':0.} for index in range(points)]}


class Navigation:
    def __init__(self, log, blocked=False, ignore_cancel=False, cancel_blocked=False,
                 cancel_result=True, cancel_error=None, stable=True):
        self.log,self.blocked,self.ignore_cancel = log,blocked,ignore_cancel
        self.cancel_blocked,self.cancel_result,self.cancel_error = cancel_blocked,cancel_result,cancel_error
        self.stable = stable
        self.entered,self.release,self.cancel_entered,self.cancel_release = [asyncio.Event() for _ in range(4)]
        self.cancel_count = 0

    async def go(self, point):
        self.log.append(('go',point['name']))
        self.entered.set()
        if self.blocked:
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                if not self.ignore_cancel:raise
                await self.release.wait()
        return True

    async def settle(self, point):
        self.log.append(('settle',point['name']))
        return self.stable

    async def cancel(self):
        self.cancel_count += 1
        self.log.append(('cancel',None))
        self.cancel_entered.set()
        if self.cancel_blocked:await self.cancel_release.wait()
        if self.cancel_error:raise self.cancel_error
        return self.cancel_result


class Observation:
    def __init__(self, log, matched=False, stale=False, target=None, sequence=None, blocked=False, ignore_cancel=False):
        self.log,self.matched,self.stale,self.target = log,matched,stale,target
        self.sequence=list(sequence or [])
        self.blocked,self.ignore_cancel=blocked,ignore_cancel
        self.entered,self.release=asyncio.Event(),asyncio.Event()
        self.calls = 0

    async def observe(self, point, target_id):
        self.log.append(('observe',point['name']))
        self.calls += 1
        self.entered.set()
        if self.blocked:
            try:await self.release.wait()
            except asyncio.CancelledError:
                if not self.ignore_cancel:raise
                await self.release.wait()
        matched,track = self.sequence.pop(0) if self.sequence else (self.matched,1)
        return {'target_id':self.target or target_id,'captured_at':time.time()-(3 if self.stale else 0),
                'suspected_target':matched,'track_id':track,'synthetic':True}


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='go2-execution-test-')
        self.root = Path(self.temporary.name)
        self.backups,self.updates,self.log,self.controllers = [],[],[],[]
        def collect(path):
            path=Path(path)
            self.assertTrue(self.root in path.parents,'Never touch real runtime')
            self.backups.append((path,path.read_bytes() if path.exists() else None))
        self.backup=patch('go2_platform_write.backup',collect)
        self.backup.start()

    async def asyncTearDown(self):
        for controller in self.controllers:
            if controller.task and not controller.task.done():
                await controller.cancel()
            if controller.cleanup and not controller.cleanup.done():
                controller.cleanup.cancel()
                try:await controller.cleanup
                except asyncio.CancelledError:pass
        self.backup.stop()
        self.temporary.cleanup()

    def controller(self, navigator=None, observer=None, **changes):
        navigator=navigator or Navigation(self.log)
        observer=observer or Observation(self.log)
        values={'update':lambda value:self.updates.append(value),'navigator_factory':lambda _:navigator,
                'observer_factory':lambda _:observer,'navigation_timeout':.15,'observation_timeout':.15,
                'observation_seconds':.015,'tick_seconds':.001,'cancel_timeout':.15}
        values.update(changes)
        directory=values.pop('directory',self.root)
        value=product.ExecutionController(directory,**values)
        self.controllers.append(value)
        return value,navigator,observer

    def assert_simulation(self, status):
        self.assertEqual('simulation',status['mode'])
        self.assertTrue(status['simulation'])
        self.assertFalse(status['physical_execution'])
        self.assertFalse(status['motion_enabled'])
        self.assertFalse(status['automatic_resume'])

    async def test_sequential_arrival_settle_observe_then_next_and_durable_progress(self):
        controller,navigation,_=self.controller()
        await controller.start(route(),'target','not_found')
        result=await controller.wait()
        self.assertEqual('not_found',result['state'])
        self.assertEqual(3,result['index'])
        self.assertFalse(result['active'])
        self.assertEqual(1,navigation.cancel_count)
        for index in range(3):
            name='Point '+str(index)
            first=self.log.index(('go',name))
            settled=self.log.index(('settle',name))
            observed=self.log.index(('observe',name))
            self.assertLess(first,settled)
            self.assertLess(settled,observed)
            if index<2:self.assertLess(observed,self.log.index(('go','Point '+str(index+1))))
        self.assertTrue(any(value['state']=='settling' for value in self.updates))
        self.assertTrue(any(value['state']=='observing' for value in self.updates))
        self.assertTrue(all(value['active'] for value in self.updates if value['state'] in product.BUSY))
        self.assert_simulation(result)
        self.assertEqual(result,json.loads(controller.path.read_text()))
        self.assertEqual(None,self.backups[0][1])
        self.assertTrue(any(previous is not None for _,previous in self.backups[1:]))
        copy=controller.status();copy['steps'].clear()
        self.assertTrue(controller.status()['steps'],'Status cannot expose mutable internal data')

    async def test_three_consecutive_same_track_frames_confirm_synthetic_and_early_stop(self):
        observer=Observation(self.log,sequence=[(True,1),(True,1),(False,1),(True,2),(True,2),(True,2)])
        controller,navigation,_=self.controller(observer=observer,observation_seconds=.08)
        await controller.start(route(),'target','found')
        result=await controller.wait()
        self.assertEqual('found',result['state'])
        self.assertTrue(result['found'])
        self.assertEqual(6,observer.calls)
        self.assertEqual(3,result['evidence']['confirmation_frames'])
        self.assertTrue(result['evidence']['synthetic'])
        self.assertFalse(result['evidence']['identity_confirmed'])
        self.assertEqual('simulation',result['evidence']['source'])
        self.assertEqual([('go','Point 0')],[item for item in self.log if item[0]=='go'])
        self.assert_simulation(result)

    async def test_default_scenarios_have_no_real_io_and_navigation_failure_never_observes(self):
        for scenario,expected in [('not_found','not_found'),('found','found'),('navigation_failed','failed')]:
            controller=product.ExecutionController(self.root/scenario,observation_seconds=.08,tick_seconds=.001)
            self.controllers.append(controller)
            await controller.start(route(1),'simulation-only',scenario)
            result=await controller.wait()
            self.assertEqual(expected,result['state'])
            self.assert_simulation(result)
            if scenario=='navigation_failed':
                self.assertFalse(any(step['state']=='observing' for step in result['steps']))
            if scenario=='found':self.assertTrue(result['evidence']['synthetic'])
        self.assertEqual([],list(self.root.rglob('*.jpg')))
        self.assertEqual([],list(self.root.rglob('*.sqlite3')))

    async def test_concurrent_starts_allow_exactly_one_task_and_keep_route_snapshot(self):
        navigator=Navigation(self.log,blocked=True)
        controller,_,_=self.controller(navigator=navigator)
        original=route()
        results=await asyncio.gather(controller.start(original,'target'),controller.start(original,'target'),
                                     return_exceptions=True)
        self.assertEqual(1,sum(isinstance(value,dict) for value in results))
        conflicts=[value for value in results if isinstance(value,HTTPException)]
        self.assertEqual([409],[value.status_code for value in conflicts])
        original['points'].clear()
        self.assertEqual(3,controller.status()['total'])
        await navigator.entered.wait()
        self.assertEqual('Point 0',controller.status()['point'])
        self.assertEqual('cancelled',(await controller.cancel())['state'])
        self.assertEqual(1,len([entry for entry in self.log if entry[0]=='go']))

    async def test_cancel_ack_waits_for_navigation_and_concurrent_cancel_cannot_interrupt_cleanup(self):
        navigator=Navigation(self.log,blocked=True,cancel_blocked=True)
        controller,_,observer=self.controller(navigator=navigator)
        await controller.start(route(),'target')
        await navigator.entered.wait()
        cancel=asyncio.create_task(controller.cancel())
        await navigator.cancel_entered.wait()
        self.assertFalse(cancel.done())
        self.assertEqual('cancelling',controller.status()['state'])
        self.assertTrue(controller.status()['active'])
        other=asyncio.create_task(controller.cancel())
        with self.assertRaises(HTTPException) as rejected:await controller.start(route(),'target')
        self.assertEqual(409,rejected.exception.status_code)
        navigator.cancel_release.set()
        first,second=await asyncio.gather(cancel,other)
        self.assertEqual('cancelled',first['state'])
        self.assertEqual(first,second)
        self.assertEqual(1,navigator.cancel_count)
        self.assertEqual(0,observer.calls)
        self.assertEqual(1,len([entry for entry in self.log if entry[0]=='go']))

    async def test_cancel_during_normal_terminal_cleanup_waits_for_same_ack(self):
        navigator=Navigation(self.log,cancel_blocked=True)
        controller,_,_=self.controller(navigator=navigator)
        await controller.start(route(1),'target')
        await navigator.cancel_entered.wait()
        cancel=asyncio.create_task(controller.cancel())
        await asyncio.sleep(.005)
        self.assertFalse(cancel.done())
        self.assertEqual('cancelling',controller.status()['state'])
        navigator.cancel_release.set()
        self.assertEqual('cancelled',(await cancel)['state'])
        self.assertEqual(1,navigator.cancel_count)

    async def test_cancelled_late_arrival_and_late_observation_cannot_continue(self):
        for phase in ['navigation','observation']:
            self.log.clear()
            navigation=Navigation(self.log,blocked=phase=='navigation',ignore_cancel=True)
            observation=Observation(self.log,matched=True,blocked=phase=='observation',ignore_cancel=True)
            controller,_,_=self.controller(navigator=navigation,observer=observation)
            await controller.start(route(),'target')
            blocked=navigation if phase=='navigation' else observation
            await blocked.entered.wait()
            cancel=asyncio.create_task(controller.cancel())
            await asyncio.sleep(.005)
            self.assertFalse(cancel.done())
            blocked.release.set()
            result=await cancel
            self.assertEqual('cancelled',result['state'])
            self.assertFalse(result['found'])
            self.assertEqual(1,len([entry for entry in self.log if entry[0]=='go']))
            if phase=='navigation':self.assertEqual(0,observation.calls)

    async def test_timeout_late_arrival_and_late_observation_never_advance(self):
        for phase in ['navigation','observation']:
            self.log.clear()
            navigation=Navigation(self.log,blocked=phase=='navigation',ignore_cancel=True)
            observation=Observation(self.log,matched=True,blocked=phase=='observation',ignore_cancel=True)
            controller,_,_=self.controller(navigator=navigation,observer=observation,
                navigation_timeout=.02,observation_timeout=.02)
            await controller.start(route(),'target')
            blocked=navigation if phase=='navigation' else observation
            await blocked.entered.wait()
            await asyncio.sleep(.04)
            blocked.release.set()
            result=await controller.wait()
            self.assertEqual('failed',result['state'])
            self.assertIn('超时',result['error'])
            self.assertEqual(1,len([entry for entry in self.log if entry[0]=='go']))
            self.assertFalse(result['found'])

    async def test_stale_or_wrong_target_observation_and_unstable_arrival_never_advance(self):
        for navigation,observation,reason in [
            (Navigation(self.log),Observation(self.log,matched=True,stale=True),'过期'),
            (Navigation(self.log),Observation(self.log,matched=True,target='replacement'),'更换'),
            (Navigation(self.log,stable=False),Observation(self.log,matched=True),'停稳')]:
            self.log.clear()
            controller,_,_=self.controller(navigator=navigation,observer=observation)
            await controller.start(route(),'target')
            result=await controller.wait()
            self.assertEqual('failed',result['state'])
            self.assertIn(reason,result['error'])
            self.assertFalse(result['found'])
            self.assertEqual(1,len([entry for entry in self.log if entry[0]=='go']))

    async def test_target_change_cancels_inflight_task_without_observation_or_next_goal(self):
        target=['target']
        navigation=Navigation(self.log,blocked=True)
        controller,_,observer=self.controller(navigator=navigation,target_provider=lambda:target[0])
        await controller.start(route(),'target')
        await navigation.entered.wait()
        target[0]='replacement'
        result=await asyncio.wait_for(controller.wait(),.3)
        self.assertEqual('failed',result['state'])
        self.assertIn('更换',result['error'])
        self.assertEqual(0,observer.calls)
        self.assertEqual(1,navigation.cancel_count)

    async def test_cancel_error_timeout_or_negative_ack_blocks_restart(self):
        for failure in ['exception','timeout','declined','cancelled_task']:
            navigation=Navigation(self.log,blocked=True,cancel_blocked=failure=='timeout',
                cancel_error=(RuntimeError('Synthetic cancel failure') if failure=='exception' else
                              asyncio.CancelledError() if failure=='cancelled_task' else None),
                cancel_result=failure!='declined')
            controller,_,_=self.controller(navigator=navigation,cancel_timeout=.02,directory=self.root/failure)
            await controller.start(route(),'target')
            await navigation.entered.wait()
            result=await controller.cancel()
            self.assertEqual('cancel_failed',result['state'])
            self.assertFalse(result['active'])
            self.assertFalse(result['motion_enabled'])
            with self.assertRaises(HTTPException):await controller.start(route(),'target')
            self.assertEqual(1,navigation.cancel_count)

    async def test_restart_marks_busy_interrupted_without_factories_and_idle_cancel_is_readonly(self):
        directory=self.root/'restore'
        directory.mkdir()
        before={'id':'b'*32,'state':'observing','route_id':'a'*32,'steps':[],'started_at':100.,
                'simulation':False,'physical_execution':True,'motion_enabled':True}
        (directory/'last_execution.json').write_text(json.dumps(before))
        def forbidden(_):raise AssertionError('Restoration cannot create any adapter')
        restored=product.ExecutionController(directory,navigator_factory=forbidden,observer_factory=forbidden)
        self.assertEqual('interrupted',restored.status()['state'])
        self.assert_simulation(restored.status())
        self.assertIsNone(restored.task)
        self.assertEqual('interrupted',json.loads(restored.path.read_text())['state'])
        idle=product.ExecutionController(self.root/'idle')
        count=len(self.backups)
        self.assertEqual('idle',(await idle.cancel())['state'])
        self.assertEqual(count,len(self.backups))
        self.assertFalse(idle.path.exists())

    async def test_asgi_contract_returns_202_simulation_and_validates_inputs(self):
        store=RouteStore(self.root/'api')
        saved=store.save(SearchRoute(**{key:route(1)[key] for key in ('name','frame_id','points')}))
        app=FastAPI()
        searcher=SimpleNamespace(target_info={'id':'existing-target'},history='untouched')
        controller=product.attach_execution(app,SimpleNamespace(store=store),searcher)
        self.controllers.append(controller)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://isolated.test') as client:
            response=await client.post('/patrol/execution/rehearse',json={'route_id':saved['id'],'scenario':'found'})
            self.assertEqual(202,response.status_code)
            self.assert_simulation(response.json())
            await controller.wait()
            status=(await client.get('/patrol/execution/status')).json()
            self.assertEqual('found',status['state'])
            self.assertTrue(status['evidence']['synthetic'])
            self.assertEqual({'id':'existing-target'},searcher.target_info)
            self.assertEqual('untouched',searcher.history)
            for payload in [{'route_id':'bad'},{'route_id':saved['id'],'scenario':'live'},
                            {'route_id':saved['id'],'motion_enabled':True}]:
                self.assertEqual(422,(await client.post('/patrol/execution/rehearse',json=payload)).status_code)
            self.assertEqual(404,(await client.post('/patrol/execution/rehearse',json={'route_id':'0'*32})).status_code)
            self.assertEqual(200,(await client.post('/patrol/execution/cancel',json={})).status_code)


class Nav2AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_waits_for_late_goal_acceptance_and_final_action_result(self):
        accepted,result,cancelled=asyncio.Event(),asyncio.Event(),asyncio.Event()
        class Handle:
            async def wait_result(self):await result.wait();return 5
            async def cancel(self):cancelled.set();return True
        class Adapter:
            async def send_goal(self,point):await accepted.wait();return Handle()
        navigator=product.Nav2Navigator(Adapter())
        go=asyncio.create_task(navigator.go(route(1)['points'][0]))
        await asyncio.sleep(0)
        go.cancel()
        with self.assertRaises(asyncio.CancelledError):await go
        cleanup=asyncio.create_task(navigator.cancel())
        await asyncio.sleep(0)
        self.assertFalse(cleanup.done())
        accepted.set()
        await cancelled.wait()
        self.assertFalse(cleanup.done(),'Acceptance of cancel is not the terminal goal acknowledgement')
        result.set()
        self.assertTrue(await cleanup)

    async def test_success_failure_and_required_stability_use_only_injected_adapter(self):
        for code,expected in [(4,True),(5,False),(6,False)]:
            class Handle:
                async def wait_result(self):return code
            class Adapter:
                async def send_goal(self,point):return Handle()
                async def wait_stable(self):return True
            navigator=product.Nav2Navigator(Adapter())
            self.assertEqual(expected,await navigator.go(route(1)['points'][0]))
            self.assertTrue(await navigator.settle({}))
            self.assertTrue(await navigator.cancel())
        navigator=product.Nav2Navigator(SimpleNamespace())
        with self.assertRaisesRegex(RuntimeError,'停稳'):await navigator.settle({})
        with self.assertRaises(ValueError):product.Nav2Navigator(None)


if __name__=='__main__':
    unittest.main(verbosity=2)
