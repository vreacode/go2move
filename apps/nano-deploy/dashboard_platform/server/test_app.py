import unittest

from pydantic import ValidationError

from .app import ModelStartRequest


class ModelStartRequestTests(unittest.TestCase):
    def test_accepts_normalized_parking_zone(self):
        request = ModelStartRequest(
            model_id="test",
            parking_zone=[0.2, 0.2, 0.8, 0.9],
        )
        self.assertEqual(request.parking_zone, [0.2, 0.2, 0.8, 0.9])

    def test_rejects_invalid_parking_zone_before_forwarding(self):
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
