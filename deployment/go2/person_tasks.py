"""Small durable stationary-search task log. Never resumes tasks after restart."""
from copy import deepcopy
import json
from pathlib import Path
import threading
import time
import uuid

OPEN_STATES = {'running', 'found_pending_confirmation'}


class SearchTasks:
    def __init__(self, path, clock=time.time):
        self.path, self.clock, self.lock = Path(path), clock, threading.RLock()
        self.items = []
        if self.path.is_file():
            data = json.loads(self.path.read_text())
            if not isinstance(data, dict) or not isinstance(data.get('items'), list):
                raise ValueError('查找任务日志格式无效。')
            self.items = data['items'][:200]
            changed = False
            for item in self.items:
                if item.get('state') in OPEN_STATES and item.get('finished_at') is None:
                    self._transition(item, 'interrupted', '服务重启，任务未自动恢复。', finish=True)
                    changed = True
            if changed:
                self._save()

    def _save(self):
        from go2_platform_write import backup
        from go2_health_monitor import write_atomic
        backup(self.path)
        write_atomic(self.path, {'version': 1, 'items': self.items})

    def _transition(self, item, state, reason, finish=False):
        now = self.clock()
        item.update(state=state, updated_at=now, reason=reason)
        if finish:
            item['finished_at'] = now
        item.setdefault('events', []).append({'state': state, 'at': now, 'reason': reason})
        item['events'] = item['events'][-20:]

    @property
    def current(self):
        with self.lock:
            return deepcopy(self.items[0]) if self.items else None

    def start(self, target, timeout_seconds=120):
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 30 <= timeout_seconds <= 600:
            raise ValueError('任务时限须为30到600秒。')
        with self.lock:
            if self.items and self.items[0]['state'] in OPEN_STATES and self.items[0].get('finished_at') is None:
                self._transition(self.items[0], 'stopped', '新任务替代。', finish=True)
            now = self.clock()
            item = {'id': uuid.uuid4().hex, 'state': 'running', 'target_id': target['id'],
                    'target_name': target['name'], 'target_kind': target.get('kind', 'photo'),
                    'description': target.get('description'), 'started_at': now, 'updated_at': now,
                    'deadline_at': now+timeout_seconds, 'timeout_seconds': timeout_seconds,
                    'finished_at': None, 'found_event_id': None, 'reason': '正在观察实时画面。',
                    'motion_enabled': False, 'events': [{'state': 'running', 'at': now}]}
            self.items.insert(0, item)
            self.items = self.items[:200]
            self._save()
            return deepcopy(item)

    def finish(self, state='stopped', reason='用户停止查找。'):
        if state not in {'stopped', 'timed_out', 'interrupted', 'completed'}:
            raise ValueError('Unsupported terminal task state')
        with self.lock:
            if not self.items or self.items[0]['state'] not in OPEN_STATES or self.items[0].get('finished_at') is not None:
                return False
            self._transition(self.items[0], state, reason, finish=True)
            self._save()
            return True

    def found(self, event):
        with self.lock:
            if (not self.items or self.items[0]['state'] not in OPEN_STATES or
                    self.items[0].get('finished_at') is not None or event.get('status') == 'rejected'):
                return
            item = self.items[0]
            if item['state'] == 'found_pending_confirmation' and item.get('found_event_id') == event['id']:
                return
            item['found_event_id'] = event['id']
            self._transition(item, 'found_pending_confirmation', '发现候选，等待人工确认。')
            self._save()

    def expire(self):
        with self.lock:
            if not self.items:
                return False
            item = self.items[0]
            if item['state'] not in OPEN_STATES or item.get('finished_at') is not None or self.clock() < item['deadline_at']:
                return False
            if item['state'] == 'found_pending_confirmation':
                self._transition(item, item['state'], '查找时限已结束，发现记录仍待人工确认。', finish=True)
            else:
                self._transition(item, 'timed_out', '查找时限已结束。', finish=True)
            self._save()
            return True

    def apply_review(self, status):
        """Only explicit human review completes a task; ended tasks never resume."""
        with self.lock:
            if not self.items or self.items[0]['state'] != 'found_pending_confirmation':
                return None
            item = self.items[0]
            if status == 'confirmed':
                self._transition(item, 'completed', '发现记录已由人工确认。', finish=True)
                self._save()
                return 'completed'
            if status == 'rejected':
                ended = item.get('finished_at') is not None or self.clock() >= item['deadline_at']
                if ended:
                    self._transition(item, 'stopped', '候选已由人工否认，已结束任务不恢复。', finish=True)
                else:
                    item['found_event_id'] = None
                    self._transition(item, 'running', '候选已由人工否认，继续当前任务。')
                self._save()
                return 'stopped' if ended else 'running'
            return None

    def list(self, limit=20):
        with self.lock:
            return {'items': deepcopy(self.items[:max(1, min(100, int(limit)))]),
                    'total': len(self.items), 'current': deepcopy(self.items[0]) if self.items else None}
