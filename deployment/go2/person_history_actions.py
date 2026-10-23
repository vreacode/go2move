"""Manual review and metadata export; no robot or inference side effects."""
from typing import Literal
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


class HistoryReview(BaseModel):
    model_config = {'extra':'forbid'}
    status: Literal['confirmed','rejected','suspected']
    note: str = Field(default='',max_length=240)


def attach_history_actions(app, searcher):
    # Resolve lazy history inside requests; attaching endpoints changes no data.
    @app.post('/history/{event_id}/review')
    async def history_review(event_id: str, payload: HistoryReview):
        try:
            return searcher.history.review(event_id,payload.status,payload.note)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404,detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400,detail=str(exc)) from exc

    @app.get('/history/export.json')
    async def history_export():
        return JSONResponse(searcher.history.export(),headers={
            'Cache-Control':'no-store',
            'Content-Disposition':'attachment; filename="person-history.json"'})

    @app.delete('/history/{event_id}')
    async def history_delete(event_id: str):
        try:
            with searcher.lock:
                result = searcher.history.delete(event_id)
                task = searcher.tasks.current or {}
                if searcher.current_event_id == event_id or task.get('found_event_id') == event_id:
                    searcher.tasks.finish('stopped', '本次发现记录已删除。')
                    searcher.generation += 1
                    searcher.active = False
                    searcher.last = searcher.preview_bytes = None
                    searcher.current_event_id = searcher.current_event_track_id = None
                return result
        except FileNotFoundError as exc:
            raise HTTPException(404, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(503, detail='备份或删除失败，操作未完成。') from exc
