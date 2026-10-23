"""Isolated saved-map fixtures; never writes real maps/routes or opens ROS."""
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np
import yaml
from saved_map_routes import SavedMapRoutes, pixel_to_world, world_to_pixel


class SavedMapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='go2-static-map-test-')
        self.root = Path(self.temp.name)/'maps'
        self.root.mkdir()
        self.maps = SavedMapRoutes(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def make_map(self, name='room', image=None, origin=None, negate=0, resolution=.1, pose=None):
        if image is None:
            image = np.full((100,100),254,np.uint8)
            image[:3],image[-3:],image[:,:3],image[:,-3:] = 0,0,0,0
        directory = self.root/name
        directory.mkdir()
        ok, encoded = cv2.imencode('.pgm',image)
        self.assertTrue(ok)
        (directory/'map.pgm').write_bytes(encoded.tobytes())
        metadata = {'image':'map.pgm','mode':'trinary','origin':origin or [0.,0.,0.],
                    'resolution':resolution,'negate':negate,'free_thresh':.25,'occupied_thresh':.65}
        (directory/'map.yaml').write_text(yaml.safe_dump(metadata))
        if pose is not None:
            (directory/'snapshot.json').write_text(json.dumps({'saved':True,'map_pose':pose,
                'map':{'width':image.shape[1],'height':image.shape[0],'resolution':resolution}}))
        return directory

    def assert_candidates(self, result):
        data = self.maps._load(result['generation']['map_id'])
        pose = result['generation']['reference_pose']
        ref_col,ref_row = world_to_pixel(pose[0],pose[1],data)
        label = data['labels'][ref_row,ref_col]
        self.assertGreater(label,0)
        for point,pixel in zip(result['points'],result['generation']['point_pixels']):
            col,row = world_to_pixel(point['x'],point['y'],data)
            self.assertTrue(data['free'][row,col])
            self.assertGreaterEqual(data['clearance'][row,col],.4)
            self.assertEqual(data['labels'][row,col],label)
            self.assertLessEqual(math.hypot(point['x']-pose[0],point['y']-pose[1]),result['generation']['radius_m']+1e-8)
            self.assertAlmostEqual(pixel['x'],col)
            self.assertAlmostEqual(pixel['y'],row)
        for index,first in enumerate(result['points']):
            for second in result['points'][index+1:]:
                self.assertGreaterEqual(math.hypot(first['x']-second['x'],first['y']-second['y']),.6-1e-8)
        self.assertFalse(result['generation']['live_localization'])
        self.assertFalse(result['generation']['ordered_route_validated'])
        self.assertFalse(result['motion_enabled'])

    def test_snapshot_pose_prioritized_and_png_pixels_consistent(self):
        self.make_map(pose=[5.05,4.95,.4])
        result = self.maps.suggest('room')
        self.assertEqual(result['generation']['reference_pose_source'],'snapshot.map_pose')
        self.assertEqual(result['generation']['reference_pose'],[5.05,4.95,.4])
        self.assert_candidates(result)
        png = self.maps.preview('room')
        self.assertTrue(png.startswith(b'\x89PNG\r\n\x1a\n'))
        decoded = cv2.imdecode(np.frombuffer(png,np.uint8),cv2.IMREAD_COLOR)
        self.assertEqual(decoded.shape[:2],(result['generation']['height'],result['generation']['width']))

    def test_rotated_origin_and_image_y_flip(self):
        self.make_map(origin=[10.,20.,math.pi/2],pose=[5.05,25.05,.3])
        result = self.maps.suggest('room')
        self.assertEqual(result['generation']['reference_pose_source'],'snapshot.map_pose')
        self.assert_candidates(result)
        data = {'width':3,'height':4,'resolution':.5,'origin':[1.,-2.,math.pi/2]}
        x,y = pixel_to_world(2,0,data)
        self.assertAlmostEqual(x,-.75)
        self.assertAlmostEqual(y,-.75)
        self.assertEqual(world_to_pixel(x,y,data),(2,0))
        self.assertGreater(pixel_to_world(2,3,data)[0],x)

    def test_unsafe_outside_and_missing_snapshot_fallback(self):
        for name,pose in [('lowclear',[.15,.15,0]),('outside',[999.,999.,0]),('missing',None)]:
            self.make_map(name,pose=pose)
            result = self.maps.suggest(name)
            self.assertEqual(result['generation']['reference_pose_source'],'largest_clearance_component_center')
            if pose is not None:
                self.assertIsNotNone(result['generation']['reference_rejected_reason'])
            self.assert_candidates(result)

    def test_unknown_205_guard_despite_legacy_threshold_mismatch(self):
        image = np.full((100,160),205,np.uint8)
        image[5:95,5:70] = 254
        image[5:95,90:155] = 254
        self.make_map(image=image,pose=[3.55,4.95,0])
        data = self.maps._load('room')
        self.assertEqual(int(data['free'].sum()),90*65*2)
        self.assertFalse(data['free'][50,80])
        result = self.maps.suggest('room')
        self.assertTrue(result['generation']['threshold_mismatch'])
        self.assertEqual(result['generation']['unknown_value_guard'],205)
        self.assertIn('205',result['warning'])
        self.assert_candidates(result)
        self.assertTrue(all(pixel['x']<70 for pixel in result['generation']['point_pixels']))

    def test_narrow_passage_does_not_connect_safe_components(self):
        image = np.zeros((100,180),np.uint8)
        image[5:95,5:75],image[5:95,105:175] = 254,254
        image[47:53,75:105] = 254  # 0.6 m passage cannot fit 0.4 m clearance.
        self.make_map(image=image,pose=[3.55,4.95,0])
        result = self.maps.suggest('room')
        self.assert_candidates(result)
        self.assertTrue(all(pixel['x']<75 for pixel in result['generation']['point_pixels']))
        self.assertEqual(self.maps._load('room')['labels'][50,90],0)

    def test_negate_and_strict_threshold_semantics(self):
        image = np.zeros((100,100),np.uint8)
        image[:5],image[-5:],image[:,:5],image[:,-5:] = 255,255,255,255
        image[40:44,40:44] = 100  # intermediate shade is unknown when negate=1.
        self.make_map(image=image,negate=1)
        data = self.maps._load('room')
        self.assertTrue(data['free'][30,30])
        self.assertFalse(data['free'][41,41])
        self.assertTrue(data['occupied'][0,0])
        self.assert_candidates(self.maps.suggest('room'))

    def test_outer_map_boundary_is_blocked(self):
        self.make_map(image=np.full((100,100),254,np.uint8))
        result = self.maps.suggest('room',count=3,radius_m=5)
        self.assert_candidates(result)
        for point in result['points']:
            self.assertGreaterEqual(min(point['x'],point['y'],10-point['x'],10-point['y']),.4)

    def test_no_adequate_free_area_explicit_failure(self):
        self.make_map('unknown',image=np.full((20,20),205,np.uint8))
        self.make_map('narrow',image=np.full((5,100),254,np.uint8))
        for name in ('unknown','narrow'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError,'净空'):
                self.maps.suggest(name)

    def test_path_whitelist_external_images_and_symlinks_rejected(self):
        directory = self.make_map()
        for map_id in ('../room','/tmp/room','room/map.yaml','room%2F..',None):
            with self.subTest(map_id=map_id),self.assertRaises(ValueError):
                self.maps.preview(map_id)
        external = Path(self.temp.name)/'outside.pgm'
        external.write_bytes((directory/'map.pgm').read_bytes())
        metadata = yaml.safe_load((directory/'map.yaml').read_text())
        for image in (str(external),'../outside.pgm'):
            metadata['image']=image
            (directory/'map.yaml').write_text(yaml.safe_dump(metadata))
            with self.assertRaisesRegex(ValueError,'越界'):
                self.maps.preview('room')
        (directory/'linked.pgm').symlink_to(external)
        metadata['image']='linked.pgm'
        (directory/'map.yaml').write_text(yaml.safe_dump(metadata))
        with self.assertRaisesRegex(ValueError,'符号链接'):
            self.maps.preview('room')
        (self.root/'linked-map').symlink_to(directory,target_is_directory=True)
        self.assertNotIn('linked-map',[item['id'] for item in self.maps.list_maps()['items']])

    def test_existing_absolute_same_directory_image_accepted(self):
        directory = self.make_map()
        metadata = yaml.safe_load((directory/'map.yaml').read_text())
        metadata['image']=str(directory/'map.pgm')
        (directory/'map.yaml').write_text(yaml.safe_dump(metadata))
        self.assert_candidates(self.maps.suggest('room'))

    def test_corrupt_metadata_and_oversized_header_fail_before_decode(self):
        directory = self.make_map()
        original = (directory/'map.yaml').read_text()
        for key,value in [('resolution',0),('origin',[0,0,float('nan')]),('free_thresh',.9),('mode','raw')]:
            metadata = yaml.safe_load(original)
            metadata[key]=value
            (directory/'map.yaml').write_text(yaml.safe_dump(metadata))
            with self.subTest(key=key),self.assertRaises(ValueError):
                self.maps.suggest('room')
        (directory/'map.yaml').write_text(original)
        (directory/'map.pgm').write_bytes(b'P5\n99999999 99999999\n255\n')
        with patch('saved_map_routes.cv2.imdecode',side_effect=AssertionError('must reject before allocating')):
            with self.assertRaisesRegex(ValueError,'尺寸'):
                self.maps.preview('room')
        (directory/'map.pgm').write_bytes(b'not a map')
        self.assertFalse(self.maps.list_maps()['items'][0]['available'])

    def test_parameter_bounds(self):
        self.make_map()
        for count in (0,13,True,1.5):
            with self.subTest(count=count),self.assertRaises(ValueError):
                self.maps.suggest('room',count=count)
        for radius in (.49,5.01,float('nan'),True):
            with self.subTest(radius=radius),self.assertRaises(ValueError):
                self.maps.suggest('room',radius_m=radius)

    def test_saved_date_precedes_metadata_repair_mtime(self):
        older,newer=self.make_map('old-20261005'),self.make_map('new-20261006')
        for directory,stamp in ((older,9999.),(newer,1.)):
            for path in directory.iterdir():
                os.utime(path,(stamp,stamp))
        self.assertEqual(self.maps.list_maps()['default_id'],'new-20261006')

    def test_resized_preview_and_overlay_use_exact_png_shape(self):
        self.make_map(image=np.full((800,1000),254,np.uint8))
        result=self.maps.suggest('room')
        png=cv2.imdecode(np.frombuffer(self.maps.preview('room'),np.uint8),cv2.IMREAD_COLOR)
        generation=result['generation']
        self.assertEqual(png.shape[:2],(generation['height'],generation['width']))
        self.assertEqual(generation['width'],768)
        data=self.maps._load('room')
        for point,pixel in zip(result['points'],generation['point_pixels']):
            col,row=world_to_pixel(point['x'],point['y'],data)
            self.assertAlmostEqual(pixel['x'],(col+.5)*png.shape[1]/1000-.5)
            self.assertAlmostEqual(pixel['y'],(row+.5)*png.shape[0]/800-.5)

    def test_readonly_hash_and_latest_usable_default(self):
        older,newer = self.make_map('older'),self.make_map('newer')
        for directory,stamp in ((older,100.),(newer,200.)):
            for path in directory.iterdir():
                os.utime(path,(stamp,stamp))
        before={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in self.root.rglob('*') if path.is_file()}
        listing=self.maps.list_maps()
        self.assertEqual(listing['default_id'],'newer')
        first=self.maps.suggest('newer')
        self.maps.preview('newer')
        after={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(before,after)
        metadata=newer/'map.yaml'
        metadata.write_text(metadata.read_text()+'# metadata revision\n')
        self.assertNotEqual(first['generation']['map_sha256'],self.maps.suggest('newer')['generation']['map_sha256'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
