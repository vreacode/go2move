"""RGB-D pairing, ranging and search/history integration with isolated files."""
import hashlib
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import numpy as np

from person_depth import load_depth,torso_depth
from rgbd_camera import decode_image,encode_pair


def messages(now=None,red=220,depth=1500):
    now=time.time() if now is None else now
    header=NS(stamp=NS(sec=int(now),nanosec=int((now-int(now))*1e9)),frame_id='d435i_color_optical_frame')
    image=np.full((64,96,3),(0,0,red),np.uint8)
    color=NS(header=header,width=96,height=64,encoding='bgr8',is_bigendian=0,step=288,data=image.tobytes())
    values=np.full((64,96),depth,np.uint16)
    depth=NS(header=header,width=96,height=64,encoding='16UC1',is_bigendian=0,step=192,data=values.tobytes())
    info=NS(header=header,width=96,height=64,k=[500.,0.,48.,0.,500.,32.,0.,0.,1.])
    return color,depth,info


def cache(directory,red=220,depth=1500):
    now=time.time();raw,bundle,status=encode_pair(*messages(now,red,depth),now)
    (directory/'latest.jpg').write_bytes(raw);(directory/'bundle.npz').write_bytes(bundle)
    (directory/'status.json').write_text(json.dumps(status))
    return raw,bundle,status


class DepthTests(unittest.TestCase):
    def test_torso_median_ignores_missing_values_and_reports_camera_depth(self):
        depth=np.full((64,96),1.5,np.float32);depth[22,40]=0;depth[23,41]=np.nan
        value=torso_depth(depth,[10,10,70,56],'optical')
        self.assertEqual(1.5,value['depth_m']);self.assertEqual('optical_axis_depth',value['measurement'])
        self.assertEqual('optical',value['frame_id']);self.assertGreater(value['valid_fraction'],.9)

    def test_invalid_sparse_out_of_range_or_mixed_depth_returns_no_measurement(self):
        for value in (0.,np.nan,np.inf,.1,6.1):
            self.assertIsNone(torso_depth(np.full((64,96),value,np.float32),[10,10,70,56],'optical'))
        depth=np.full((64,96),1.,np.float32);depth[:,40:]=4.
        self.assertIsNone(torso_depth(depth,[10,10,70,56],'optical'))
        self.assertIsNone(torso_depth(np.ones((64,96)),[1,1,4,5],'optical'))

    def test_big_endian_padded_depth_and_rgb_are_decoded(self):
        color,depth,_=messages();depth.is_bigendian=1
        depth.data=np.full((64,98),2000,dtype='>u2').tobytes();depth.step=196
        self.assertTrue(np.all(decode_image(depth)==2.))
        color.encoding='rgb8';color.data=np.full((64,96,3),(220,0,0),np.uint8).tobytes()
        self.assertEqual([0,0,220],decode_image(color)[0,0].tolist())

    def test_truncated_messages_rejected_before_array_access(self):
        color,depth,_=messages()
        for msg in (color,depth):
            msg.data=msg.data[:-1]
            with self.assertRaises(ValueError):decode_image(msg)

    def test_unsynchronized_future_stale_or_different_frame_messages_rejected(self):
        for mode in ('future','stale','unsynchronized','frame','grid','intrinsics'):
            now=time.time();color,depth,info=messages(now)
            if mode=='future':now-=.1
            if mode=='stale':now+=2.1
            if mode=='unsynchronized':depth.header=NS(stamp=NS(sec=color.header.stamp.sec,nanosec=color.header.stamp.nanosec-50000000),frame_id=color.header.frame_id)
            if mode=='frame':depth.header=NS(stamp=color.header.stamp,frame_id='other_camera')
            if mode=='grid':depth.width=95
            if mode=='intrinsics':info.k=[0.]*9
            with self.subTest(mode=mode),self.assertRaises(ValueError):encode_pair(color,depth,info,now)

    def test_bundle_roundtrip_and_cross_frame_mismatch_cannot_produce_distance(self):
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder);raw,bundle,status=cache(directory)
            self.assertTrue(np.allclose(load_depth(directory,status,raw,time.time()),1.5))
            with self.assertRaises(ValueError):load_depth(directory,status,raw,time.time()+3)
            with self.assertRaises(ValueError):load_depth(directory,status,raw+b'changed',time.time())
            _,other,_=encode_pair(*messages(time.time(),red=210,depth=3000),time.time())
            (directory/'bundle.npz').write_bytes(other)
            with self.assertRaises(ValueError):load_depth(directory,status,raw,time.time())
            self.assertIsNone(load_depth(directory,{'source':'unitree_sdk2_video'},raw,time.time()))

    def test_bundle_alignment_and_metadata_are_validated_independently_of_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder);raw,bundle,status=cache(directory)
            for field,value in [('alignment','depth'),('depth_frame_id','different'),('depth_captured_at',status['captured_at']-.1)]:
                bad=dict(status);bad[field]=value
                with self.subTest(field=field),self.assertRaises(ValueError):load_depth(directory,bad,raw,time.time())


class SearchDepthTests(unittest.TestCase):
    def setUp(self):
        from test_person_attributes_tasks import DescriptionSearchTests
        self.fixture=DescriptionSearchTests();self.fixture.setUp()
        import person_search
        self.product=person_search

    def tearDown(self):self.fixture.tearDown()

    def test_three_aligned_frames_store_depth_and_camera_source_with_candidate_photo(self):
        import asyncio
        from person_history import PersonHistory
        owner=self.fixture.searcher
        asyncio.run(self.product.start(self.product.SearchRequest(speak=False)))
        for red in (240,230,220):
            cache(self.fixture.camera,red=red);result=owner.inspect_frame()
        self.assertTrue(result['suspected_target'])
        self.assertEqual(1.5,result['candidates'][0]['depth']['depth_m'])
        event=owner.history.get(result['event_id'])
        self.assertEqual('realsense_d435i',event['camera_source'])
        self.assertEqual(1.5,event['depth']['depth_m'])
        reopened=PersonHistory(self.fixture.runtime/'history')
        self.assertEqual(event,reopened.get(event['id']))

    def test_incoherent_depth_keeps_rgb_search_but_never_invents_a_distance(self):
        import asyncio
        owner=self.fixture.searcher
        asyncio.run(self.product.start(self.product.SearchRequest(speak=False)))
        cache(self.fixture.camera);(self.fixture.camera/'bundle.npz').write_bytes(b'wrong frame')
        result=owner.inspect_frame()
        self.assertTrue(result['depth_error'])
        self.assertIsNone(result['candidates'][0]['depth'])

    def test_depth_tracks_the_saved_strongest_snapshot_and_preserves_review(self):
        owner=self.fixture.searcher.history;target={'id':'depth-target','name':'Depth target'}
        first=owner.record(target,{'similarity':.6,'track_id':7,'camera_source':'realsense_d435i','depth':{'depth_m':2.}},100.,b'first',b'reference')
        owner.review(first['id'],'confirmed','keep')
        for score,depth,image in [(.9,1.5,b'strong'),(.7,4.,b'weak')]:
            owner.record(target,{'similarity':score,'track_id':7,'camera_source':'realsense_d435i','depth':{'depth_m':depth}},101.,image,b'reference',first['id'])
        event=owner.get(first['id'])
        self.assertEqual(1.5,event['depth']['depth_m']);self.assertEqual('confirmed',event['status'])
        self.assertEqual(b'strong',owner.image(first['id'],'snapshot'))


if __name__=='__main__':unittest.main(verbosity=2)
