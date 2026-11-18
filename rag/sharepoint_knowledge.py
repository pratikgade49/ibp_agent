"""Read-only Microsoft Graph sync for a shared IBP knowledge folder."""
from __future__ import annotations

import io
import logging
import os
import threading
import zipfile
from urllib.parse import quote, urlparse
from xml.etree import ElementTree

import requests

LOG = logging.getLogger(__name__)
GRAPH = "https://graph.microsoft.com/v1.0"
MAX_DOCUMENT_BYTES = 2_000_000
MAX_TEXT_CHARS = 200_000
MAX_FILES = 500
_lock = threading.Lock()
_documents: list[dict] = []


def snapshot() -> list[dict]:
    with _lock:
        return list(_documents)


def _graph_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "graph.microsoft.com" or not parsed.path.startswith("/v1.0/"):
        raise ValueError("Unexpected Graph pagination URL")
    return url


def _text(name: str, data: bytes) -> str:
    if name.lower().endswith((".md", ".txt")):
        return data.decode("utf-8-sig")[:MAX_TEXT_CHARS]
    if name.lower().endswith(".docx"):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > MAX_DOCUMENT_BYTES:
                raise ValueError("Word document XML exceeds size limit")
            root = ElementTree.fromstring(archive.read(info))
            ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            paragraphs = ["".join(node.text or "" for node in p.iter(ns + "t")) for p in root.iter(ns + "p")]
            return "\n\n".join(p for p in paragraphs if p)[:MAX_TEXT_CHARS]
    raise ValueError("Unsupported file type")


def sync() -> dict:
    """Replace the in-memory snapshot only after a complete successful scan."""
    tenant, client, secret, drive, folder = (os.environ.get(k, "").strip() for k in (
        "SHAREPOINT_TENANT_ID", "SHAREPOINT_CLIENT_ID", "SHAREPOINT_CLIENT_SECRET",
        "SHAREPOINT_DRIVE_ID", "SHAREPOINT_FOLDER_ID"))
    if not all((tenant, client, secret, drive, folder)):
        raise ValueError("SharePoint tenant, client, secret, drive and folder settings are required")
    if not all(c.isalnum() or c in "-." for c in tenant):
        raise ValueError("Invalid tenant ID")
    token_response = requests.post(
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        data={"client_id": client, "client_secret": secret, "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials"}, timeout=30)
    token_response.raise_for_status()
    headers = {"Authorization": "Bearer " + token_response.json()["access_token"]}
    base = f"{GRAPH}/drives/{quote(drive, safe='')}/items/"
    queue = [folder]
    found = []
    visited = set()
    scanned = 0
    while queue:
        parent = queue.pop()
        if parent in visited:
            continue
        visited.add(parent)
        url = base + quote(parent, safe="") + "/children?$select=id,name,size,folder,file,webUrl,lastModifiedDateTime"
        while url:
            response = requests.get(_graph_url(url), headers=headers, timeout=30)
            response.raise_for_status()
            page = response.json()
            for item in page.get("value", []):
                scanned += 1
                if scanned > MAX_FILES:
                    raise ValueError("SharePoint folder exceeds the pilot's 500 item limit")
                if "folder" in item:
                    queue.append(item["id"])
                elif "file" in item and item.get("name", "").lower().endswith((".md", ".txt", ".docx")):
                    if item.get("size", 0) > MAX_DOCUMENT_BYTES:
                        raise ValueError("A supported SharePoint document exceeds the 2 MB limit")
                    result = requests.get(base + quote(item["id"], safe="") + "/content", headers=headers, timeout=60, stream=True)
                    result.raise_for_status()
                    data = bytearray()
                    for part in result.iter_content(65536):
                        data.extend(part)
                        if len(data) > MAX_DOCUMENT_BYTES:
                            raise ValueError("A supported SharePoint document exceeds the 2 MB limit")
                    result.close()
                    found.append({"source": item["name"], "url": item.get("webUrl", ""), "modified": item.get("lastModifiedDateTime", ""), "text": _text(item["name"], bytes(data))})
            url = page.get("@odata.nextLink")
    with _lock:
        _documents[:] = found
    return {"documents": len(found), "items_scanned": scanned}


def start_sync_worker() -> None:
    if os.environ.get("SHAREPOINT_SYNC_ENABLED", "").lower() != "true":
        return
    def worker():
        while True:
            try:
                result = sync()
                LOG.info("SharePoint knowledge sync completed: %s", result)
            except Exception as exc:
                LOG.warning("SharePoint knowledge sync failed (%s); previous snapshot retained", type(exc).__name__)
            try:
                interval = max(60, int(os.environ.get("SHAREPOINT_SYNC_INTERVAL_SECONDS", "3600")))
            except ValueError:
                interval = 3600
            threading.Event().wait(interval)
    threading.Thread(target=worker, name="sharepoint-knowledge-sync", daemon=True).start()
