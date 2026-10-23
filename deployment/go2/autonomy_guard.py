"""Independent native-telemetry guard for the bounded SDK autonomy worker."""
import math

from local_autonomy import number
from rotation_guard import RotationGuard


class AutonomyGuard(RotationGuard):
    # A circular footprint covers the swept area of every stationary rotation.
    MAX_ANGLE_DEG = math.inf

    def __init__(self, radius=1., duration=30.):
        super().__init__()
        if not 1 <= number(radius) <= 3 or not 5 <= number(duration) <= 60:
            raise ValueError('Autonomy guard requires radius 1..3 m and duration 5..60 s')
        self.MAX_DISPLACEMENT_M = radius - .05
        self.MAX_DURATION_S = duration
        self._last_tick = None
        self._tick_at = None

    def check(self, state, low, now, starting=False, settling=False):
        reason = super().check(state, low, now, starting=starting)
        if reason:
            return reason
        try:
            # On this MCF robot, locked-stand telemetry was observed as 1001/1002.
            # StopMove from that state caused a body shift before any forward
            # request. Require the operator's START unlock before preparation;
            # never reinterpret this posture change as autonomous readiness.
            if state['error'] in (1001, 1002):
                raise ValueError('MCF standing state is locked; operator must unlock with START before autonomy')
            flags = self._counter(low.get('bit_flag'), '系统状态')
            # Use the existing health decoder's documented active fault bits.
            if flags & (0x40 | 0x80 | 0x10 | 0x02):
                raise ValueError('系统故障标志非零，停止自主行走。')
            tick = self._counter(low.get('tick'), '低层时钟')
            if self._last_tick is not None and tick < self._last_tick:
                raise ValueError('机器人低层时钟回退，停止自主行走。')
            if tick != self._last_tick:
                self._tick_at = now
            self._last_tick = tick
            if now - self._tick_at > .25:
                raise ValueError('机器人低层数据冻结，停止自主行走。')
            if abs(state['position'][2] - self._position[2]) > .08:
                raise ValueError('机身高度发生异常变化，停止自主行走。')
            speed = math.hypot(*state['velocity'][:2])
            yaw_speed = self._number(state.get('yaw_speed'), '角速度')
            if abs(yaw_speed) > .30:
                raise ValueError('实际角速度超过0.30弧度每秒，停止自主行走。')
            if not settling and self._started_at is None and (speed > .025 or abs(yaw_speed) > .04):
                raise ValueError('启动前机器狗尚未停稳。')
            return None
        except (ValueError, TypeError, KeyError) as exc:
            return str(exc)
