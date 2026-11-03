import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from product_migration_agent import api, config
from product_migration_agent.test_service import FakeLLM, inputs


class ApiTests(unittest.TestCase):
    def test_convert_then_download(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(config, "CACHE_FILE", Path(tmp) / "c.json"), \
             mock.patch("agent.llm.get_llm_client", return_value=FakeLLM(), create=True):
            app = FastAPI()
            app.include_router(api.router)
            c = TestClient(app)
            d, m, r = inputs()
            files = {"data": ("d.xlsx", d), "mapping": ("m.xlsx", m), "rules": ("r.xlsx", r)}
            try:
                res = c.post("/migration/convert", files=files)
            except ModuleNotFoundError:
                self.skipTest("agent.llm dependencies not installed in this environment")
            self.assertEqual(res.status_code, 200, res.text)
            body = res.json()
            self.assertEqual(body["summary"]["records"], 3)
            dl = c.get(body["download_url"])
            self.assertEqual(dl.status_code, 200)
            self.assertTrue(dl.content.startswith(b"PK"))
            self.assertEqual(c.get("/migration/download/nope").status_code, 404)

    def test_bad_upload_is_400(self):
        app = FastAPI()
        app.include_router(api.router)
        files = {"data": ("d.xlsx", b"junk"), "mapping": ("m.xlsx", b"junk"), "rules": ("r.xlsx", b"junk")}
        self.assertEqual(TestClient(app).post("/migration/convert", files=files).status_code, 400)


if __name__ == "__main__":
    unittest.main()
