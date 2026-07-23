import unittest

from pydantic import ValidationError

from .schemas import ModelStartRequest


class ParkingZoneSchemaTests(unittest.TestCase):
    def test_parking_zone_is_optional(self):
        request = ModelStartRequest(model_id="test")
        self.assertIsNone(request.parking_zone)

    def test_accepts_normalized_positive_area(self):
        request = ModelStartRequest(
            model_id="test",
            parking_zone=[0.25, 0.25, 0.75, 0.9],
        )
        self.assertEqual(request.parking_zone, [0.25, 0.25, 0.75, 0.9])

    def test_rejects_out_of_range_or_non_positive_area(self):
        invalid_zones = (
            [0.0, 0.0, 1.1, 0.9],
            [0.8, 0.1, 0.2, 0.9],
            [0.1, 0.2, 0.9],
        )
        for zone in invalid_zones:
            with self.subTest(zone=zone):
                with self.assertRaises(ValidationError):
                    ModelStartRequest(model_id="test", parking_zone=zone)


if __name__ == "__main__":
    unittest.main()
