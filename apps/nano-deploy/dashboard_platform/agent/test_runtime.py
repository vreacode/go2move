import asyncio
import tempfile
import unittest
from pathlib import Path

from .config import AgentSettings
from .logs import LogBuffer
from .runtime import ModelProcessManager
from .schemas import ModelStartRequest


class ModelPreflightTests(unittest.TestCase):
    def test_relative_weight_resolves_under_model_workdir(self):
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory)
            settings = AgentSettings(model_workdir=workdir)
            manager = ModelProcessManager(settings, LogBuffer(20))
            self.assertEqual(manager.resolve_model_weight("weights/model.pt"), (workdir / "weights/model.pt").resolve())

    def test_start_rejects_missing_local_weight_before_spawning(self):
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory)
            settings = AgentSettings(
                model_workdir=workdir,
                available_models='[{"id":"test","label":"Test","weight":"missing.pt"}]',
                camera_rtsp_visible_url="rtsp://test/video1",
            )
            manager = ModelProcessManager(settings, LogBuffer(20))
            with self.assertRaisesRegex(RuntimeError, "not available locally"):
                asyncio.run(manager.start(ModelStartRequest(model_id="test")))
            self.assertIsNone(manager.process)


if __name__ == "__main__":
    unittest.main()
