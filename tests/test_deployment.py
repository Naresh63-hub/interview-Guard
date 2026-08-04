import importlib
import unittest
from pathlib import Path

import yaml


class DeploymentConfigTestCase(unittest.TestCase):
    def test_render_start_command_points_to_existing_app(self):
        config = yaml.safe_load(Path("render.yaml").read_text())
        service = config["services"][0]

        self.assertEqual(service["startCommand"], "gunicorn -k eventlet -w 1 app:app")

        module_name, app_name = service["startCommand"].rsplit(" ", 1)[-1].split(":")
        module = importlib.import_module(module_name)
        self.assertTrue(hasattr(module, app_name))


if __name__ == "__main__":
    unittest.main()
