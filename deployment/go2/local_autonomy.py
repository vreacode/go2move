"""Dependency-free, bounded local roaming. No ROS or robot command imports.

Plan forward segments and stationary turns against a conservative circular
footprint. This is local obstacle avoidance, not map navigation or drop detection.
The existing Nav2 test footprint (+/-0.45, +/-0.35 m), including its 0.01 m
padding, is enclosed by the circular footprint used here.
"""
from dataclasses import dataclass
import math

# Keep these consistent with nav2_motion_test.yaml; regression checks the file.
BODY_HALF_LENGTH = .45
BODY_HALF_WIDTH = .35
FOOTPRINT_PADDING = .01
BODY_RADIUS = math.hypot(BODY_HALF_LENGTH + FOOTPRINT_PADDING, BODY_HALF_WIDTH + FOOTPRINT_PADDING)


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Expected a finite number')
    return float(value)


def angle(value):
    return math.atan2(math.sin(value), math.cos(value))


@dataclass(frozen=True)
class Limits:
    radius: float = 1.0
    speed: float = .08
    yaw_speed: float = .15
    body_radius: float = BODY_RADIUS
    margin: float = .12
    lookahead: float = .45
    boundary_margin: float = .15

    def __post_init__(self):
        for key in self.__dataclass_fields__:
            number(getattr(self, key))
        if not 1 <= self.radius <= 3 or not .04 <= self.speed <= .10:
            raise ValueError('Radius must be 1..3 m; speed must be .04...10 m/s')
        if not .05 <= self.yaw_speed <= .15:
            raise ValueError('Yaw speed must be .05...15 rad/s')
        if self.body_radius < BODY_RADIUS or self.margin < .12:
            raise ValueError('Do not shrink the existing footprint or clearance')
        if not .35 <= self.lookahead <= .8 or self.boundary_margin < .15:
            raise ValueError('Insufficient lookahead or boundary margin')

    @property
    def clearance(self):
        return self.body_radius + self.margin


def scan_points(ranges, angle_min, angle_increment, range_min, range_max, frame):
    """Require a complete, populated 360 degree scan in the robot base frame.

    Positive infinity is the converter's no-return convention. An entirely empty
    scan, NaN, below-range return, or a missing quadrant never establishes free space.
    """
    a, step, lo, hi = map(number, (angle_min, angle_increment, range_min, range_max))
    if frame != 'base_footprint' or not .001 <= step <= .05 or not 0 < lo <= .45 or hi < 2:
        raise ValueError('Invalid scan frame, resolution, or range limits')
    if not 2 * math.pi - .06 <= step * len(ranges) <= 2 * math.pi + .06:
        raise ValueError('A complete 360 degree scan is required')
    points, quadrants = [], [0] * 4
    for index, raw in enumerate(ranges):
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError('Invalid laser sample')
        distance = float(raw)
        if distance == math.inf:
            continue
        if not math.isfinite(distance) or not lo <= distance <= hi:
            raise ValueError('Invalid or below-minimum laser return; stop')
        bearing = a + index * step
        points.append((distance * math.cos(bearing), distance * math.sin(bearing)))
        quadrants[int((bearing % (2 * math.pi)) / (math.pi / 2)) % 4] += 1
    if len(points) < 12 or min(quadrants) < 2:
        raise ValueError('Scan is empty or has insufficient surrounding observations')
    return points


class LocalRoamer:
    def __init__(self, limits=None):
        self.limits = limits or Limits()
        self.origin = None
        self.target_yaw = None
        self.turn_reason = None
        self.turn_started = None
        self.preferred_turn = 1
        self.last_pose = None
        self.last_at = None
        self.fault = None
        self.max_displacement = 0.
        self.progress_at = None
        self.progress_pose = None
        self.turn_progress_at = None
        self.turn_best_error = None
        self.forward_settling = True
        self.settling_after_turn = False
        self.settled_since = None

    @staticmethod
    def _result(state, reason, velocity=(0., 0., 0.), **extra):
        return dict(state=state, reason=reason, velocity=list(velocity), **extra)

    def _fail(self, reason):
        self.fault = reason
        return self._result('fault', reason)

    def _segment_clear(self, heading, pose, points):
        """Exact point-to-segment distances; a circle is convex at the boundary."""
        limit = self.limits
        c, s = math.cos(heading), math.sin(heading)
        for px, py in points:
            along = max(0., min(limit.lookahead, px * c + py * s))
            if (px - along * c)**2 + (py - along * s)**2 <= limit.clearance**2:
                return False
        for distance in (0., limit.lookahead):
            x = pose[0] + distance * math.cos(pose[2] + heading)
            y = pose[1] + distance * math.sin(pose[2] + heading)
            if math.hypot(x - self.origin[0], y - self.origin[1]) > limit.radius - limit.boundary_margin:
                return False
        return True

    def step(self, pose, points, now, measured_speed=0., measured_yaw_speed=0., monitor_progress=False):
        if self.fault:
            return self._result('fault', self.fault)
        try:
            if len(pose) != 3:
                raise ValueError('Invalid odometry pose')
            pose = tuple(map(number, pose))
            now, measured_speed = number(now), number(measured_speed)
            measured_yaw_speed = number(measured_yaw_speed)
            if measured_speed < 0 or measured_speed > .20:
                raise ValueError('Measured speed exceeds the autonomous limit')
            if abs(measured_yaw_speed) > .30:
                raise ValueError('Measured yaw speed exceeds the autonomous limit')
            if len(points) < 12 or any(len(p) != 2 or not all(math.isfinite(number(v)) for v in p) for p in points):
                raise ValueError('Insufficient or invalid obstacle observations')
            if self.last_at is not None:
                dt = now - self.last_at
                if not 0 < dt <= .35:
                    raise ValueError('Control deadline missed or clock moved backwards')
                if math.hypot(pose[0] - self.last_pose[0], pose[1] - self.last_pose[1]) > .03 + .25 * dt:
                    raise ValueError('Odometry jumped or the robot moved too fast')
                if abs(angle(pose[2] - self.last_pose[2])) > .04 + .4 * dt:
                    raise ValueError('Odometry heading jumped')
            self.last_pose, self.last_at = pose, now
            if self.origin is None:
                self.origin = pose
            displacement = math.hypot(pose[0] - self.origin[0], pose[1] - self.origin[1])
            self.max_displacement = max(self.max_displacement, displacement)
            if displacement >= self.limits.radius - .05:
                return self._fail('Activity boundary reached; manual restart required')
            if min(math.hypot(x, y) for x, y in points) <= self.limits.clearance:
                return self._fail('Obstacle inside body clearance; no safe stationary turn')

            forward_clear = self._segment_clear(0., pose, points)
            if self.target_yaw is not None:
                self.progress_at = None
                error = angle(self.target_yaw - pose[2])
                if monitor_progress:
                    if self.turn_best_error is None or abs(error) < self.turn_best_error - .03:
                        self.turn_best_error, self.turn_progress_at = abs(error), now
                    elif now - self.turn_progress_at > 5:
                        return self._fail('No turning progress for 5 seconds; stop')
                else:
                    self.turn_best_error = None
                if monitor_progress and now - self.turn_started > 35:
                    return self._fail('Turn made no sufficient progress within 35 seconds')
                if abs(error) <= .10:
                    self.target_yaw = None
                    self.forward_settling = True
                    self.settling_after_turn = True
                    self.settled_since = None
                    # Stop between turn and walking; re-evaluate on the next cycle.
                    return self._result('settling', 'Turn complete; checking forward corridor')
                if measured_speed > .025:
                    return self._result('braking', 'Stopping translation before turning')
                return self._result('turning', self.turn_reason,
                                    (0., 0., math.copysign(self.limits.yaw_speed, error)))
            if forward_clear:
                # Stationarity gates a transition out of a commanded turn. Normal
                # walking has small yaw oscillations; reapplying this gate every
                # cycle repeatedly sent StopMove during otherwise clear walking.
                if self.forward_settling:
                    if abs(measured_yaw_speed) > .035 or measured_speed > .025:
                        self.settled_since = None
                        return self._result('settling', 'Stopping rotation before walking')
                    if self.settling_after_turn:
                        if self.settled_since is None:
                            self.settled_since = now
                        if now - self.settled_since < .25:
                            return self._result('settling', 'Waiting for a stable stop after turning')
                    self.forward_settling = False
                    self.settling_after_turn = False
                    self.settled_since = None
                if monitor_progress:
                    if self.progress_at is None or math.hypot(pose[0]-self.progress_pose[0],pose[1]-self.progress_pose[1]) >= .015:
                        self.progress_at, self.progress_pose = now, pose
                    elif now - self.progress_at > 5:
                        return self._fail('No walking progress for 5 seconds; stop')
                else:
                    self.progress_at = None
                return self._result('walking', 'Forward corridor and activity boundary are clear',
                                    (self.limits.speed, 0., 0.))

            # Choose an actual collision-free segment, not merely a large range ray.
            inward = angle(math.atan2(self.origin[1] - pose[1], self.origin[0] - pose[0]) - pose[2])
            candidates = [math.radians(d) for d in (30, -30, 60, -60, 90, -90, 120, -120, 150, -150, 180)]
            if displacement > .2:
                candidates.append(inward)
            safe = [h for h in candidates if abs(h) > .15 and self._segment_clear(h, pose, points)]
            if not safe:
                return self._result('blocked', 'No safe direction; waiting without moving')
            near_boundary = displacement + self.limits.lookahead > self.limits.radius - self.limits.boundary_margin
            def score(heading):
                turn_bias = -.03 if heading * self.preferred_turn > 0 else 0.
                return (abs(angle(heading - inward)) if near_boundary else abs(heading)) + turn_bias
            chosen = min(safe, key=score)
            self.preferred_turn = 1 if chosen > 0 else -1
            self.target_yaw = angle(pose[2] + chosen)
            self.turn_started = now
            self.turn_best_error = None
            self.progress_at = None
            self.turn_reason = 'Turning toward the activity center' if near_boundary else 'Turning toward a clear obstacle-free corridor'
            return self._result('braking', self.turn_reason)
        except (ValueError, TypeError, OverflowError) as exc:
            return self._fail(str(exc))


class FreshInputs:
    """Track source and receipt clocks independently; replay cannot refresh data."""
    def __init__(self):
        self.samples = {}
        self.errors = {}

    def receive(self, key, value, stamp, received_at, wall_now):
        try:
            stamp, received_at, wall_now = map(number, (stamp, received_at, wall_now))
            if not -.05 <= wall_now - stamp <= .30:
                raise ValueError(key + ' source timestamp is stale or in the future')
            previous = self.samples.get(key)
            if previous and stamp < previous['stamp']:
                raise ValueError(key + ' source timestamp moved backwards')
            if previous and stamp == previous['stamp']:
                return False
            self.samples[key] = dict(value=value, stamp=stamp, at=received_at)
            self.errors.pop(key, None)
            return True
        except (ValueError, TypeError) as exc:
            self.errors[key] = str(exc)
            return False

    def reason(self, now, wall_now):
        for key in ('scan', 'odom', 'cloud'):
            if key in self.errors:
                return self.errors[key]
            sample = self.samples.get(key)
            if sample is None:
                return 'Waiting for ' + key
            if not 0 <= now - sample['at'] <= .30 or not -.05 <= wall_now - sample['stamp'] <= .30:
                return key + ' data is stale'
        return None
