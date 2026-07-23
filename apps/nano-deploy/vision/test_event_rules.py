import unittest

from event_rules import Detection, IllegalParkingRule, IoUTracker, NoParkingZone


class EventRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.zone = NoParkingZone("gate_a_no_parking", 0.25, 0.25, 0.75, 0.9)

    def vehicle(self, track_id=None, x1=300, y1=180, x2=500, y2=500, confidence=0.9):
        return Detection("vehicle", confidence, (x1, y1, x2, y2), track_id)

    def test_dwell_before_event_and_event_after_threshold(self):
        rule = IllegalParkingRule(self.zone, dwell_seconds=5.0, cooldown_seconds=30.0)
        self.assertEqual(rule.update(0.0, [self.vehicle(1)], (800, 600)), [])
        self.assertEqual(rule.update(4.9, [self.vehicle(1)], (800, 600)), [])
        events = rule.update(5.0, [self.vehicle(1)], (800, 600))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "illegal_parking")
        self.assertEqual(events[0].track_id, 1)

    def test_leaving_zone_resets_dwell(self):
        rule = IllegalParkingRule(self.zone, dwell_seconds=5.0)
        rule.update(0.0, [self.vehicle(1)], (800, 600))
        rule.update(4.0, [self.vehicle(1, x1=0, x2=100)], (800, 600))
        self.assertEqual(rule.update(6.0, [self.vehicle(1)], (800, 600)), [])

    def test_cooldown_suppresses_repeated_events(self):
        rule = IllegalParkingRule(self.zone, dwell_seconds=1.0, cooldown_seconds=10.0)
        rule.update(0.0, [self.vehicle(1)], (800, 600))
        self.assertEqual(len(rule.update(1.0, [self.vehicle(1)], (800, 600))), 1)
        self.assertEqual(rule.update(2.0, [self.vehicle(1)], (800, 600)), [])
        self.assertEqual(len(rule.update(11.0, [self.vehicle(1)], (800, 600))), 1)

    def test_tracker_keeps_id_for_overlapping_boxes(self):
        tracker = IoUTracker(iou_threshold=0.2)
        first = tracker.update(0.0, [self.vehicle()])
        second = tracker.update(1.0, [self.vehicle(x1=310, x2=510)])
        self.assertEqual(first[0].track_id, second[0].track_id)


if __name__ == "__main__":
    unittest.main()
