#!/usr/bin/env python3
"""Dashboard scan proxy checks with in-process fake backend only."""
import sys
import unittest
from unittest.mock import patch
import httpx
sys.path.insert(0,'/home/unitree/go2move/apps/nano-deploy')
from dashboard_platform.server import app as product

class ScanProxyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls=[]
        self.state={'state':'blocked','available':False,'physical_validated':False,
                    'reason':'温度与事故暂停，需后续现场验证。','angle_deg':0,'max_angle_deg':90}
        async def fake_backend(method,path,payload=None):
            self.calls.append((method,path,payload))
            if path=='/scan/start':
                raise product.HTTPException(status_code=409,detail=self.state['reason'])
            return httpx.Response(200,json=self.state,request=httpx.Request(method,'http://fake'+path))
        self.patcher=patch.object(product,'go2_person_request',side_effect=fake_backend)
        self.patcher.start()
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=product.app),base_url='http://isolated')
        self.auth={'Authorization':'Bearer '+product.settings.auth_token}

    async def asyncTearDown(self):
        await self.client.aclose()
        self.patcher.stop()

    async def test_all_scan_routes_require_authentication(self):
        for action in ('status','start','stop'):
            method='GET' if action=='status' else 'POST'
            response=await self.client.request(method,'/api/go2/person/scan/'+action,
                json={'confirmed':True} if action=='start' else None)
            self.assertEqual(401,response.status_code)
        self.assertEqual([],self.calls)

    async def test_status_keeps_pause_and_physical_validation_fields(self):
        response=await self.client.get('/api/go2/person/scan/status',headers=self.auth)
        self.assertEqual(200,response.status_code)
        self.assertEqual(self.state,response.json())
        self.assertEqual([('GET','/scan/status',None)],self.calls)

    async def test_confirmation_validation_and_backend_409_are_preserved(self):
        for body in ({},{'confirmed':False},{'confirmed':True,'angle_deg':360}):
            response=await self.client.post('/api/go2/person/scan/start',headers=self.auth,json=body)
            self.assertEqual(422,response.status_code)
        self.assertEqual([],self.calls)
        response=await self.client.post('/api/go2/person/scan/start',headers=self.auth,json={'confirmed':True})
        self.assertEqual(409,response.status_code)
        self.assertEqual(self.state['reason'],response.json()['detail'])
        self.assertEqual([('POST','/scan/start',{'confirmed':True})],self.calls)

    async def test_stop_forwards_only_stop_without_voice_or_start(self):
        response=await self.client.post('/api/go2/person/scan/stop',headers=self.auth,json={})
        self.assertEqual(200,response.status_code)
        self.assertEqual([('POST','/scan/stop',None)],self.calls)

if __name__=='__main__':
    unittest.main(verbosity=2)
