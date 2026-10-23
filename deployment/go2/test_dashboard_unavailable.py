"""Disconnected dashboard Agent must report 503 without leaking connection data."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import httpx

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'apps/nano-deploy'))
from dashboard_platform.server import app as product


class UnavailableTests(unittest.IsolatedAsyncioTestCase):
    async def test_camera_agent_disconnect_is_clear_503_and_auth_still_required(self):
        async def fail(*args,**kwargs):raise httpx.ConnectError('private-upstream-details')
        with patch.object(product.bridge,'fetch_json',side_effect=fail):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=product.app),base_url='http://test') as c:
                self.assertEqual(401,(await c.get('/api/camera/status')).status_code)
                r=await c.get('/api/camera/status',headers={'Authorization':'Bearer '+product.settings.auth_token})
                self.assertEqual(503,r.status_code)
                self.assertIn('设备服务',r.json()['detail'])
                self.assertNotIn('private-upstream-details',r.text)


if __name__=='__main__':unittest.main(verbosity=2)
