import os
import unittest
from unittest.mock import patch

from utils import config_api


class BundledModelDefaultsTests(unittest.TestCase):
    def test_clean_install_defaults_to_the_bundled_r1_endpoint(self):
        with patch.dict(
            os.environ,
            {
                "JAVIS_BUNDLED_MODEL": "deepseek-r1:8b",
                "JAVIS_BUNDLED_OLLAMA_URL": "http://127.0.0.1:11435/v1",
            },
            clear=False,
        ):
            config = config_api._default_config()

        self.assertEqual(config["model"]["provider"], "local")
        self.assertEqual(config["model"]["name"], "deepseek-r1:8b")
        self.assertEqual(config["model"]["local"]["name"], "deepseek-r1:8b")
        self.assertEqual(
            config["model"]["local"]["base_url"],
            "http://127.0.0.1:11435/v1",
        )


if __name__ == "__main__":
    unittest.main()
