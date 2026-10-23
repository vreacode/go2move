#!/usr/bin/env python3
"""Isolated SQLite review/export migration tests; no models, hardware or network."""
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI
import httpx
import person_history as product
from person_history_actions import attach_history_actions

LEGACY_SQL = """CREATE TABLE events (
    id TEXT PRIMARY KEY,target_id TEXT NOT NULL,target_name TEXT NOT NULL,
    found_at REAL NOT NULL,last_seen_at REAL NOT NULL,snapshot_at REAL NOT NULL,
    similarity REAL NOT NULL,observations INTEGER NOT NULL,track_id INTEGER NOT NULL,
    source TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'suspected')"""


class HistoryActionsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='go2-history-actions-test-')
        self.root = Path(self.temporary.name)
        self.directory = self.root/'history'
        self.directory.mkdir()
        self.database = self.directory/'history.sqlite3'
        self.backups = []
        def collect_snapshot(path):
            path = Path(path)
            self.assertTrue(self.root in path.parents,'Never back up the actual runtime')
            self.assertNotEqual(self.database,path,'Back up a consistent SQLite snapshot, never a live raw file')
            with sqlite3.connect(str(path)) as db:
                db.row_factory = sqlite3.Row
                self.assertEqual('ok',db.execute('PRAGMA integrity_check').fetchone()[0])
                self.backups.append({'columns':[row['name'] for row in db.execute('PRAGMA table_info(events)')],
                    'events':[dict(row) for row in db.execute('SELECT * FROM events ORDER BY id')]})
        self.backup = patch('go2_platform_write.backup',collect_snapshot)
        self.backup.start()

    async def asyncTearDown(self):
        self.backup.stop()
        self.temporary.cleanup()

    def legacy(self):
        with sqlite3.connect(str(self.database)) as db:
            db.execute(LEGACY_SQL)
            for index in [1,2]:
                event_id = str(index)*32
                db.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (event_id,'target-'+str(index),'旧记录'+str(index),1000.,1000.+index,1000.,
                     .7,1,index,'live_camera','suspected'))
                folder = self.directory/event_id
                folder.mkdir()
                (folder/'snapshot.jpg').write_bytes(b'legacy-snapshot-'+bytes([index]))
                (folder/'target.jpg').write_bytes(b'legacy-target-'+bytes([index]))

    def record(self,owner,**changes):
        target = {'id':'target-photo','name':'Reference person'}
        target.update(changes)
        candidate = {'similarity':.7,'track_id':8,'score_kind':
                     'color_fraction' if target.get('kind')=='description' else 'appearance_similarity'}
        return owner.record(target,candidate,1000.,b'snapshot',b'reference')

    async def test_legacy_two_records_migrate_after_consistent_backup_and_keep_images(self):
        self.legacy()
        owner = product.PersonHistory(self.directory)
        self.assertEqual(1,len(self.backups))
        self.assertNotIn('reviewed_at',self.backups[0]['columns'])
        self.assertEqual(['1'*32,'2'*32],[row['id'] for row in self.backups[0]['events']])
        listing = owner.list()
        self.assertEqual(2,listing['total'])
        for event in listing['items']:
            self.assertEqual('suspected',event['status'])
            self.assertEqual(event['status'],event['review_status'])
            self.assertIsNone(event['reviewed_at'])
            self.assertEqual('',event['review_note'])
            self.assertEqual('photo',event['target_kind'])
            self.assertEqual('appearance_similarity',event['score_kind'])
            self.assertEqual('appearance_candidate',event['match_semantics'])
            self.assertFalse(event['automatic_identity_confirmation'])
            self.assertEqual(.7,event['similarity'])
            self.assertTrue(owner.image(event['id'],'snapshot').startswith(b'legacy-snapshot'))
            self.assertTrue(owner.image(event['id'],'target').startswith(b'legacy-target'))
        self.assertEqual(listing,product.PersonHistory(self.directory).list())
        self.assertEqual(1,len(self.backups),'Completed migration must not repeat backups or alter rows')
        self.assertEqual([],list(self.directory.glob('.history-before-review-*')))

    async def test_wal_committed_rows_are_included_in_migration_backup(self):
        self.legacy()
        writer = sqlite3.connect(str(self.database))
        try:
            self.assertEqual('wal',writer.execute('PRAGMA journal_mode=WAL').fetchone()[0])
            writer.execute('INSERT INTO events SELECT ?,target_id,target_name,found_at,last_seen_at,snapshot_at,similarity,observations,track_id,source,status FROM events WHERE id=?',
                           ('3'*32,'1'*32))
            writer.commit()
            self.assertTrue(Path(str(self.database)+'-wal').exists())
            owner = product.PersonHistory(self.directory)
            self.assertEqual(3,owner.list()['total'])
            self.assertEqual(3,len(self.backups[0]['events']))
        finally:
            writer.close()

    async def test_partial_migration_keeps_existing_review_and_fails_closed_if_backup_fails(self):
        self.legacy()
        with sqlite3.connect(str(self.database)) as db:
            db.execute('ALTER TABLE events ADD COLUMN reviewed_at REAL')
            db.execute("ALTER TABLE events ADD COLUMN review_note TEXT NOT NULL DEFAULT ''")
            db.execute('UPDATE events SET status=?,reviewed_at=?,review_note=? WHERE id=?',
                       ('confirmed',1200.,'Existing manual review','1'*32))
        with patch('go2_platform_write.backup',side_effect=OSError('Synthetic backup failure')):
            with self.assertRaisesRegex(OSError,'backup failure'):
                product.PersonHistory(self.directory)
        with sqlite3.connect(str(self.database)) as db:
            columns = [row[1] for row in db.execute('PRAGMA table_info(events)')]
        self.assertNotIn('score_kind',columns,'No migration may proceed without a backup')
        owner = product.PersonHistory(self.directory)
        event = owner.get('1'*32)
        self.assertEqual(('confirmed',1200.,'Existing manual review'),
                         (event['status'],event['reviewed_at'],event['review_note']))
        self.assertEqual(2,owner.list()['total'])

    async def test_confirm_reject_withdraw_and_note_persist_after_reopen(self):
        owner = product.PersonHistory(self.directory)
        original = self.record(owner)
        self.assertEqual([],self.backups,'Fresh schema needs no ALTER migration')
        for index,status in enumerate(['confirmed','rejected','suspected']):
            with patch.object(product.time,'time',return_value=2000.+index):
                event = owner.review(original['id'],status,'审核'+status)
            self.assertEqual(status,event['status'])
            self.assertEqual(status,event['review_status'])
            self.assertEqual(2000.+index,event['reviewed_at'])
            self.assertEqual('审核'+status,event['review_note'])
            self.assertEqual(event['review_note'],event['note'])
            self.assertEqual(event,product.PersonHistory(self.directory).get(event['id']))
            for field in ['id','target_id','found_at','last_seen_at','similarity','observations']:
                self.assertEqual(original[field],event[field])
        event = owner.review(original['id'],'confirmed')
        self.assertEqual('',event['review_note'])

    async def test_later_stronger_observation_preserves_manual_labels(self):
        owner = product.PersonHistory(self.directory)
        for status in ['confirmed','rejected']:
            original = self.record(owner)
            reviewed = owner.review(original['id'],status,'Keep this decision')
            event = owner.record({'id':'target-photo','name':'Reference person'},
                {'similarity':.9,'track_id':10},1002.,b'stronger-snapshot',b'reference',original['id'])
            self.assertEqual(reviewed['status'],event['status'])
            self.assertEqual(reviewed['reviewed_at'],event['reviewed_at'])
            self.assertEqual(reviewed['review_note'],event['review_note'])
            self.assertEqual(2,event['observations'])
            self.assertEqual(.9,event['similarity'])
            self.assertEqual(b'stronger-snapshot',owner.image(event['id'],'snapshot'))

    async def test_review_validation_and_missing_ids_do_not_change_any_record(self):
        owner = product.PersonHistory(self.directory)
        event = self.record(owner)
        before = owner.list()
        for event_id in ['0'*32,'bad',"' OR 1=1 --",'../history.sqlite3',None]:
            with self.subTest(event_id=event_id):
                with self.assertRaises(FileNotFoundError):owner.review(event_id,'confirmed')
                with self.assertRaises(FileNotFoundError):owner.get(event_id)
        for status,note in [('automatic',''),('confirmed','x'*241),('confirmed',None)]:
            with self.assertRaises(ValueError):owner.review(event['id'],status,note)
        self.assertEqual(before,owner.list())
        self.assertEqual('x'*240,owner.review(event['id'],'confirmed','x'*240)['review_note'])

    async def test_description_metadata_export_is_color_evidence_and_legacy_aliases_stay_valid(self):
        owner = product.PersonHistory(self.directory)
        photo = self.record(owner)
        description = self.record(owner,id='description-target',kind='description',
            description='穿红色上衣的人',color_label='红色')
        self.assertEqual('description',description['target_kind'])
        self.assertEqual('color_fraction',description['score_kind'])
        self.assertEqual('穿红色上衣的人',description['description'])
        self.assertEqual(description['description'],description['target_description'])
        self.assertEqual('红色',description['color_label'])
        self.assertEqual(description['similarity'],description['score'])
        self.assertEqual('clothing_color_candidate',description['match_semantics'])
        self.assertFalse(description['automatic_identity_confirmation'])
        owner.review(photo['id'],'confirmed','Checked by a person')
        exported = owner.export()
        self.assertEqual(2,exported['total'])
        self.assertFalse(exported['truncated'])
        self.assertEqual('candidate_evidence',exported['match_semantics'])
        self.assertFalse(exported['automatic_identity_confirmation'])
        self.assertEqual(2,len(exported['items']))
        self.assertEqual({'appearance_candidate','clothing_color_candidate'},
                         {item['match_semantics'] for item in exported['items']})
        self.assertEqual(exported,json.loads(json.dumps(exported)))
        for item in exported['items']:
            self.assertIn('reviewed_at',item)
            self.assertIn('review_note',item)
            self.assertNotIn('image_base64',item)
        self.assertTrue(owner.export(limit=1)['truncated'])
        self.assertEqual(1,len(owner.export(limit=1)['items']))
        self.assertEqual(2,owner.export(limit=100000)['total'])

    async def test_asgi_review_export_and_invalid_requests_use_no_external_http(self):
        owner = product.PersonHistory(self.directory)
        event = self.record(owner)
        app = FastAPI()
        attach_history_actions(app,SimpleNamespace(history=owner))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://isolated.test') as client:
            response = await client.post('/history/'+event['id']+'/review',json={'status':'confirmed','note':'人工核对'})
            self.assertEqual(200,response.status_code)
            self.assertEqual('confirmed',response.json()['status'])
            self.assertEqual('人工核对',response.json()['review_note'])
            response = await client.get('/history/export.json')
            self.assertEqual(200,response.status_code)
            self.assertEqual('no-store',response.headers['cache-control'])
            self.assertIn('attachment',response.headers['content-disposition'])
            self.assertEqual('confirmed',response.json()['items'][0]['status'])
            for payload in [{'status':'unknown'},{'status':'confirmed','note':'x'*241},
                            {'status':'confirmed','note':None},{'status':'confirmed','move':True}]:
                response = await client.post('/history/'+event['id']+'/review',json=payload)
                self.assertEqual(422,response.status_code)
            response = await client.post('/history/'+'0'*32+'/review',json={'status':'suspected'})
            self.assertEqual(404,response.status_code)
            response = await client.post('/history/bad/review',json={'status':'suspected'})
            self.assertEqual(404,response.status_code)
            response = await client.post('/history/'+event['id']+'/review',json={'status':'suspected'})
            self.assertEqual(200,response.status_code)
            self.assertEqual('suspected',response.json()['review_status'])


if __name__=='__main__':
    unittest.main(verbosity=2)
