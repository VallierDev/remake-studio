import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import tomllib
import unittest
from unittest.mock import patch

import local_flow2api
from flow2api import gateway_config


class LocalFlowTests(unittest.TestCase):
    def test_local_settings_are_private_and_stable(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "vendor"
            runtime = root / "private"
            (source / "config").mkdir(parents=True)
            (source / "main.py").touch()
            (source / "config/setting_example.toml").write_text(
                '[global]\napi_key = "han1234"\nadmin_password = "admin"\n'
                '[server]\nhost = "0.0.0.0"\nport = 8000\n')
            with patch.object(local_flow2api, "SOURCE", source), patch.object(local_flow2api, "RUNTIME", runtime):
                first = local_flow2api.configure()
                self.assertEqual(first, local_flow2api.configure())
            config = tomllib.loads((source / "config/setting.toml").read_text())
            self.assertEqual(config["global"]["api_key"], first["api_key"])
            self.assertNotEqual(first["api_key"], "han1234")
            self.assertEqual(config["server"], {"host": "127.0.0.1", "port": 18781})
            self.assertEqual((runtime / "credentials.json").stat().st_mode & 0o777, 0o600)
            self.assertTrue((source / "data").is_symlink())
            self.assertTrue((source / "config/setting.toml").is_symlink())

    def test_unconfigured_client_uses_project_service(self):
        with patch.dict(os.environ, {"FLOW2API_BASE_URL": "http://remote.invalid"}, clear=True), patch.object(
                local_flow2api, "credentials", return_value=("http://127.0.0.1:18781", {"Authorization": "test"})):
            self.assertEqual(gateway_config(None)[0], "http://127.0.0.1:18781")


if __name__ == "__main__":
    unittest.main()
