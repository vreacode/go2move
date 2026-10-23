"""Person-follow geometry, identity and fail-stop tests; no robot transport."""
import copy
import math
import unittest
from person_follow import FollowController, FollowRequest


def observation(at=100., track=7, z=3., x=320):
    return {'captured_at': at, 'target_id': 'target', 'target_kind': 'photo',
            'camera_source': 'realsense_d435i', 'suspected_target': True,
            'similarity_threshold': .75, 'depth_error': None,
            'camera_geometry': {'width': 640, 'height': 480, 'fx': 600., 'cx': 320., 'frame_id': 'optical'},
            'candidates': [{'track_id': track, 'similarity': .9, 'bbox': [x-30, 40, x+30, 400],
                            'depth': {'depth_m': z, 'measurement': 'optical_axis_depth', 'frame_id': 'optical'}}]}


class FollowTests(unittest.TestCase):
    def setUp(self):
        self.controller = FollowController()
        self.controller.start('target', 1.8, 100.)

    def acquire(self, **kwargs):
        for at in (100., 100.1, 100.2):
            value = self.controller.update(observation(at=at, **kwargs), 'target', at+.1)
        return value

    def stopped(self, result):
        self.assertFalse(result['active'])
        self.assertEqual([0, 0, 0], result['suggested_velocity'])
        self.assertFalse(result['physical_execution'])

    def test_three_distinct_frames_required_and_speed_capped(self):
        r = observation()
        for _ in range(8):
            self.assertEqual([0, 0, 0], self.controller.update(r, 'target', 100.1)['suggested_velocity'])
        result = self.acquire()
        self.assertEqual(7, result['track_id'])
        self.assertEqual([.2, 0, 0], result['suggested_velocity'])
        self.assertFalse(result['motion_enabled'])

    def test_close_target_never_reverses(self):
        for z in (.5, 1.5, 1.8, 2.0):
            self.setUp()
            result = self.acquire(z=z)
            self.assertEqual([0, 0, 0], result['suggested_velocity'])

    def test_right_target_negative_yaw_and_no_forward_until_centered(self):
        result = self.acquire(x=500)
        self.assertLess(result['bearing_rad'], 0)
        self.assertEqual(0, result['suggested_velocity'][0])
        self.assertLess(result['suggested_velocity'][2], 0)
        self.assertLessEqual(abs(result['suggested_velocity'][2]), .25)

    def test_left_target_positive_yaw(self):
        result = self.acquire(x=140)
        self.assertGreater(result['suggested_velocity'][2], 0)

    def test_no_missing_candidate_track_handoff_or_reacquire_after_stop(self):
        for change in ('missing', 'track', 'ambiguous', 'low_score', 'excluded'):
            self.setUp(); self.acquire()
            result = observation(at=100.3)
            if change == 'missing': result['candidates'] = []
            if change == 'track': result['candidates'][0]['track_id'] = 8
            if change == 'ambiguous':
                other = copy.deepcopy(result['candidates'][0]); other.update(track_id=8, similarity=.88)
                result['candidates'].append(other)
            if change == 'low_score': result['candidates'][0]['similarity'] = .6
            if change == 'excluded': result['candidates'][0]['excluded_by_review'] = True
            with self.subTest(change=change):
                self.stopped(self.controller.update(result, 'target', 100.4))
                self.stopped(self.controller.update(observation(at=100.5), 'target', 100.6))

    def test_bad_depth_source_geometry_and_nonfinite_values_stop(self):
        changes = [lambda r: r.update(depth_error='bundle mismatch'),
                   lambda r: r.update(camera_source='native_camera'),
                   lambda r: r['candidates'][0].update(depth=None),
                   lambda r: r['candidates'][0]['depth'].update(depth_m=float('nan')),
                   lambda r: r['candidates'][0]['depth'].update(frame_id='other'),
                   lambda r: r['camera_geometry'].update(fx=0),
                   lambda r: r['candidates'][0].update(bbox=[-1, 1, 80, 400]),
                   lambda r: r.update(target_kind='description'),
                   lambda r: r['candidates'][0].update(track_id=True),
                   lambda r: r.update(suspected_target=False)]
        for change in changes:
            self.setUp(); self.acquire(); result = observation(at=100.3); change(result)
            self.stopped(self.controller.update(result, 'target', 100.4))

    def test_stale_future_and_backwards_camera_time_stop(self):
        for at, now in ((99., 100.4), (101., 100.4), (100.1, 100.4)):
            self.setUp(); self.acquire()
            self.stopped(self.controller.update(observation(at=at), 'target', now))

    def test_watchdog_tick_stops_without_new_image(self):
        self.acquire()
        self.stopped(self.controller.update(observation(at=100.2), 'target', 100.9))

    def test_target_change_and_deadline_stop(self):
        for target, now in (('new_target', 100.4), ('target', 220.)):
            self.setUp(); self.acquire()
            self.stopped(self.controller.update(observation(at=now), target, now))

    def test_acquisition_timeout_and_clock_regression(self):
        self.stopped(self.controller.update(None, 'target', 116.))
        self.setUp(); self.acquire()
        self.stopped(self.controller.update(observation(at=99.), 'target', 99.))

    def test_cancel_zero_and_restart_never_preserves_track(self):
        self.acquire(); self.controller.stop()
        self.stopped(self.controller.snapshot())
        self.controller.start('new', 2., 101.)
        self.assertIsNone(self.controller.track_id)
        self.assertEqual([0, 0, 0], self.controller.snapshot()['suggested_velocity'])
        self.assertFalse(FollowController().active)

    def test_strict_api_does_not_accept_arming_or_unbounded_distance(self):
        for payload in ({'mode': 'execute'}, {'armed': True}, {'distance_m': 1.},
                        {'distance_m': 3.}, {'distance_m': math.nan}):
            with self.assertRaises(ValueError): FollowRequest(**payload)


if __name__ == '__main__': unittest.main(verbosity=2)
