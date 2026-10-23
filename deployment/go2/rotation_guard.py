"""Pure telemetry guard for a bounded rotation; never sends robot commands.

65 C before starting and 70 C while running are conservative thresholds chosen
for this software experiment, not manufacturer limits. The caller must latch any
rejection and stop through its existing worker. All timestamps are monotonic.
"""
import math
from numbers import Integral, Real
import struct


class RotationGuard:
    MAX_AGE_S = .25
    MAX_ANGLE_DEG = 85.0
    MAX_DISPLACEMENT_M = .10
    MAX_DURATION_S = 45.0

    def __init__(self):
        self._position = None
        self._error = None
        self._lost = None
        self._last_yaw = None
        self._last_now = None
        self._started_at = None
        self._angle = 0.0
        self._net_angle = 0.0
        self._displacement = 0.0

    @staticmethod
    def _number(value, label):
        if isinstance(value,bool) or not isinstance(value,Real) or not math.isfinite(value):
            raise ValueError(label+'缺失或不是有限数值。')
        return float(value)

    @classmethod
    def _vector(cls, value, count, label):
        if not isinstance(value,(list,tuple)) or len(value) != count:
            raise ValueError(label+'缺失或长度不正确。')
        return tuple(cls._number(item,label) for item in value)

    @classmethod
    def _counter(cls, value, label):
        number = cls._number(value,label)
        if number < 0 or not number.is_integer():
            raise ValueError(label+'不是有效计数。')
        return int(number)

    @classmethod
    def _remote(cls, value):
        # Official SDK example/wireless_controller/wireless_controller.py:
        # keys: bytes 2,3; lx/rx/ry/ly: float32 offsets 4,8,12,20.
        # Offset 16 is an unused placeholder in that example.
        if not isinstance(value,(bytes,bytearray,list,tuple)) or len(value) != 40:
            raise ValueError('手柄状态缺失或长度不正确。')
        if any(isinstance(item,bool) or not isinstance(item,int) or not 0 <= item <= 255 for item in value):
            raise ValueError('手柄状态含无效字节。')
        packet = bytes(value)
        keys = struct.unpack_from('<H',packet,2)[0]
        axes = [struct.unpack_from('<f',packet,offset)[0] for offset in (4,8,12,20)]
        if not all(math.isfinite(axis) for axis in axes):
            raise ValueError('手柄摇杆状态不是有限数值。')
        if keys or any(abs(axis) > .03 for axis in axes):
            raise ValueError('手柄正在接管，停止扫描。')

    def mark_started(self, now):
        now = self._number(now,'开始时间')
        if self._position is None:
            raise ValueError('必须先通过健康预检才能开始。')
        if now < self._last_now:
            raise ValueError('开始时间早于最近健康预检。')
        if self._started_at is None:
            self._started_at = now  # Repeated calls must not extend the deadline.

    def check(self, state, low, now, starting=False):
        try:
            now = self._number(now,'检查时间')
            if not isinstance(state,dict) or not isinstance(low,dict):
                raise ValueError('运动或低层状态缺失。')
            for sample,label in ((state,'运动状态'),(low,'低层状态')):
                age = now-self._number(sample.get('at'),label+'时间')
                if age < 0 or age > self.MAX_AGE_S:
                    raise ValueError(label+'已过期或时间在未来。')
            if self._last_now is not None and now < self._last_now:
                raise ValueError('检查时钟回退，停止扫描。')
            height = self._number(state.get('height'),'站高')
            rpy = self._vector(state.get('rpy'),3,'姿态')
            position = self._vector(state.get('position'),3,'位置')
            velocity = self._vector(state.get('velocity'),3,'速度')
            error = self._counter(state.get('error'),'错误状态')
            temperatures = self._vector(low.get('temperatures'),12,'电机温度')
            soc = self._number(low.get('soc'),'电量')
            voltage = self._number(low.get('power_v'),'电压')
            lost_values = self._vector(low.get('motor_lost'),12,'电机丢包计数')
            lost = tuple(self._counter(value,'电机丢包计数') for value in lost_values)
            faults = low.get('motor_faults')
            if not isinstance(faults,(list,tuple)) or len(faults) != 12:
                raise ValueError('电机故障码缺失或长度不正确，停止扫描。')
            if any(isinstance(value,bool) or not isinstance(value,Integral) or value < 0 for value in faults):
                raise ValueError('电机故障码不是有效非负整数，停止扫描。')
            if any(value != 0 for value in faults):
                raise ValueError('电机故障码非零，禁止启动或继续扫描。')
            self._remote(low.get('wireless_remote'))
            if not .25 <= height <= .40:
                raise ValueError('站高超出25至40厘米范围。')
            if max(abs(rpy[0]),abs(rpy[1])) >= .25:
                raise ValueError('倾角达到0.25弧度限制，停止扫描。')
            if math.hypot(velocity[0],velocity[1]) > .20:
                raise ValueError('平面速度超过0.20米每秒，停止扫描。')
            if min(temperatures) < -20:
                raise ValueError('电机温度低于-20℃，遥测无效。')
            temperature_limit = 65 if starting or self._started_at is None else 70
            if max(temperatures) >= temperature_limit:
                raise ValueError('电机温度达到本次软件保守阈值'+str(temperature_limit)+'℃。')
            if not 30 <= soc <= 100:
                raise ValueError('电量低于30%或无效。')
            if not 27 <= voltage <= 40:
                raise ValueError('电压超出27至40伏范围。')
            if self._position is None:
                # The first fully healthy check establishes immutable references.
                self._position, self._error, self._lost = position, error, lost
                self._last_yaw = rpy[2]
            elif error != self._error:
                raise ValueError('错误状态较预检基线发生变化，停止扫描。')
            elif any(current > initial for current,initial in zip(lost,self._lost)):
                raise ValueError('电机丢包计数较预检基线新增，停止扫描。')
            delta = math.atan2(math.sin(rpy[2]-self._last_yaw),math.cos(rpy[2]-self._last_yaw))
            self._angle += abs(delta)
            self._net_angle += delta
            self._last_yaw = rpy[2]
            self._last_now = now
            self._displacement = max(self._displacement,math.sqrt(sum(
                (current-initial)**2 for current,initial in zip(position,self._position))))
            if math.degrees(self._angle) >= self.MAX_ANGLE_DEG-1e-9:
                raise ValueError('累计转角达到85度限制，停止扫描。')
            if self._displacement > self.MAX_DISPLACEMENT_M:
                raise ValueError('位移超过0.10米，停止扫描。')
            if self._started_at is not None and now-self._started_at >= self.MAX_DURATION_S:
                raise ValueError('扫描达到45秒时限，停止扫描。')
            return None
        except (ValueError,TypeError,OverflowError) as exc:
            return str(exc) or '遥测无效，停止扫描。'

    def progress(self):
        return {'angle_deg':math.degrees(self._angle),
                'net_angle_deg':math.degrees(self._net_angle),
                'displacement_m':self._displacement}
