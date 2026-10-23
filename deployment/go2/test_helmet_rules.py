"""Deterministic multi-person/PPE association tests; no camera or model claims."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from helmet_rules import HelmetRules,associate_heads
from helmet_monitor import HelmetEvidence


def people(count):
    return [{'track_id':i+1,'bbox':[130*i,0,130*i+100,300]} for i in range(count)]


def head(index,label,score=.9):
    return {'label':label,'confidence':score,'bbox':[130*index+25,0,130*index+75,60]}


class HelmetTests(unittest.TestCase):
    def test_failed_new_record_commit_does_not_appear_successful_in_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=HelmetEvidence(tmp)
            import helmet_monitor
            original=helmet_monitor.atomic
            def write_error(path,data):
                if path.name=='records.json':raise OSError('simulated disk full')
                return original(path,data)
            p={'track_id':1,'confidence':.9,'depth':None}
            with patch('helmet_monitor.atomic',side_effect=write_error):
                with self.assertRaises(OSError):store.record('s',p,100,b'frame',b'crop','test')
            self.assertEqual(0,store.list()['total'])
            self.assertEqual(0,HelmetEvidence(tmp).list()['total'])

    def test_failed_update_preserves_previous_durable_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=HelmetEvidence(tmp)
            p={'track_id':1,'confidence':.9,'depth':None}
            entry=store.record('s',p,100,b'frame',b'crop','test')
            with patch('helmet_monitor.atomic',side_effect=OSError('write unavailable')):
                with self.assertRaises(OSError):store.record('s',p,101,b'other',b'other','test')
            self.assertEqual(1,store.list()['items'][0]['observations'])
            self.assertEqual(100,store.list()['items'][0]['last_seen_at'])
            self.assertEqual(1,HelmetEvidence(tmp).list()['items'][0]['observations'])

    def run_scene(self,count,heads):
        rules=HelmetRules()
        for stamp in (100.,100.2,100.4):
            result=rules.update(people(count),heads,stamp,stamp+.05)
        return rules,result

    def test_one_helmet_one_no_helmet_independent_targets(self):
        _,r=self.run_scene(2,[head(0,'Hardhat'),head(1,'NO-Hardhat')])
        self.assertEqual(['helmet','no_helmet'],[p['state'] for p in r])
        self.assertEqual([2],[p['track_id'] for p in r if p['confirmed_violation']])

    def test_one_helmet_multiple_no_helmet_all_recordable(self):
        _,r=self.run_scene(4,[head(0,'Hardhat')]+[head(i,'NO-Hardhat') for i in range(1,4)])
        self.assertEqual([2,3,4],[p['track_id'] for p in r if p['confirmed_violation']])
        self.assertEqual(4,len({p['track_id'] for p in r}))

    def test_missing_helmet_is_not_violation_evidence(self):
        _,r=self.run_scene(3,[head(0,'Hardhat')])
        self.assertEqual(['helmet','unknown','unknown'],[p['state'] for p in r])

    def test_three_consecutive_frames_and_matching_track_required(self):
        rule=HelmetRules()
        for i in range(2):
            r=rule.update(people(1),[head(0,'NO-Hardhat')],100+i*.2,100+i*.2)
            self.assertFalse(r[0]['confirmed_violation'])
        other=people(1);other[0]['track_id']=9
        r=rule.update(other,[head(0,'NO-Hardhat')],100.4,100.4)
        self.assertEqual('unknown',r[0]['state'])

    def test_hat_in_hand_lower_body_does_not_mark_compliant(self):
        d=head(0,'Hardhat');d['bbox']=[25,180,75,240]
        _,r=self.run_scene(1,[d]);self.assertEqual('unknown',r[0]['state'])

    def test_overlapping_people_do_not_share_one_head(self):
        p=people(1)*2;p=[dict(p[0]),dict(p[1])];p[1]['track_id']=2
        a=associate_heads(p,[head(0,'NO-Hardhat')])
        self.assertTrue(all(v['raw_state']=='unknown' for v in a.values()))

    def test_conflicting_low_confidence_and_outside_heads_are_unknown(self):
        for detections in ([head(0,'Hardhat',.85),head(0,'NO-Hardhat',.85)],
                           [head(0,'NO-Hardhat',.4)],[head(2,'NO-Hardhat')]):
            _,r=self.run_scene(1,detections);self.assertEqual('unknown',r[0]['state'])

    def test_wearing_hat_and_occlusion_clear_current_violation(self):
        rule,_=self.run_scene(1,[head(0,'NO-Hardhat')])
        r=rule.update(people(1),[head(0,'Hardhat')],100.6,100.6)
        self.assertEqual('helmet',r[0]['state']);self.assertFalse(r[0]['confirmed_violation'])
        r=rule.update(people(1),[],100.8,100.8)
        self.assertEqual('unknown',r[0]['state'])

    def test_gaps_and_missing_person_reset_consecutive_evidence(self):
        rule,_=self.run_scene(1,[head(0,'NO-Hardhat')])
        rule.update([],[],100.6,100.6)
        r=rule.update(people(1),[head(0,'NO-Hardhat')],100.8,100.8)
        self.assertEqual('unknown',r[0]['state'])
        r=rule.update(people(1),[head(0,'NO-Hardhat')],103.,103.)
        self.assertEqual('unknown',r[0]['state'])

    def test_stale_future_repeated_and_backwards_stamps_rejected(self):
        rule,_=self.run_scene(1,[])
        for stamp,now in ((100.4,100.4),(100.1,100.5),(101.,100.5),(101.,104.)):
            with self.assertRaises(ValueError):rule.update(people(1),[],stamp,now)

    def test_evidence_separate_per_track_persistent_and_snapshot_depth_coherent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=HelmetEvidence(tmp)
            def p(track,z):return {'track_id':track,'confidence':.9,'depth':{'depth_m':z}}
            a=store.record('s',p(1,1.5),100,b'frame1',b'crop1','test')
            b=store.record('s',p(2,2.5),100,b'frame2',b'crop2','test')
            again=store.record('s',p(1,3.),101,b'new_frame',b'new_crop','test')
            self.assertEqual(a['id'],again['id']);self.assertNotEqual(a['id'],b['id'])
            self.assertEqual(1.5,again['depth']['depth_m']);self.assertEqual(3.,again['last_depth']['depth_m'])
            self.assertEqual(b'frame1',store.image(a['id'],'snapshot'))
            restored=HelmetEvidence(tmp);self.assertEqual(2,restored.list()['total'])
            self.assertEqual(2,restored.list()['items'][1]['observations'])
            with self.assertRaises(FileNotFoundError):restored.image('../secret','crop')


if __name__=='__main__':unittest.main(verbosity=2)
