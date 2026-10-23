#!/usr/bin/env python3
"""Geometry, replay, and native safety checks using synthetic inputs only."""
import math
import ast
from pathlib import Path
import re
import unittest

from autonomy_guard import AutonomyGuard
from local_autonomy import FreshInputs, Limits, LocalRoamer, scan_points
from test_rotation_guard import healthy, remote


def ring(radius=4., count=72):
    return [(radius*math.cos(i*2*math.pi/count), radius*math.sin(i*2*math.pi/count)) for i in range(count)]


class LocalAutonomyTests(unittest.TestCase):
    def test_clear_path_walks_with_bounded_speed(self):
        plan = LocalRoamer().step((0, 0, 0), ring(), 1.)
        self.assertEqual('walking', plan['state'])
        self.assertEqual([.08, 0., 0.], plan['velocity'])

    def test_front_obstacle_stops_before_selecting_clear_turn(self):
        planner = LocalRoamer()
        self.assertEqual('walking', planner.step((0, 0, 0), ring(), 1.)['state'])
        points = ring() + [(.85, y) for y in (-.1, 0., .1)]
        self.assertEqual([0., 0., 0.], planner.step((0, 0, 0), points, 1.05)['velocity'])
        self.assertEqual('braking', planner.step((0, 0, 0), points, 1.10, .06)['state'])
        plan = planner.step((0, 0, 0), points, 1.15)
        self.assertEqual('turning', plan['state'])
        self.assertEqual([0., 0.], plan['velocity'][:2])
        self.assertGreater(plan['velocity'][2], 0.)

    def test_chooses_right_when_left_is_blocked(self):
        planner = LocalRoamer()
        points = ring() + [(.85, 0.), (.55, .55), (0., .85)]
        planner.step((0, 0, 0), points, 1.)
        self.assertLess(planner.step((0, 0, 0), points, 1.05)['velocity'][2], 0.)

    def test_collision_latches_even_after_obstacle_disappears(self):
        planner = LocalRoamer()
        self.assertEqual('fault', planner.step((0, 0, 0), ring()+[(.6, 0.)], 1.)['state'])
        self.assertEqual('fault', planner.step((0, 0, 0), ring(), 1.05)['state'])

    def test_surrounded_robot_waits_without_spinning(self):
        self.assertEqual('blocked', LocalRoamer().step((0, 0, 0), ring(.78), 1.)['state'])

    def test_circular_body_covers_the_actual_nav2_footprint_and_padding(self):
        path=Path(__file__).resolve().parents[2]/'ros2/go2_bringup/config/nav2_motion_test.yaml'
        content=path.read_text()
        polygons=re.findall(r'footprint:\s*"([^"\n]+)"',content)
        pads=[float(v) for v in re.findall(r'footprint_padding:\s*([0-9.]+)',content)]
        self.assertTrue(polygons)
        self.assertEqual(len(polygons),len(pads))
        for polygon,pad in zip(polygons,pads):
            for x,y in ast.literal_eval(polygon):
                self.assertGreaterEqual(Limits().body_radius+1e-12,math.hypot(abs(x)+pad,abs(y)+pad))

    def test_rotation_must_settle_before_forward_motion(self):
        plan = LocalRoamer().step((0, 0, 0), ring(), 1., measured_yaw_speed=.10)
        self.assertEqual('settling', plan['state'])
        self.assertEqual([0., 0., 0.], plan['velocity'])

    def test_walking_yaw_feedback_does_not_repeat_the_turn_stop_gate(self):
        planner = LocalRoamer(Limits(radius=2.))
        self.assertEqual('walking', planner.step((0, 0, 0), ring(), 1.)['state'])
        # Representative feedback from the onsite walk that used to cause
        # walking/StopMove oscillation despite a clear forward corridor.
        feedback = [-.07137, -.00533, -.04368, .03728, -.04261, .07137, .13209]
        for tick in range(1, 71):
            plan = planner.step((tick*.008, 0, .01*math.sin(tick)), ring(),
                                1+tick*.1, .08, feedback[(tick-1)%len(feedback)], True)
            self.assertEqual('walking', plan['state'], plan['reason'])
            self.assertEqual([.08, 0., 0.], plan['velocity'])

    def test_turn_completion_requires_continuous_stationarity_before_walking(self):
        planner = LocalRoamer()
        points = ring() + [(.85, 0.)]
        self.assertEqual('braking', planner.step((0, 0, 0), points, 1.)['state'])
        target = planner.target_yaw
        self.assertEqual('turning', planner.step((0, 0, 0), points, 1.05)['state'])
        # Reach the target with physically bounded pose increments.
        for tick in range(1, math.ceil(target/.02)+1):
            yaw = min(target, tick*.02)
            now = 1.05+tick*.1
            plan = planner.step((0, 0, yaw), ring(), now, measured_yaw_speed=.15)
        self.assertEqual('settling', plan['state'])
        self.assertEqual('settling', planner.step((0, 0, yaw), ring(), now+.1)['state'])
        # A renewed yaw pulse resets the dwell; a momentary zero is insufficient.
        self.assertEqual('settling', planner.step((0, 0, yaw), ring(), now+.2,
                                                 measured_yaw_speed=.05)['state'])
        for delta in (.3, .4, .5):
            self.assertEqual('settling', planner.step((0, 0, yaw), ring(), now+delta)['state'])
        self.assertEqual('walking', planner.step((0, 0, yaw), ring(), now+.6)['state'])

    def test_walk_noise_keeps_obstacle_braking_and_yaw_speed_fault(self):
        planner = LocalRoamer()
        planner.step((0, 0, 0), ring(), 1.)
        plan = planner.step((.008, 0, .01), ring()+[(.85, 0.)], 1.1, .08, .10)
        self.assertEqual('braking', plan['state'])
        self.assertEqual([0., 0., 0.], plan['velocity'])
        self.assertEqual('braking', planner.step((.012, 0, .01), ring(), 1.2, .06, .10)['state'])
        self.assertEqual('fault', planner.step((.012, 0, .01), ring(), 1.3, 0., .31)['state'])

    def test_jump_clock_deadline_and_boundary_fail_closed(self):
        for pose, now in [((.3, 0, 0), 1.05), ((0, 0, 1), 1.05), ((0, 0, 0), .9), ((0, 0, 0), 1.5)]:
            with self.subTest(pose=pose, now=now):
                planner = LocalRoamer()
                planner.step((0, 0, 0), ring(), 1.)
                self.assertEqual('fault', planner.step(pose, ring(), now)['state'])
        planner = LocalRoamer()
        planner.step((0, 0, 0), ring(), 1.)
        for index in range(1, 49):
            plan = planner.step((index*.02, 0, 0), ring(), 1+index*.1)
        self.assertEqual('fault', plan['state'])

    def test_limits_reject_unsafe_or_nonfinite_values(self):
        for kwargs in ({'radius':.9}, {'radius':4}, {'speed':.2}, {'yaw_speed':.3},
                       {'body_radius':.3}, {'margin':0.}, {'radius':math.nan}, {'speed':True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Limits(**kwargs)

    def test_stalled_walk_and_turn_stop_but_stationary_preview_remains_usable(self):
        for points in (ring(), ring()+[(.85,0.)]):
            planner = LocalRoamer()
            for tick in range(65):
                plan = planner.step((0,0,0),points,1+tick*.1,monitor_progress=True)
            self.assertEqual('fault',plan['state'])
            self.assertIn('progress',plan['reason'])
            preview = LocalRoamer()
            for tick in range(65):
                plan = preview.step((0,0,0),points,1+tick*.1)
            self.assertNotEqual('fault',plan['state'])

    def test_populated_full_scan_and_invalid_inputs(self):
        args = dict(angle_min=-math.pi, angle_increment=math.pi/360, range_min=.45, range_max=20., frame='base_footprint')
        self.assertEqual(720, len(scan_points([3.]*720, **args)))
        for ranges in ([math.inf]*720, [3.]*360, [math.nan]+[3.]*719, [.1]+[3.]*719,
                       [-math.inf]+[3.]*719, [True]+[3.]*719):
            with self.subTest(first=ranges[0], length=len(ranges)), self.assertRaises(ValueError):
                scan_points(ranges, **args)
        with self.assertRaises(ValueError):
            scan_points([3.]*720, **dict(args, frame='camera'))

    def test_static_room_simulation_avoids_obstacle_and_stays_bounded(self):
        # Ray returns are computed from fixed world walls and a circular obstacle.
        planner = LocalRoamer()
        pose, dt, states, min_gap, max_radius = [0., 0., 0.], .1, set(), 9., 0.
        for index in range(1800):
            points = []
            for ray in range(180):
                bearing = pose[2] + ray*math.pi/90
                c, s = math.cos(bearing), math.sin(bearing)
                walls = [(bound-pos)/direction for pos, direction in ((pose[0],c),(pose[1],s))
                         if abs(direction)>1e-8 for bound in (-2.5,2.5) if (bound-pos)/direction>0]
                distance = min(walls)
                ox, oy, radius = 1.-pose[0], -pose[1], .18
                along = ox*c + oy*s
                discriminant = radius**2 - (ox*ox + oy*oy - along*along)
                if along>0 and discriminant>=0:
                    distance = min(distance, along-math.sqrt(discriminant))
                relative = bearing-pose[2]
                points.append((distance*math.cos(relative), distance*math.sin(relative)))
            plan = planner.step(pose, points, 1+index*dt)
            self.assertNotEqual('fault', plan['state'], plan['reason'])
            states.add(plan['state'])
            v, _, w = plan['velocity']
            pose[0] += v*math.cos(pose[2])*dt
            pose[1] += v*math.sin(pose[2])*dt
            pose[2] += w*dt
            min_gap = min(min_gap, math.hypot(pose[0]-1.,pose[1])-.18)
            max_radius = max(max_radius, math.hypot(*pose[:2]))
        self.assertTrue({'walking','turning','braking'}.issubset(states))
        self.assertGreater(min_gap, planner.limits.clearance)
        self.assertLess(max_radius, .86)
        self.assertGreater(max_radius, .3)


class FreshInputTests(unittest.TestCase):
    def make_inputs(self):
        inputs = FreshInputs()
        for key in ('scan', 'odom', 'cloud'):
            self.assertTrue(inputs.receive(key, {}, 100., 10., 100.))
        return inputs

    def test_source_and_receipt_must_both_be_fresh(self):
        inputs = self.make_inputs()
        self.assertIsNone(inputs.reason(10.1, 100.1))
        self.assertTrue(inputs.reason(10.31, 100.1))
        self.assertTrue(inputs.reason(10.1, 100.31))

    def test_duplicate_header_never_refreshes_receipt(self):
        inputs = self.make_inputs()
        self.assertFalse(inputs.receive('scan', {}, 100., 10.25, 100.25))
        self.assertEqual(10., inputs.samples['scan']['at'])
        self.assertTrue(inputs.reason(10.31, 100.31))

    def test_future_old_backwards_and_missing_inputs_stop(self):
        for stamp in (99., 101., 99.99):
            inputs = self.make_inputs()
            self.assertFalse(inputs.receive('scan', {}, stamp, 10.1, 100.1))
            self.assertTrue(inputs.reason(10.1, 100.1))
        self.assertTrue(FreshInputs().reason(10., 100.))


class AutonomyGuardTests(unittest.TestCase):
    def sample(self, now=1000., tick=100, **changes):
        state, low = healthy(now, yaw_speed=0.)
        low.update(tick=tick, bit_flag=4)
        state.update(changes)
        return state, low

    def test_healthy_turn_and_translation_allowed_within_native_boundary(self):
        guard = AutonomyGuard()
        self.assertIsNone(guard.check(*self.sample(), 1000.))
        guard.mark_started(1000.)
        self.assertIsNone(guard.check(*self.sample(1000.1,101,position=[.2,0,0],velocity=[.08,0,0]),1000.1))
        self.assertTrue(guard.check(*self.sample(1000.2,102,position=[.96,0,0]),1000.2))

    def test_guard_rejects_fault_hot_remote_stale_low_clock_and_height(self):
        for key, value in [('motor_faults',[0]*9+[4,0,0]), ('temperatures',[70]*12),
                           ('wireless_remote',remote(keys=1)), ('bit_flag',0x40), ('tick',None)]:
            guard = AutonomyGuard()
            state, low = self.sample()
            low[key] = value
            with self.subTest(key=key):
                self.assertTrue(guard.check(state, low, 1000.))
        guard = AutonomyGuard()
        self.assertIsNone(guard.check(*self.sample(),1000.))
        self.assertTrue(guard.check(*self.sample(1000.3),1000.3))
        guard = AutonomyGuard()
        self.assertIsNone(guard.check(*self.sample(),1000.))
        self.assertTrue(guard.check(*self.sample(1000.1,101,position=[0,0,.10]),1000.1))

    def test_must_start_stationary_and_end_by_deadline(self):
        self.assertTrue(AutonomyGuard().check(*self.sample(velocity=[.08,0,0]),1000.))
        guard = AutonomyGuard(duration=5.)
        self.assertIsNone(guard.check(*self.sample(),1000.))
        guard.mark_started(1000.)
        self.assertTrue(guard.check(*self.sample(1005.,105),1005.))

    def test_locked_stand_and_its_transition_are_not_autonomous_preparation(self):
        for state_code in (1001, 1002):
            guard=AutonomyGuard()
            self.assertIn('START',guard.check(*self.sample(error=state_code),1000.))
            self.assertTrue(guard.check(*self.sample(1000.1,101,error=100),1000.1,settling=True))
        guard=AutonomyGuard()
        self.assertIsNone(guard.check(*self.sample(error=100),1000.))
        self.assertIsNone(guard.check(*self.sample(1000.1,101,error=100,velocity=[.06,0,0],yaw_speed=.11),1000.1,settling=True))
        self.assertTrue(guard.check(*self.sample(1000.2,102,error=100,velocity=[.21,0,0]),1000.2,settling=True))

    def test_settling_keeps_fault_tilt_speed_remote_and_tick_guards(self):
        for changes,low_changes in [({'velocity':[.21,0,0]},{}),({'rpy':[.3,0,0]},{}),
                ({},{'motor_faults':[4]*12}),({},{'wireless_remote':remote(keys=1)})]:
            state,low=self.sample(**changes);low.update(low_changes)
            self.assertTrue(AutonomyGuard().check(state,low,1000.,settling=True))
        guard=AutonomyGuard();self.assertIsNone(guard.check(*self.sample(),1000.))
        self.assertTrue(guard.check(*self.sample(1000.3),1000.3,settling=True))


if __name__ == '__main__':
    unittest.main(verbosity=2)
