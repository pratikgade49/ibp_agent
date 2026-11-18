import io
import zipfile

import pytest

from rag import sharepoint_knowledge as sp
from rag.knowledge import retrieve


def test_word_extraction():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Capacity guidance</w:t></w:r></w:p></w:body></w:document>')
    assert sp._text("guide.docx", buf.getvalue()) == "Capacity guidance"


def test_sync_and_retrieve_with_source_link(monkeypatch):
    for key, value in {"SHAREPOINT_TENANT_ID": "tenant", "SHAREPOINT_CLIENT_ID": "client", "SHAREPOINT_CLIENT_SECRET": "secret", "SHAREPOINT_DRIVE_ID": "drive", "SHAREPOINT_FOLDER_ID": "folder"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(sp, "_documents", [])
    class Response:
        def __init__(self, body=None, content=b""):
            self.body, self.content = body, content
        def raise_for_status(self): pass
        def json(self): return self.body
        def iter_content(self, size): yield self.content
        def close(self): pass
    monkeypatch.setattr(sp.requests, "post", lambda *a, **kw: Response({"access_token": "token"}))
    def get(url, **kwargs):
        if "/content" in url:
            return Response(content=b"Investigate PCAPAUSAGE when capacity is constrained.")
        return Response({"value": [{"id": "item", "name": "guide.txt", "file": {}, "size": 60, "webUrl": "https://example.sharepoint.com/guide", "lastModifiedDateTime": "2026-01-01T00:00:00Z"}]})
    monkeypatch.setattr(sp.requests, "get", get)
    assert sp.sync()["documents"] == 1
    result = retrieve("PCAPAUSAGE")
    assert result[0]["source"] == "guide.txt"
    assert result[0]["url"] == "https://example.sharepoint.com/guide"


def test_failed_sync_preserves_last_snapshot(monkeypatch):
    monkeypatch.setattr(sp, "_documents", [{"source": "old"}])
    monkeypatch.delenv("SHAREPOINT_TENANT_ID", raising=False)
    with pytest.raises(ValueError):
        sp.sync()
    assert sp.snapshot() == [{"source": "old"}]


def test_rejects_untrusted_pagination_url():
    with pytest.raises(ValueError):
        sp._graph_url("https://example.com/v1.0/next")
