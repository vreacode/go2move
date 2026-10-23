"""Reference selection and deletion APIs, with no robot control calls."""
import asyncio
from fastapi import HTTPException
from fastapi.responses import Response


def attach_target_actions(app, searcher, scanner):
    @app.get('/targets')
    async def targets():
        listing = searcher.targets.list()
        events = searcher.history.export()['items']
        for target in listing['items']:
            matches = [event for event in events if event['target_id'] == target['id']]
            target['discovery_count'] = len(matches)
            target['last_found_at'] = max((event['last_seen_at'] for event in matches), default=None)
            target['selected'] = target['id'] == (searcher.target_info or {}).get('id')
        return listing

    @app.get('/targets/{target_id}/image.jpg')
    async def image(target_id: str):
        try:
            return Response(searcher.targets.image(target_id), media_type='image/jpeg',
                            headers={'Cache-Control':'no-store'})
        except FileNotFoundError as exc:
            raise HTTPException(404, detail=str(exc)) from exc

    @app.post('/targets/{target_id}/select')
    async def select(target_id: str):
        try:
            searcher.targets.get(target_id)
            await scanner.stop()
            return await asyncio.get_running_loop().run_in_executor(None, searcher.select_target, target_id)
        except FileNotFoundError as exc:
            raise HTTPException(404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(503, detail='目标读取或保存失败，请稍后重试。') from exc

    @app.delete('/targets/{target_id}')
    async def delete(target_id: str):
        try:
            # The searcher lock also excludes in-flight inference commits.
            with searcher.lock:
                if (searcher.target_info or {}).get('id') == target_id:
                    from go2_platform_write import backup
                    from person_search import RUNTIME
                    for name in ('target.jpg', 'target.json'):
                        backup(RUNTIME/name)
                result = searcher.targets.delete(target_id)
                if (searcher.target_info or {}).get('id') == target_id:
                    searcher.clear_target()
                return result
        except FileNotFoundError as exc:
            raise HTTPException(404, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(503, detail='备份或删除失败，操作未完成。') from exc
