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

    def test_push_uses_saved_oracle_source_mapping(self):
        app = FastAPI()
        app.include_router(api.router)
        client = TestClient(app)
        records = [{"MATNR": "P-1", "MTART": "FERT"}]
        source_map = [{"field": "MTART", "oracle_field": "Oracle Product Type", "sap_description": "Product Type"}]
        job_id = api.JOBS.put(b"workbook", records, [], source_map)
        result = {"status": "ok"}
        with mock.patch("product_migration_agent.api.push_products", return_value=result) as push:
            response = client.post(f"/migration/push/{job_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), result)
        push.assert_called_once_with(records, set(), field_map=source_map)


if __name__ == "__main__":
    unittest.main()
