import io
import json
import unittest
from unittest.mock import patch
from urllib.error import URLError

import nearby


class GeocodeFallbackTests(unittest.TestCase):
    def test_photon_fallback_uses_supported_parameters(self):
        nearby._geocode_cache.clear()
        response = io.BytesIO(json.dumps({"features": [{"geometry": {"coordinates": [129.31, 35.54]}}]}).encode())
        with patch("nearby.urlopen", side_effect=[URLError("unavailable"), response]) as fetch:
            self.assertEqual(nearby.geocode("울산"), (35.54, 129.31))
            self.assertNotIn("lang=ko", fetch.call_args.args[0].full_url)

