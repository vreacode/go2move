"""Authenticated patrol preparation proxies; fake backend, no ROS/robot I/O."""
import sys
from pathlib import Path
import unittest
from unittest.mock import patch
import httpx

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'apps/nano-deploy'))
from dashboard_platform.server import app as product


class PreparationApi(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls=[]
        async def backend(method,path,payload=None):
            self.calls.append((method,path,payload))
            request=httpx.Request(method,'http://fake'+path)
            if path.endswith('.png'):
                return httpx.Response(200,content=b'\x89PNG\r\n\x1a\nFAKE',request=request)
            return httpx.Response(200,json={'mode':'simulation','motion_enabled':False,'physical_execution':False,
                                          'generation':{'live_localization':False}},request=request)
        self.patcher=patch.object(product,'go2_person_request',side_effect=backend);self.patcher.start()
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=product.app),base_url='http://test',trust_env=False)
        self.auth={'Authorization':'Bearer '+product.settings.auth_token}

    async def asyncTearDown(self):
        await self.client.aclose();self.patcher.stop()

    async def test_new_routes_all_require_auth(self):
        for method,path,payload in [('GET','maps',None),('GET','maps/end-of-day-20261006/preview.png',None),
            ('POST','suggest',{'map_id':'end-of-day-20261006'}),('GET','execution/status',None),
            ('POST','execution/rehearse',{'route_id':'a'*32}),('POST','execution/cancel',None)]:
            response=await self.client.request(method,'/api/go2/person/patrol/'+path,json=payload)
            self.assertEqual(401,response.status_code)
        self.assertEqual([],self.calls)

    async def test_suggestion_carries_bounded_defaults_without_motion(self):
        response=await self.client.post('/api/go2/person/patrol/suggest',json={'map_id':'end-of-day-20261006'},headers=self.auth)
        self.assertEqual(200,response.status_code)
        self.assertEqual([('POST','/patrol/suggest',{'map_id':'end-of-day-20261006','count':3,'radius_m':2.0})],self.calls)
        self.assertFalse(response.json()['generation']['live_localization'])

    async def test_bad_paths_and_unbounded_fields_never_forward(self):
        for body in [{'map_id':'../secret'}, {'map_id':'m','count':7}, {'map_id':'m','radius_m':.1},
                     {'map_id':'m','radius_m':6}, {'map_id':'m','armed':True}]:
            response=await self.client.post('/api/go2/person/patrol/suggest',json=body,headers=self.auth)
            self.assertEqual(422,response.status_code)
        response=await self.client.get('/api/go2/person/patrol/maps/bad.dot/preview.png',headers=self.auth)
        self.assertEqual(400,response.status_code)
        self.assertEqual([],self.calls)

    async def test_map_preview_is_no_store_authenticated_png(self):
        response=await self.client.get('/api/go2/person/patrol/maps/m/preview.png',headers=self.auth)
        self.assertEqual('image/png',response.headers['content-type'])
        self.assertEqual('no-store',response.headers['cache-control'])
        self.assertTrue(response.content.startswith(b'\x89PNG'))

    async def test_only_explicit_simulation_scenarios_can_start(self):
        for scenario in ('not_found','found','navigation_failed'):
            response=await self.client.post('/api/go2/person/patrol/execution/rehearse',headers=self.auth,
                json={'route_id':'b'*32,'scenario':scenario})
            self.assertEqual(202,response.status_code)
            self.assertFalse(response.json()['physical_execution'])
        count=len(self.calls)
        response=await self.client.post('/api/go2/person/patrol/execution/rehearse',headers=self.auth,
            json={'route_id':'b'*32,'scenario':'live'})
        self.assertEqual(422,response.status_code);self.assertEqual(count,len(self.calls))

    async def test_cancel_only_targets_simulation_controller(self):
        response=await self.client.post('/api/go2/person/patrol/execution/cancel',headers=self.auth)
        self.assertEqual(200,response.status_code)
        self.assertEqual([('POST','/patrol/execution/cancel',None)],self.calls)


if __name__=='__main__':
    unittest.main(verbosity=2)
