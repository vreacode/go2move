"""Persistent local appearance-match evidence; images and SQLite metadata."""
from pathlib import Path
from contextlib import contextmanager
import json
import re
import sqlite3
import threading
import tempfile
import time
import uuid


class PersonHistory:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.database = self.directory/'history.sqlite3'
        self.lock = threading.RLock()
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, target_id TEXT NOT NULL, target_name TEXT NOT NULL,
                found_at REAL NOT NULL, last_seen_at REAL NOT NULL, snapshot_at REAL NOT NULL,
                similarity REAL NOT NULL, observations INTEGER NOT NULL,
                track_id INTEGER NOT NULL, source TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'suspected',
                reviewed_at REAL, review_note TEXT NOT NULL DEFAULT '',
                score_kind TEXT NOT NULL DEFAULT 'appearance_similarity',
                target_kind TEXT NOT NULL DEFAULT 'photo', description TEXT,
                color_label TEXT, camera_source TEXT, depth_json TEXT, deleted_at REAL)''')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(events)')}
            additions = {
                'reviewed_at':'REAL', 'review_note':"TEXT NOT NULL DEFAULT ''",
                'score_kind':"TEXT NOT NULL DEFAULT 'appearance_similarity'",
                'target_kind':"TEXT NOT NULL DEFAULT 'photo'",
                'description':'TEXT', 'color_label':'TEXT','camera_source':'TEXT','depth_json':'TEXT',
                'deleted_at':'REAL'}
            missing = [(name,definition) for name,definition in additions.items() if name not in columns]
            if missing:
                self._backup_migration(db)
                db.execute('BEGIN IMMEDIATE')
                columns = {row['name'] for row in db.execute('PRAGMA table_info(events)')}
                for name,definition in missing:
                    if name not in columns:
                        db.execute('ALTER TABLE events ADD COLUMN '+name+' '+definition)

    def _backup_migration(self, source):
        # Never copy a live SQLite/WAL file directly. The backup API produces a
        # consistent standalone database before the established backup logger.
        from go2_platform_write import backup
        with tempfile.TemporaryDirectory(prefix='.history-before-review-',dir=str(self.directory)) as directory:
            snapshot = Path(directory)/self.database.name
            destination = sqlite3.connect(str(snapshot))
            try:
                source.backup(destination)
            finally:
                destination.close()
            backup(snapshot)


    @contextmanager
    def connect(self):
        db = sqlite3.connect(str(self.database), timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def metadata(row):
        item = dict(row)
        item.pop('deleted_at', None)
        item.setdefault('status','suspected')
        item.setdefault('reviewed_at',None)
        item.setdefault('review_note','')
        item.setdefault('score_kind','appearance_similarity')
        item.setdefault('target_kind','photo')
        item.setdefault('description',None)
        item.setdefault('color_label',None)
        item.setdefault('camera_source',None)
        item['depth']=json.loads(item.pop('depth_json',None) or 'null')
        item['review_status'] = item['status']
        item['note'] = item['review_note']
        item['target_description'] = item['description']
        item['score'] = item['similarity']
        item['match_semantics'] = ('clothing_color_candidate' if item['score_kind']=='color_fraction'
                                   or item['target_kind']=='description' else 'appearance_candidate')
        item['automatic_identity_confirmation'] = False
        item['snapshot_url'] = '/history/%s/snapshot.jpg' % item['id']
        item['target_url'] = '/history/%s/target.jpg' % item['id']
        return item

    def record(self, target, candidate, captured_at, snapshot, reference, event_id=None):
        """Merge one continuous encounter, or create a durable new discovery."""
        with self.lock, self.connect() as db:
            row = db.execute('SELECT * FROM events WHERE id=? AND deleted_at IS NULL', (event_id,)).fetchone() if event_id else None
            if row is not None and row['target_id'] != target['id']:
                row = None
            if row is None:
                event_id = uuid.uuid4().hex
                folder = self.directory/event_id
                folder.mkdir()
                (folder/'snapshot.jpg').write_bytes(snapshot)
                (folder/'target.jpg').write_bytes(reference)
                db.execute('''INSERT INTO events
                    (id,target_id,target_name,found_at,last_seen_at,snapshot_at,similarity,observations,track_id,source,
                     score_kind,target_kind,description,color_label,camera_source,depth_json)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (event_id,target['id'],target['name'],captured_at,captured_at,captured_at,
                     candidate['similarity'],1,candidate['track_id'],'live_camera',
                     candidate.get('score_kind','appearance_similarity'),target.get('kind','photo'),
                     target.get('description'),target.get('color_label'),candidate.get('camera_source'),
                     json.dumps(candidate.get('depth'),allow_nan=False)))
            else:
                if candidate['similarity'] > row['similarity']:
                    path = self.directory/event_id/'snapshot.jpg'
                    temporary = path.with_suffix('.tmp.jpg')
                    temporary.write_bytes(snapshot)
                    temporary.replace(path)
                    db.execute('UPDATE events SET camera_source=?,depth_json=? WHERE id=?',
                        (candidate.get('camera_source'),json.dumps(candidate.get('depth'),allow_nan=False),event_id))
                db.execute('''UPDATE events SET last_seen_at=?,snapshot_at=?,similarity=?,observations=observations+1,
                    track_id=? WHERE id=?''',
                    (max(captured_at,row['last_seen_at']),captured_at if candidate['similarity'] > row['similarity'] else row['snapshot_at'],
                     max(candidate['similarity'],row['similarity']),
                     candidate['track_id'],event_id))
            db.commit()
            return self.metadata(db.execute('SELECT * FROM events WHERE id=?',(event_id,)).fetchone())

    def list(self, limit=20):
        with self.lock, self.connect() as db:
            rows = db.execute('SELECT * FROM events WHERE deleted_at IS NULL ORDER BY last_seen_at DESC LIMIT ?',
                              (max(1,min(int(limit),100)),)).fetchall()
            count = db.execute('SELECT COUNT(*) FROM events WHERE deleted_at IS NULL').fetchone()[0]
        return {'items':[self.metadata(row) for row in rows], 'total':count}

    @staticmethod
    def _validate_id(event_id):
        if not isinstance(event_id,str) or not re.fullmatch(r'[0-9a-f]{32}',event_id):
            raise FileNotFoundError('记录不存在。')

    def get(self, event_id):
        self._validate_id(event_id)
        with self.lock, self.connect() as db:
            row = db.execute('SELECT * FROM events WHERE id=? AND deleted_at IS NULL',(event_id,)).fetchone()
            if row is None:
                raise FileNotFoundError('记录不存在。')
            return self.metadata(row)

    def review(self, event_id, status, note=''):
        self._validate_id(event_id)
        if status not in ('confirmed','rejected','suspected'):
            raise ValueError('审核状态无效。')
        if not isinstance(note,str) or len(note)>240:
            raise ValueError('备注应为最多240字的文本。')
        with self.lock, self.connect() as db:
            if db.execute('SELECT id FROM events WHERE id=? AND deleted_at IS NULL',(event_id,)).fetchone() is None:
                raise FileNotFoundError('记录不存在。')
            db.execute('UPDATE events SET status=?,reviewed_at=?,review_note=? WHERE id=?',
                       (status,time.time(),note,event_id))
            return self.metadata(db.execute('SELECT * FROM events WHERE id=?',(event_id,)).fetchone())

    def export(self, limit=1000):
        limit = max(1,min(int(limit),1000))
        with self.lock, self.connect() as db:
            rows = db.execute('SELECT * FROM events WHERE deleted_at IS NULL ORDER BY last_seen_at DESC,id DESC LIMIT ?',
                              (limit,)).fetchall()
            count = db.execute('SELECT COUNT(*) FROM events WHERE deleted_at IS NULL').fetchone()[0]
        return {'schema_version':1, 'exported_at':time.time(), 'total':count,
                'items':[self.metadata(row) for row in rows], 'truncated':count>len(rows),
                'match_semantics':'candidate_evidence', 'automatic_identity_confirmation':False,
                'score_semantics':{'appearance_similarity':'外观相似度候选，须人工核对。',
                                   'color_fraction':'衣着颜色占比候选，须人工核对。'}}

    def image(self, event_id, kind):
        if not re.fullmatch(r'[0-9a-f]{32}',event_id) or kind not in ('snapshot','target'):
            raise FileNotFoundError('记录不存在。')
        with self.lock, self.connect() as db:
            if db.execute('SELECT id FROM events WHERE id=? AND deleted_at IS NULL',(event_id,)).fetchone() is None:
                raise FileNotFoundError('记录不存在。')
            return (self.directory/event_id/(kind+'.jpg')).read_bytes()

    def delete(self, event_id):
        self._validate_id(event_id)
        with self.lock, self.connect() as db:
            if db.execute('SELECT id FROM events WHERE id=? AND deleted_at IS NULL', (event_id,)).fetchone() is None:
                raise FileNotFoundError('记录不存在。')
            self._backup_migration(db)
            db.execute('UPDATE events SET deleted_at=? WHERE id=?', (time.time(), event_id))
        return {'deleted':True, 'id':event_id}
