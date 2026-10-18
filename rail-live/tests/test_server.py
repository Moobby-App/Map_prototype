import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from server.main import app


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_static_files_and_private_paths(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/static/app.js").status_code, 200)
        self.assertEqual(self.client.get("/.env").status_code, 404)
        self.assertEqual(self.client.get("/keys/AuthKey_XXXX.p8").status_code, 404)

    def test_missing_credentials(self):
        with patch.dict(os.environ, {"TEAM_ID": "", "KEY_ID": "", "MAPKIT_PRIVATE_KEY_PATH": ""}):
            response = self.client.get("/token")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_normalized_data_allowlist(self):
        import server.main as main
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            (root / "data" / "stations.json").write_text("[]")
            with patch.object(main, "ROOT", root):
                response = self.client.get("/data/stations.json")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), [])
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertEqual(self.client.get("/data/trips.json").status_code, 404)
                self.assertEqual(self.client.get("/data/.env").status_code, 404)
                self.assertEqual(self.client.get("/data/raw").status_code, 404)

    def test_signed_token(self):
        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(serialization.Encoding.PEM,
                                serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.p8"
            path.write_bytes(pem)
            with patch.dict(os.environ, {"TEAM_ID": "TESTTEAM01", "KEY_ID": "TESTKEY001", "MAPKIT_PRIVATE_KEY_PATH": str(path)}):
                response = self.client.get("/token")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        body = response.json()
        claims = jwt.decode(body["token"], key.public_key(), algorithms=["ES256"], issuer="TESTTEAM01")
        self.assertEqual(jwt.get_unverified_header(body["token"])["kid"], "TESTKEY001")
        self.assertEqual(claims["origin"], "http://localhost:8000")
        self.assertEqual(claims["scope"], "mapkit_js")
        self.assertEqual(claims["exp"] - claims["iat"], 1800)
        self.assertEqual(body["expires_at"], claims["exp"])
        self.assertLessEqual(abs(time.time() - claims["iat"]), 5)

    def test_invalid_key_has_safe_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.p8"
            path.write_text("not a private key")
            with patch.dict(os.environ, {"TEAM_ID": "TESTTEAM01", "KEY_ID": "TESTKEY001", "MAPKIT_PRIVATE_KEY_PATH": str(path)}):
                response = self.client.get("/token")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(directory, response.text)


if __name__ == "__main__":
    unittest.main()
