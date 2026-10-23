"""Explicit camera selection; changing source requires restarting person search."""
import json
from pathlib import Path

BASE=Path(__file__).resolve().parent
CONFIG=BASE/'config/person_camera.json'
SOURCES={'native':BASE/'runtime/native_camera','realsense':BASE/'runtime/rgbd_camera'}


def camera_directory():
    source=json.loads(CONFIG.read_text()).get('source') if CONFIG.exists() else 'native'
    if source not in SOURCES:
        raise ValueError('找人相机须为native或realsense。')
    return SOURCES[source]


def select_camera(source):
    """Validate the new stream before changing the existing search source."""
    import time
    import urllib.request
    from person_depth import load_depth
    from go2_platform_write import write
    if source not in SOURCES:
        raise ValueError('Unsupported camera source')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open('http://127.0.0.1:8768/status',timeout=3) as response:
            if json.load(response).get('active'):
                raise ValueError('请先结束当前找人任务，再切换相机。')
    except OSError:
        pass
    directory=SOURCES[source];deadline=time.monotonic()+25;error=None
    while time.monotonic()<deadline:
        try:
            status=json.loads((directory/'status.json').read_text());raw=(directory/'latest.jpg').read_bytes()
            if not status.get('running') or not 0<=time.time()-status.get('captured_at',0)<=2:
                raise ValueError('等待新鲜相机画面。')
            if source=='realsense' and load_depth(directory,status,raw,time.time()) is None:
                raise ValueError('等待对齐深度。')
            write(CONFIG,json.dumps({'source':source},indent=2)+'\n')
            return
        except (OSError,ValueError,KeyError) as exc:
            error=exc;time.sleep(.1)
    raise ValueError('相机未就绪，原来源保持：'+str(error))


if __name__=='__main__':
    import sys
    if len(sys.argv)!=2:raise SystemExit('Usage: person_camera.py native|realsense')
    select_camera(sys.argv[1]);print('Selected camera: '+sys.argv[1])
