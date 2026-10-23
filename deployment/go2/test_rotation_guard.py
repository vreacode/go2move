#!/usr/bin/env python3
"""Synthetic telemetry only; no SDK, ROS, HTTP, hardware or speech."""
import copy
import math
import struct
import unittest
from rotation_guard import RotationGuard


def healthy(now=1000.0, **changes):
    state = {'at':now,'height':.30,'rpy':[0.,0.,0.],
             'position':[0.,0.,0.],'velocity':[0.,0.,0.],'error':100}
    low = {'at':now,'temperatures':[40.]*12,'soc':80.,'power_v':30.,
           'motor_lost':[0]*12,'motor_faults':[0]*12,'wireless_remote':[0]*40}
    state.update(changes)
    return state,low


def remote(keys=0, offset=None, axis=0.):
    packet = bytearray(40)
    struct.pack_into('<H',packet,2,keys)
    if offset is not None:
        struct.pack_into('<f',packet,offset,axis)
    return list(packet)


class RotationGuardTests(unittest.TestCase):
    def setUp(self):
        self.guard = RotationGuard()

    def check(self, now=1000.0, yaw=0., position=None, starting=False, **low_changes):
        state,low = healthy(now,rpy=[0.,0.,math.radians(yaw)])
        if position is not None:
            state['position'] = position
        low.update(low_changes)
        return self.guard.check(state,low,now,starting=starting)

    def start(self, now=1000.0):
        self.assertIsNone(self.check(now))
        self.guard.mark_started(now)

    def test_baseline_error_100_is_accepted_and_progress_starts_zero(self):
        self.assertIsNone(self.check())
        self.assertEqual({'angle_deg':0.,'net_angle_deg':0.,'displacement_m':0.},self.guard.progress())

    def test_every_required_field_and_vector_length_are_checked(self):
        for name in ['at','height','rpy','position','velocity','error']:
            state,low = healthy()
            del state[name]
            with self.subTest(state_field=name):
                self.assertTrue(RotationGuard().check(state,low,1000.))
        for name in ['at','temperatures','soc','power_v','motor_lost','motor_faults','wireless_remote']:
            state,low = healthy()
            del low[name]
            with self.subTest(low_field=name):
                self.assertTrue(RotationGuard().check(state,low,1000.))
        for name in ['rpy','position','velocity','temperatures','motor_lost','motor_faults','wireless_remote']:
            state,low = healthy()
            owner = state if name in state else low
            owner[name] = owner[name][:-1]
            with self.subTest(short_vector=name):
                self.assertTrue(RotationGuard().check(state,low,1000.))
        self.assertTrue(self.guard.check(None,{},1000.))

    def test_nan_inf_non_numeric_and_boolean_telemetry_are_rejected(self):
        for key in ['at','height','error','soc','power_v']:
            for bad in [float('nan'),float('inf'),float('-inf'),None,'0',True]:
                state,low = healthy()
                owner = state if key in state else low
                owner[key] = bad
                with self.subTest(field=key,value=bad):
                    self.assertTrue(RotationGuard().check(state,low,1000.))
        for key in ['rpy','position','velocity','temperatures','motor_lost','motor_faults']:
            for bad in [float('nan'),float('inf'),None,'0',True]:
                state,low = healthy()
                owner = state if key in state else low
                owner[key][0] = bad
                with self.subTest(field=key,value=bad):
                    self.assertTrue(RotationGuard().check(state,low,1000.))
        for now in [float('nan'),float('inf'),None,True]:
            self.assertTrue(self.guard.check(*healthy(),now))

    def test_timestamps_accept_age_quarter_second_and_reject_stale_future(self):
        state,low = healthy()
        state['at'] = low['at'] = 999.75
        self.assertIsNone(self.guard.check(state,low,1000.))
        for owner_name in ['state','low']:
            for timestamp in [999.749,1000.001]:
                state,low = healthy()
                owner = state if owner_name=='state' else low
                owner['at'] = timestamp
                with self.subTest(owner=owner_name,at=timestamp):
                    self.assertIn('过期',RotationGuard().check(state,low,1000.))

    def test_height_and_tilt_boundary_limits(self):
        for height in [.25,.40]:
            state,low = healthy(height=height)
            self.assertIsNone(RotationGuard().check(state,low,1000.))
        for height in [.249,.401]:
            state,low = healthy(height=height)
            self.assertIn('站高',RotationGuard().check(state,low,1000.))
        for index in [0,1]:
            for angle in [-.25,.25]:
                state,low = healthy()
                state['rpy'][index] = angle
                self.assertIn('倾角',RotationGuard().check(state,low,1000.))
            state,low = healthy()
            state['rpy'][index] = .2499
            self.assertIsNone(RotationGuard().check(state,low,1000.))

    def test_preflight_rejects_temperature_65_and_current_hot_state(self):
        for temperature in [65.,70.,84.]:
            temperatures = [40.]*12
            temperatures[0],temperatures[9] = temperature,74. if temperature==84. else 40.
            self.assertIn('65',self.check(temperatures=temperatures))
        self.assertIsNone(self.check(temperatures=[64.99]*12))

    def test_running_temperature_below_70_and_starting_flag_remains_stricter(self):
        self.start()
        self.assertIsNone(self.check(1001.,temperatures=[69.99]*12))
        self.assertIn('70',self.check(1002.,temperatures=[70.]*12))
        self.assertIn('65',self.check(1002.,temperatures=[66.]*12,starting=True))

    def test_unstarted_repeated_checks_keep_preflight_temperature_limit(self):
        self.assertIsNone(self.check())
        self.assertIn('65',self.check(1001.,temperatures=[65.]*12))

    def test_power_boundaries(self):
        self.assertIsNone(self.check(soc=30.,power_v=27.))
        self.assertIn('电量',self.check(soc=29.99))
        self.assertIn('电量',self.check(soc=101.))
        self.assertIn('电压',self.check(power_v=26.99))

    def test_implausible_temperature_voltage_and_planar_speed_reject(self):
        self.assertIsNone(self.check(temperatures=[-20.]*12,power_v=40.))
        self.assertIn('温度',self.check(temperatures=[-20.01]*12))
        self.assertIn('电压',self.check(power_v=40.01))
        for velocity in [[.2001,0.,0.],[-.2001,0.,0.],[.15,.15,0.]]:
            state,low = healthy(velocity=velocity)
            self.assertIn('速度',RotationGuard().check(state,low,1000.))
        state,low = healthy(velocity=[.20,0.,0.])
        self.assertIsNone(RotationGuard().check(state,low,1000.))

    def test_all_sixteen_remote_buttons_request_takeover(self):
        for bit in range(16):
            with self.subTest(button=bit):
                self.assertIn('接管',self.check(wireless_remote=remote(keys=1<<bit)))

    def test_each_official_axis_requests_takeover_in_both_directions(self):
        for offset in [4,8,12,20]:
            for axis in [-.031,.031]:
                with self.subTest(offset=offset,axis=axis):
                    self.assertIn('接管',self.check(wireless_remote=remote(offset=offset,axis=axis)))
            self.assertIsNone(self.check(wireless_remote=remote(offset=offset,axis=.03)))
        self.assertIsNone(self.check(wireless_remote=remote(offset=16,axis=1.)))

    def test_remote_nan_inf_and_invalid_bytes_are_rejected(self):
        for offset in [4,8,12,20]:
            for axis in [float('nan'),float('inf')]:
                self.assertIn('有限',self.check(wireless_remote=remote(offset=offset,axis=axis)))
        for bad in [-1,256,True,0.0,'0']:
            packet = remote()
            packet[0] = bad
            self.assertIn('字节',self.check(wireless_remote=packet))

    def test_motor_counter_increase_at_any_joint_rejects_and_baseline_unchanged(self):
        self.assertIsNone(self.check(motor_lost=[10]*12))
        for index in range(12):
            lost = [10]*12
            lost[index] = 11
            self.assertIn('新增',self.check(1001.,motor_lost=lost))
        self.assertIsNone(self.check(1001.,motor_lost=[10]*12))
        for bad in [-1,.5]:
            lost = [10]*12
            lost[0] = bad
            self.assertIn('计数',self.check(1001.,motor_lost=lost))

    def test_every_motor_fault_blocks_preflight_even_with_cool_temperatures(self):
        for index in range(12):
            for code in [1,4,255]:
                faults = [0]*12
                faults[index] = code
                with self.subTest(index=index,code=code):
                    self.assertIn('故障码非零',self.check(motor_faults=faults,temperatures=[35.]*12))
        with self.assertRaises(ValueError):
            self.guard.mark_started(1000.)

    def test_motor_fault_after_start_blocks_continuation_and_cooling_does_not_clear_it(self):
        self.start()
        faults = [0]*12
        faults[9] = 4
        self.assertIn('故障码非零',self.check(1001.,motor_faults=faults,temperatures=[75.]*12))
        self.assertIn('故障码非零',self.check(1002.,motor_faults=faults,temperatures=[35.]*12))
        self.assertEqual(0.,self.guard.progress()['angle_deg'])

    def test_motor_fault_requires_twelve_nonnegative_integer_fields(self):
        for faults in [None,[],[0]*11,[0]*13,0,'0'*12]:
            with self.subTest(faults=faults):
                self.assertIn('故障码',self.check(motor_faults=faults))
        for invalid in [-1,float('nan'),float('inf'),0.0,1.5,True,None,'0']:
            faults = [0]*12
            faults[9] = invalid
            with self.subTest(invalid=invalid):
                self.assertIn('非负整数',self.check(motor_faults=faults))
        state,low = healthy()
        del low['motor_faults']
        self.assertIn('缺失',self.guard.check(state,low,1000.))
        self.assertIsNone(self.check(motor_faults=[0]*12))

    def test_error_change_is_rejected_before_and_after_start(self):
        self.assertIsNone(self.check())
        state,low = healthy(1001.,error=0)
        self.assertIn('基线',self.guard.check(state,low,1001.))
        self.guard.mark_started(1001.)
        state,low = healthy(1002.,error=101)
        self.assertIn('基线',self.guard.check(state,low,1002.))
        state,low = healthy(1002.,error=100)
        self.assertIsNone(self.guard.check(state,low,1002.))

    def test_invalid_first_check_does_not_establish_reference(self):
        self.assertIn('65',self.check(yaw=60.,position=[10.,0.,0.],temperatures=[84.]*12))
        with self.assertRaises(ValueError):
            self.guard.mark_started(1000.)
        self.assertIsNone(self.check())
        self.assertEqual(0.,self.guard.progress()['angle_deg'])
        self.assertEqual(0.,self.guard.progress()['displacement_m'])

    def test_repeated_startup_checks_and_mark_started_do_not_reset_reference(self):
        self.assertIsNone(self.check())
        self.assertIsNone(self.check(1001.,yaw=30.,position=[.04,0.,0.]))
        self.guard.mark_started(1001.)
        self.assertIsNone(self.check(1002.,yaw=40.,position=[.05,0.,0.]))
        self.assertAlmostEqual(40.,self.guard.progress()['angle_deg'])
        self.assertAlmostEqual(.05,self.guard.progress()['displacement_m'])

    def test_yaw_wrap_is_continuous_in_both_directions(self):
        self.assertIsNone(self.check(yaw=179.))
        self.assertIsNone(self.check(1001.,yaw=-179.))
        self.assertAlmostEqual(2.,self.guard.progress()['angle_deg'])
        self.assertAlmostEqual(2.,self.guard.progress()['net_angle_deg'])
        self.assertIsNone(self.check(1002.,yaw=179.))
        self.assertAlmostEqual(4.,self.guard.progress()['angle_deg'])
        self.assertAlmostEqual(0.,self.guard.progress()['net_angle_deg'])

    def test_absolute_angle_accumulates_direction_changes_and_hard_stops_at_85(self):
        self.start()
        self.assertIsNone(self.check(1001.,yaw=30.))
        self.assertIsNone(self.check(1002.,yaw=0.))
        self.assertIn('85',self.check(1003.,yaw=25.))
        self.assertAlmostEqual(85.,self.guard.progress()['angle_deg'])
        self.assertAlmostEqual(25.,self.guard.progress()['net_angle_deg'])
        other = RotationGuard()
        state,low = healthy()
        self.assertIsNone(other.check(state,low,1000.))
        state,low = healthy(1001.,rpy=[0.,0.,math.radians(-85)])
        self.assertIn('85',other.check(state,low,1001.))
        self.assertAlmostEqual(-85.,other.progress()['net_angle_deg'])

    def test_three_dimensional_drift_is_limited_and_peak_is_retained(self):
        self.start()
        self.assertIsNone(self.check(1001.,position=[.1,0.,0.]))
        self.assertIn('位移',self.check(1002.,position=[0.,0.,.1001]))
        self.assertAlmostEqual(.1001,self.guard.progress()['displacement_m'])
        self.assertIn('位移',self.check(1003.,position=[0.,0.,0.]))
        self.assertAlmostEqual(.1001,self.guard.progress()['displacement_m'])

    def test_duration_stops_at_45_and_repeated_mark_does_not_extend_it(self):
        self.start()
        self.assertIsNone(self.check(1020.))
        self.guard.mark_started(1020.)
        self.assertIsNone(self.check(1044.99))
        self.assertIn('45秒',self.check(1045.))

    def test_mark_started_requires_preflight_and_finite_monotonic_time(self):
        with self.assertRaises(ValueError):
            self.guard.mark_started(1000.)
        self.assertIsNone(self.check())
        for now in [999.,float('nan'),float('inf'),True]:
            with self.subTest(now=now):
                with self.assertRaises(ValueError):
                    self.guard.mark_started(now)
        self.assertIsNone(self.check(1001.))
        self.assertIn('回退',self.check(1000.))

    def test_input_dicts_and_remote_packets_are_not_modified(self):
        state,low = healthy()
        before = copy.deepcopy((state,low))
        self.assertIsNone(self.guard.check(state,low,1000.))
        self.assertEqual(before,(state,low))


if __name__ == '__main__':
    unittest.main(verbosity=2)
