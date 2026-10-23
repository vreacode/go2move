#!/usr/bin/env python3
"""Pure synthetic telemetry, including worker-specific limits and fail-closed input."""
import math
import unittest

from motion_health_guard import MotionHealthGuard
from test_rotation_guard import healthy, remote


def sample(now=1000., **changes):
    state,low=healthy(now,**changes)
    state['yaw_speed']=0.
    low.update(bit_flag=4,tick=int(now*1000))
    return state,low


class MotionHealthGuardTests(unittest.TestCase):
    def test_duration_requires_finite_numeric_value_within_one_to_sixty_seconds(self):
        for value in (1.,45.,60.):
            self.assertEqual(value,MotionHealthGuard(value).MAX_DURATION_S)
        for value in (0.,.99,60.01,math.inf,math.nan,True,None,'45'):
            with self.subTest(value=value),self.assertRaises(ValueError):MotionHealthGuard(value)

    def test_first_movement_deadline_cannot_be_extended_by_repeated_start_calls(self):
        guard=MotionHealthGuard(2.)
        self.assertIsNone(guard.check(*sample(),1000.))
        guard.mark_started(1000.)
        self.assertIsNone(guard.check(*sample(1001.99),1001.99))
        guard.mark_started(1001.99)
        self.assertIn('2秒',guard.check(*sample(1002.),1002.))

    def test_route_translation_and_turning_do_not_inherit_rotation_only_limits(self):
        guard=MotionHealthGuard()
        self.assertIsNone(guard.check(*sample(),1000.));guard.mark_started(1000.)
        self.assertIsNone(guard.check(*sample(1001.,position=[4.,0.,0.],rpy=[0.,0.,2.]),1001.))
        self.assertIsNone(guard.check(*sample(1002.,position=[8.,1.,0.],rpy=[0.,0.,-2.]),1002.))
        self.assertIn('高度',guard.check(*sample(1002.1,position=[8.,1.,.081]),1002.1))

    def test_temperature_uses_stricter_startup_limit_and_keeps_runtime_limit(self):
        state,low=sample();low['temperatures'][9]=65.
        self.assertIn('65',MotionHealthGuard().check(state,low,1000.))
        guard=MotionHealthGuard();self.assertIsNone(guard.check(*sample(),1000.));guard.mark_started(1000.)
        state,low=sample(1000.1);low['temperatures']=[69.99]*12
        self.assertIsNone(guard.check(state,low,1000.1))
        low['temperatures'][0]=70.
        self.assertIn('70',guard.check(state,low,1000.1))

    def test_every_joint_fault_and_active_system_fault_bit_blocks_startup(self):
        for index in range(12):
            state,low=sample();low['motor_faults'][index]=4
            self.assertIn('故障码',MotionHealthGuard().check(state,low,1000.))
        for bit in (0x40,0x80,0x10,0x02):
            state,low=sample();low['bit_flag']|=bit
            self.assertIn('系统故障',MotionHealthGuard().check(state,low,1000.))

    def test_locked_stance_and_nonstationary_start_are_rejected(self):
        for error in (1001,1002):
            self.assertIn('START',MotionHealthGuard().check(*sample(error=error),1000.))
        self.assertIn('停稳',MotionHealthGuard().check(*sample(velocity=[.026,0.,0.]),1000.))

    def test_buttons_axes_and_invalid_remote_packets_all_block(self):
        for packet in ([0]*39,remote(keys=1),remote(offset=4,axis=.031),
                       remote(offset=8,axis=-.031),remote(offset=12,axis=.031),
                       remote(offset=20,axis=-.031),remote(offset=4,axis=math.nan)):
            state,low=sample();low['wireless_remote']=packet
            self.assertTrue(MotionHealthGuard().check(state,low,1000.))

    def test_missing_telemetry_and_frozen_native_tick_cannot_look_healthy(self):
        for key in ('temperatures','motor_faults','motor_lost','wireless_remote','bit_flag','tick'):
            state,low=sample();del low[key]
            self.assertTrue(MotionHealthGuard().check(state,low,1000.))
        guard=MotionHealthGuard();self.assertIsNone(guard.check(*sample(),1000.))
        state,low=sample(1000.251);low['tick']=1000000
        self.assertIn('冻结',guard.check(state,low,1000.251))


if __name__=='__main__':unittest.main(verbosity=2)
