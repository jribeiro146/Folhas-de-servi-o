"""Microsoft Graph storage bridge for SharePoint/OneDrive document libraries."""

from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, parse, request

from src.config import (
    GRAPH_ACTIVE_PATH,
    GRAPH_ARCHIVE_PATH,
    GRAPH_CLIENT_ID,
    GRAPH_CLIENT_SECRET,
    GRAPH_DRIVE_ID,
    GRAPH_TENANT_ID,
)


GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
TOKEN_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
EXCEL_SUFFIXES = {".xlsx", ".xlsm"}
DRAFT_FOLDER_RE = re.compile(r"^.+_\d{4}-\d{2}-\d{2}_.+$")
ARCHIVED_SINGLE_SUFFIX = ".fs_archived"
ARCHIVED_BUNDLE_MARKER = ".fs_archived"


class GraphStorageError(Exception):
    """Raised when Microsoft Graph cannot complete a storage operation."""


@dataclass(frozen=True)
class GraphConfig:
    tenant_id: str
    client_id: str
    client_secret: str
    drive_id: str
    active_path: str = "Activas"
    archive_path: str = "Arquivadas"

    @classmethod
    def from_env(cls) -> "GraphConfig":
        return cls(
            tenant_id=GRAPH_TENANT_ID,
            client_id=GRAPH_CLIENT_ID,
            client_secret=GRAPH_CLIENT_SECRET,
            drive_id=GRAPH_DRIVE_ID,
            active_path=GRAPH_ACTIVE_PATH or "Activas",
            archive_path=GRAPH_ARCHIVE_PATH or "Arquivadas",
        )

    def missing_fields(self) -> list[str]:
        fields = {
            "GRAPH_TENANT_ID": self.tenant_id,
            "GRAPH_CLIENT_ID": self.client_id,
            "GRAPH_CLIENT_SECRET": self.client_secret,
            "GRAPH_DRIVE_ID": self.drive_id,
        }
        return [name for name, value in fields.items() if not value]


class GraphStorageService:
    """Downloads active Excel files and uploads archived bundles through Graph."""

    def __init__(self, config: GraphConfig | None = None):
        self.config = config or GraphConfig.from_env()
        self._access_token: str | None = None
        self._access_token_expires_at = 0.0

    def validate_config(self) -> None:
        missing = self.config.missing_fields()
        if missing:
            raise GraphStorageError(
                "Configuração Microsoft Graph incompleta: " + ", ".join(missing)
            )

    def status(self) -> dict[str, object]:
        return {
            "configured": not self.config.missing_fields(),
            "missing_fields": self.config.missing_fields(),
            "drive_id": bool(self.config.drive_id),
            "active_path": self.config.active_path,
            "archive_path": self.config.archive_path,
        }

    def test_connection(self) -> dict[str, object]:
        self.validate_config()
        drive = self._graph_json("GET", f"/drives/{self.config.drive_id}")
        active = self._graph_json(
            "GET",
            f"/drives/{self.config.drive_id}/root:/{self._quote_path(self.config.active_path)}",
        )
        return {
            "drive": drive.get("name"),
            "active_folder": active.get("name"),
            "active_path": self.config.active_path,
        }

    def sync_active_files(self, local_active_dir: Path) -> list[Path]:
        """Download active Excel files and draft bundles into the local cache."""
        self.validate_config()
        local_active_dir.mkdir(parents=True, exist_ok=True)
        downloaded: list[Path] = []
        remote_names: set[str] = set()
        self._repair_active_cache(local_active_dir)

        for item in self.list_active_items():
            item_name = self._safe_file_name(str(item.get("name") or ""))
            remote_names.add(item_name)

            if self._is_excel_item(item):
                downloaded.append(self._sync_active_workbook(item, local_active_dir))
                continue

            if item.get("folder"):
                bundle_path = self._sync_active_bundle(item, local_active_dir)
                if bundle_path is not None:
                    downloaded.append(bundle_path)

        self._remove_stale_active_cache(local_active_dir, remote_names)

        return downloaded

    def list_active_workbooks(self) -> list[dict[str, Any]]:
        return [
            item for item in self.list_active_items()
            if self._is_excel_item(item)
        ]

    def list_active_items(self) -> list[dict[str, Any]]:
        self.validate_config()
        response = self._graph_json(
            "GET",
            (
                f"/drives/{self.config.drive_id}/root:/"
                f"{self._quote_path(self.config.active_path)}:/children"
                "?$select=id,name,size,lastModifiedDateTime,file,folder"
            ),
        )
        return list(response.get("value") or [])

    def download_item(self, item_id: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        content = self._graph_bytes("GET", f"/drives/{self.config.drive_id}/items/{item_id}/content")
        destination.write_bytes(content)

    def upload_archive_bundle(self, archived_excel_path: Path) -> list[str]:
        """Upload the archived Excel and sidecars to Graph archive folder."""
        self.validate_config()
        if not archived_excel_path.exists():
            raise GraphStorageError(f"Ficheiro arquivado não encontrado: {archived_excel_path}")

        archive_folder_name = archived_excel_path.parent.name
        remote_folder = self._join_graph_path(self.config.archive_path, archive_folder_name)
        self.ensure_folder_path(remote_folder)

        uploaded: list[str] = []
        for local_file in self._iter_uploadable_files(archived_excel_path.parent):
            remote_path = self._join_graph_path(remote_folder, local_file.name)
            self.upload_file(local_file, remote_path)
            uploaded.append(remote_path)

        return uploaded

    def upload_active_bundle(self, draft_excel_path: Path, *, fail_if_exists: bool = False) -> list[str]:
        """Upload or update a draft bundle in the Graph active folder."""
        self.validate_config()
        if not self._is_local_bundle_file(draft_excel_path):
            raise GraphStorageError("Só rascunhos em pasta podem ser sincronizados para Activas.")

        bundle_dir = draft_excel_path.parent
        remote_folder = self._join_graph_path(self.config.active_path, bundle_dir.name)
        if fail_if_exists and self._path_exists(remote_folder):
            raise GraphStorageError(
                f"Já existe um rascunho no SharePoint com o nome '{bundle_dir.name}'. "
                "Atualize a lista e volte a guardar para criar um nome único."
            )

        self.ensure_folder_path(remote_folder)
        uploaded: list[str] = []
        for local_file in self._iter_uploadable_files(bundle_dir):
            remote_path = self._join_graph_path(remote_folder, local_file.name)
            self.upload_file(local_file, remote_path)
            uploaded.append(remote_path)

        self._write_bundle_meta(bundle_dir, {"name": bundle_dir.name, "remote_path": remote_folder})
        return uploaded

    def remove_active_entry(self, local_source_path: Path) -> bool:
        """Remove the finalized source from the Graph active folder."""
        self.validate_config()
        source_name = (
            local_source_path.parent.name
            if self._is_local_bundle_file(local_source_path)
            else local_source_path.name
        )
        remote_path = self._join_graph_path(self.config.active_path, source_name)
        if self.delete_path(remote_path):
            return True

        # Fallback by direct child name. This is useful for folders with encoded
        # characters or for sources already cleaned from the local cache.
        for item in self.list_active_items():
            if item.get("name") == source_name:
                self._delete_item_by_id(str(item["id"]))
                return True

        return False

    def delete_path(self, graph_path: str) -> bool:
        item = self._get_item_by_path(graph_path)
        if not item:
            return False

        self._delete_item_by_id(str(item["id"]))
        return True

    def _delete_item_by_id(self, item_id: str) -> None:
        self._graph_bytes("DELETE", f"/drives/{self.config.drive_id}/items/{item_id}")

    def ensure_folder_path(self, folder_path: str) -> None:
        current = ""
        for segment in [part for part in folder_path.strip("/").split("/") if part]:
            parent = current
            current = self._join_graph_path(current, segment)
            if self._path_exists(current):
                continue
            self._create_folder(parent, segment)

    def upload_file(self, local_file: Path, remote_path: str) -> None:
        content_type = mimetypes.guess_type(local_file.name)[0] or "application/octet-stream"
        self._graph_bytes(
            "PUT",
            f"/drives/{self.config.drive_id}/root:/{self._quote_path(remote_path)}:/content",
            data=local_file.read_bytes(),
            content_type=content_type,
        )

    def _sync_active_workbook(self, item: dict[str, Any], local_active_dir: Path) -> Path:
        local_path = local_active_dir / self._safe_file_name(str(item.get("name") or ""))
        meta_path = local_path.with_name(f"{local_path.name}.graph.json")
        self._clear_archived_marker(local_path)

        if self._is_cache_current(local_path, meta_path, item):
            return local_path

        self.download_item(str(item["id"]), local_path)
        self._write_item_meta(meta_path, item)
        return local_path

    def _sync_active_bundle(self, folder_item: dict[str, Any], local_active_dir: Path) -> Path | None:
        folder_name = self._safe_file_name(str(folder_item.get("name") or ""))
        children = self._list_children(str(folder_item["id"]))
        excel_items = [item for item in children if self._is_excel_item(item)]
        if not excel_items:
            return None

        local_dir = local_active_dir / folder_name
        local_dir.mkdir(parents=True, exist_ok=True)
        self._clear_archived_marker(local_dir)
        downloaded_excel: Path | None = None

        for child in children:
            if not child.get("file"):
                continue

            child_name = self._safe_file_name(str(child.get("name") or ""))
            local_path = local_dir / child_name
            meta_path = local_path.with_name(f"{local_path.name}.graph.json")
            if not self._is_cache_current(local_path, meta_path, child):
                self.download_item(str(child["id"]), local_path)
                self._write_item_meta(meta_path, child)

            if self._is_excel_item(child) and downloaded_excel is None:
                downloaded_excel = local_path

        self._write_bundle_meta(local_dir, folder_item)
        return downloaded_excel

    def _list_children(self, item_id: str) -> list[dict[str, Any]]:
        response = self._graph_json(
            "GET",
            f"/drives/{self.config.drive_id}/items/{item_id}/children"
            "?$select=id,name,size,lastModifiedDateTime,file,folder",
        )
        return list(response.get("value") or [])

    def _access_token_value(self) -> str:
        self.validate_config()
        if self._access_token and time.time() < self._access_token_expires_at:
            return self._access_token

        token_url = TOKEN_URL_TEMPLATE.format(tenant_id=parse.quote(self.config.tenant_id))
        body = parse.urlencode(
            {
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "scope": GRAPH_SCOPE,
                "grant_type": "client_credentials",
            }
        ).encode("utf-8")
        response = self._request_json(
            token_url,
            method="POST",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            authenticated=False,
        )
        self._access_token = str(response["access_token"])
        self._access_token_expires_at = time.time() + int(response.get("expires_in") or 3600) - 120
        return self._access_token

    def _graph_json(self, method: str, path: str, data: bytes | None = None, content_type: str | None = None) -> dict:
        url = f"{GRAPH_ROOT}{path}"
        headers = {"Accept": "application/json"}
        if content_type:
            headers["Content-Type"] = content_type
        return self._request_json(url, method=method, data=data, headers=headers)

    def _graph_bytes(
        self,
        method: str,
        path: str,
        data: bytes | None = None,
        content_type: str | None = None,
    ) -> bytes:
        url = f"{GRAPH_ROOT}{path}"
        headers: dict[str, str] = {}
        if content_type:
            headers["Content-Type"] = content_type
        return self._request_bytes(url, method=method, data=data, headers=headers)

    def _request_json(
        self,
        url: str,
        *,
        method: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        authenticated: bool = True,
    ) -> dict:
        content = self._request_bytes(
            url,
            method=method,
            data=data,
            headers=headers,
            authenticated=authenticated,
        )
        return json.loads(content.decode("utf-8")) if content else {}

    def _request_bytes(
        self,
        url: str,
        *,
        method: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        authenticated: bool = True,
    ) -> bytes:
        request_headers = dict(headers or {})
        if authenticated:
            request_headers["Authorization"] = f"Bearer {self._access_token_value()}"

        req = request.Request(url, data=data, headers=request_headers, method=method)
        try:
            with request.urlopen(req, timeout=60) as response:
                return response.read()
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise GraphStorageError(f"Microsoft Graph HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise GraphStorageError(f"Microsoft Graph indisponível: {exc}") from exc

    def _path_exists(self, graph_path: str) -> bool:
        return self._get_item_by_path(graph_path) is not None

    def _get_item_by_path(self, graph_path: str) -> dict[str, Any] | None:
        try:
            return self._graph_json(
                "GET",
                f"/drives/{self.config.drive_id}/root:/{self._quote_path(graph_path)}:",
            )
        except GraphStorageError as exc:
            if "HTTP 404" in str(exc):
                return None
            raise

    def _create_folder(self, parent_path: str, folder_name: str) -> None:
        body = json.dumps(
            {
                "name": folder_name,
                "folder": {},
                "@microsoft.graph.conflictBehavior": "replace",
            }
        ).encode("utf-8")
        if parent_path:
            path = f"/drives/{self.config.drive_id}/root:/{self._quote_path(parent_path)}:/children"
        else:
            path = f"/drives/{self.config.drive_id}/root/children"
        self._graph_json("POST", path, data=body, content_type="application/json")

    @staticmethod
    def _is_cache_current(local_path: Path, meta_path: Path, item: dict[str, Any]) -> bool:
        if not local_path.exists() or not meta_path.exists():
            return False
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return (
            meta.get("id") == item.get("id")
            and meta.get("lastModifiedDateTime") == item.get("lastModifiedDateTime")
            and int(meta.get("size") or 0) == int(item.get("size") or 0)
        )

    @staticmethod
    def _write_item_meta(meta_path: Path, item: dict[str, Any]) -> None:
        meta_path.write_text(
            json.dumps(
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "size": int(item.get("size") or 0),
                    "lastModifiedDateTime": str(item.get("lastModifiedDateTime") or ""),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _write_bundle_meta(bundle_dir: Path, item: dict[str, Any]) -> None:
        (bundle_dir / ".graph_bundle.json").write_text(
            json.dumps(
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "remote_path": item.get("remote_path"),
                    "lastModifiedDateTime": str(item.get("lastModifiedDateTime") or ""),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _remove_stale_active_cache(self, local_active_dir: Path, remote_names: set[str]) -> None:
        for item in list(local_active_dir.iterdir()):
            if self._is_graph_metadata_file(item):
                self._remove_orphan_metadata(item, local_active_dir, remote_names)
                continue

            if self._is_archived_marker(item):
                self._remove_orphan_archived_marker(item, local_active_dir, remote_names)
                continue

            if item.name in remote_names:
                continue

            if item.is_file() and item.with_name(f"{item.name}.graph.json").exists():
                self._remove_local_path(item)
                self._remove_local_path(item.with_name(f"{item.name}.graph.json"))
                continue

            if item.is_dir() and (
                (item / ".graph_bundle.json").exists()
                or DRAFT_FOLDER_RE.match(item.name)
            ):
                self._remove_local_path(item)

    def _repair_active_cache(self, local_active_dir: Path) -> None:
        for item in list(local_active_dir.iterdir()):
            if self._is_graph_metadata_file(item):
                owner = self._metadata_owner_path(item)
                if owner is None or not owner.exists():
                    self._remove_local_path(item)
                continue

            if self._is_archived_marker(item):
                owner = self._archived_marker_owner_path(item)
                if owner is None or not owner.exists():
                    self._remove_local_path(item)
                continue

            if item.is_dir() and DRAFT_FOLDER_RE.match(item.name):
                self._repair_bundle_cache(item)

    def _repair_bundle_cache(self, bundle_dir: Path) -> None:
        excel_files = [
            path for path in bundle_dir.iterdir()
            if path.is_file() and path.suffix.lower() in EXCEL_SUFFIXES and not path.name.startswith("~$")
        ]
        exact_excel = bundle_dir / f"{bundle_dir.name}.xlsx"
        if exact_excel.exists():
            return
        if excel_files:
            return

        self._remove_local_path(bundle_dir)

    def _remove_orphan_metadata(self, item: Path, local_active_dir: Path, remote_names: set[str]) -> None:
        owner = self._metadata_owner_path(item)
        if owner is None or not owner.exists():
            self._remove_local_path(item)
            return

        if owner.parent == local_active_dir and owner.name not in remote_names:
            self._remove_local_path(item)

    def _remove_orphan_archived_marker(self, item: Path, local_active_dir: Path, remote_names: set[str]) -> None:
        owner = self._archived_marker_owner_path(item)
        if owner is None or not owner.exists():
            self._remove_local_path(item)
            return

        if owner.parent == local_active_dir and owner.name in remote_names:
            self._remove_local_path(item)

    @staticmethod
    def _remove_local_path(path: Path) -> None:
        try:
            if path.is_dir():
                shutil.rmtree(path, onerror=GraphStorageService._handle_remove_error)
            else:
                GraphStorageService._unlink_local_file(path)
        except FileNotFoundError:
            return
        except OSError:
            return

    @staticmethod
    def _unlink_local_file(path: Path) -> None:
        try:
            path.unlink()
        except PermissionError:
            GraphStorageService._make_path_writable(path)
            path.unlink()

    @staticmethod
    def _handle_remove_error(function: Any, path: str, exc_info: object) -> None:
        GraphStorageService._make_path_writable(Path(path))
        function(path)

    @staticmethod
    def _make_path_writable(path: Path) -> None:
        try:
            os.chmod(path, stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)
        except OSError:
            return

    @staticmethod
    def _iter_uploadable_files(directory: Path) -> list[Path]:
        return sorted(
            path for path in directory.iterdir()
            if path.is_file() and not GraphStorageService._is_graph_metadata_file(path)
        )

    @staticmethod
    def _is_graph_metadata_file(path: Path) -> bool:
        return path.name.endswith(".graph.json") or path.name == ".graph_bundle.json"

    @staticmethod
    def _is_archived_marker(path: Path) -> bool:
        return path.name == ARCHIVED_BUNDLE_MARKER or path.name.endswith(ARCHIVED_SINGLE_SUFFIX)

    @staticmethod
    def _metadata_owner_path(path: Path) -> Path | None:
        if path.name == ".graph_bundle.json":
            return path.parent

        if path.name.endswith(".graph.json"):
            return path.with_name(path.name[: -len(".graph.json")])

        return None

    @staticmethod
    def _archived_marker_owner_path(path: Path) -> Path | None:
        if path.name == ARCHIVED_BUNDLE_MARKER:
            return path.parent

        if path.name.endswith(ARCHIVED_SINGLE_SUFFIX):
            return path.with_name(path.name[: -len(ARCHIVED_SINGLE_SUFFIX)])

        return None

    @staticmethod
    def _is_local_bundle_file(file_path: Path) -> bool:
        return file_path.parent.name == file_path.stem

    @staticmethod
    def _clear_archived_marker(path: Path) -> None:
        marker = (
            path / ARCHIVED_BUNDLE_MARKER
            if path.is_dir()
            else path.with_name(f"{path.name}{ARCHIVED_SINGLE_SUFFIX}")
        )
        try:
            GraphStorageService._unlink_local_file(marker)
        except FileNotFoundError:
            return
        except OSError:
            return

    @staticmethod
    def _is_excel_item(item: dict[str, Any]) -> bool:
        name = str(item.get("name") or "")
        return (
            bool(item.get("file"))
            and not name.startswith("~$")
            and Path(name).suffix.lower() in EXCEL_SUFFIXES
        )

    @staticmethod
    def _safe_file_name(value: str) -> str:
        safe = "".join(char for char in value if char not in '<>:"/\\|?*').strip()
        if not safe:
            raise GraphStorageError("Nome de ficheiro Graph inválido.")
        return safe

    @staticmethod
    def _join_graph_path(*parts: str) -> str:
        return "/".join(part.strip("/") for part in parts if part and part.strip("/"))

    @staticmethod
    def _quote_path(path: str) -> str:
        return parse.quote(path.strip("/"), safe="/")
