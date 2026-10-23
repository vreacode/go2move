"""Native health checks for bounded mapping/Nav2 writers; no robot I/O.

Reuse the tested autonomy checks for temperature, faults, remote takeover,
telemetry freshness and startup stance. A route's geometry belongs to its
parent controller, so this guard does not impose the autonomy radius. The
independent worker deadline is at most 60 seconds from its first movement.
The 65/70 C thresholds are local conservative limits, not vendor ratings.
"""
import math

from autonomy_guard import AutonomyGuard


class MotionHealthGuard(AutonomyGuard):
    def __init__(self, duration=45.):
        duration = self._number(duration, '运动写入器时限')
        if not 1 <= duration <= 60:
            raise ValueError('Motion writer duration must be within 1..60 seconds')
        super().__init__(radius=3., duration=60.)
        self.MAX_DISPLACEMENT_M = math.inf
        self.MAX_DURATION_S = duration

    def check(self, state, low, now, starting=False):
        reason = super().check(state, low, now, starting=starting)
        if reason and '秒时限' in reason:
            return '运动写入器达到%g秒时限，保持停止。' % self.MAX_DURATION_S
        return reason
