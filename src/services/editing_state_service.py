"""Persistent leases, revisions, autosaves and idempotency for document editing."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from src.config import APP_DATA_DIR


EDIT_METADATA_NAME = ".fs_edit.json"
STATE_VERSION = 1


class EditingStateError(Exception):
    """Base error for editing-state operations."""


class LeaseConflictError(EditingStateError):
    """Raised when another editor owns the active lease."""

    def __init__(self, snapshot: dict[str, Any]):
        super().__init__("A folha está a ser editada por outro utilizador.")
        self.snapshot = snapshot


class LeaseRequiredError(EditingStateError):
    """Raised when a write does not carry the active lease."""

    def __init__(self, snapshot: dict[str, Any]):
        super().__init__("A reserva de edição expirou ou já não é válida.")
        self.snapshot = snapshot


class RevisionConflictError(EditingStateError):
    """Raised when a client tries to write an obsolete revision."""

    def __init__(self, snapshot: dict[str, Any]):
        super().__init__("A folha foi alterada desde a última leitura.")
        self.snapshot = snapshot


class OperationInProgressError(EditingStateError):
    """Raised when the same idempotent operation is already running."""


@dataclass(frozen=True)
class EditorIdentity:
    id: str
    display_name: str


class EditingStateService:
    """Coordinates concurrent editors through small atomic JSON state files."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        lease_seconds: int | None = None,
        operation_timeout_seconds: int = 120,
    ):
        self.root = Path(root or (APP_DATA_DIR / "editing-state"))
        self.root.mkdir(parents=True, exist_ok=True)
        configured_lease = int(os.environ.get("FS_EDIT_LEASE_SECONDS", "10"))
        self.lease_seconds = max(int(lease_seconds or configured_lease), 1)
        self.operation_timeout_seconds = max(int(operation_timeout_seconds), 1)

    def resolve_document_id(self, path: str | Path) -> str:
        file_path = Path(path)
        metadata = self._read_edit_metadata(file_path)
        if metadata.get("document_id"):
            return str(metadata["document_id"])

        graph_id = self._read_graph_item_id(file_path)
        if graph_id:
            identity = f"graph:{graph_id}"
        else:
            logical_name = self._original_logical_name(file_path)
            identity = f"local:{file_path.parent.parent.resolve()}:{logical_name.casefold()}"
        return str(uuid.uuid5(uuid.NAMESPACE_URL, identity))

    def resolve_private_workspace_id(
        self,
        path: str | Path,
        identity: EditorIdentity,
        client_id: str,
    ) -> str:
        """Return the isolated editing id for one editor session on a source file."""
        normalized_client_id = str(client_id or "").strip()
        if not normalized_client_id:
            raise EditingStateError("Identificador da sessão de edição em falta.")
        source_document_id = self.resolve_document_id(path)
        workspace_identity = (
            f"private-workspace:{source_document_id}:{identity.id}:{normalized_client_id}"
        )
        return str(uuid.uuid5(uuid.NAMESPACE_URL, workspace_identity))

    def associate_path(
        self,
        path: str | Path,
        document_id: str,
        *,
        original_name: str,
    ) -> None:
        file_path = Path(path)
        if file_path.parent.name == file_path.stem:
            self._atomic_write_json(
                file_path.parent / EDIT_METADATA_NAME,
                {
                    "version": STATE_VERSION,
                    "document_id": document_id,
                    "original_name": original_name,
                },
            )

        with self._state_transaction(document_id) as state:
            state.setdefault("aliases", [])
            if file_path.stem not in state["aliases"]:
                state["aliases"].append(file_path.stem)
            state["original_name"] = state.get("original_name") or original_name
            state["fingerprint"] = self._fingerprint(file_path)

    def snapshot(self, path: str | Path, document_id: str | None = None) -> dict[str, Any]:
        file_path = Path(path)
        resolved_id = document_id or self.resolve_document_id(file_path)
        with self._state_transaction(resolved_id, path=file_path) as state:
            return self._public_snapshot(state)

    def snapshot_document(self, document_id: str) -> dict[str, Any]:
        with self._state_transaction(document_id) as state:
            return self._public_snapshot(state)

    def acquire_lease(
        self,
        path: str | Path,
        identity: EditorIdentity,
        client_id: str,
    ) -> dict[str, Any]:
        file_path = Path(path)
        document_id = self.resolve_document_id(file_path)
        return self._acquire_document_lease(file_path, document_id, identity, client_id)

    def acquire_private_workspace(
        self,
        path: str | Path,
        identity: EditorIdentity,
        client_id: str,
    ) -> dict[str, Any]:
        """Acquire an isolated workspace without reserving the shared source file."""
        file_path = Path(path)
        workspace_id = self.resolve_private_workspace_id(file_path, identity, client_id)
        return self._acquire_document_lease(
            file_path,
            workspace_id,
            identity,
            client_id,
        )

    def _acquire_document_lease(
        self,
        file_path: Path,
        document_id: str,
        identity: EditorIdentity,
        client_id: str,
    ) -> dict[str, Any]:
        if not client_id.strip():
            raise EditingStateError("Identificador da sessão de edição em falta.")

        now = time.time()
        with self._state_transaction(document_id, path=file_path) as state:
            lease = self._active_lease(state, now)
            if lease and not (
                lease.get("owner_id") == identity.id
                and lease.get("client_id") == client_id
            ):
                raise LeaseConflictError(self._public_snapshot(state, now=now))

            if lease:
                token = str(lease["token"])
            else:
                token = secrets.token_urlsafe(32)
            state["lease"] = {
                "token": token,
                "owner_id": identity.id,
                "owner_name": identity.display_name,
                "client_id": client_id,
                "acquired_at": lease.get("acquired_at", now) if lease else now,
                "expires_at": now + self.lease_seconds,
            }
            return self._public_snapshot(state, now=now, include_token=True)

    def renew_lease(
        self,
        document_id: str,
        identity: EditorIdentity,
        client_id: str,
        lease_token: str,
    ) -> dict[str, Any]:
        now = time.time()
        with self._state_transaction(document_id) as state:
            self._assert_lease(state, identity, client_id, lease_token, now)
            state["lease"]["expires_at"] = now + self.lease_seconds
            return self._public_snapshot(state, now=now, include_token=True)

    def release_lease(
        self,
        document_id: str,
        identity: EditorIdentity,
        client_id: str,
        lease_token: str,
    ) -> bool:
        with self._state_transaction(document_id) as state:
            lease = self._active_lease(state)
            if not lease:
                state["lease"] = None
                return False
            if not self._lease_matches(lease, identity, client_id, lease_token):
                return False
            state["lease"] = None
            return True

    def release_user_leases(self, owner_id: str) -> int:
        """Release every active lease owned by a user during an explicit logout."""
        released = 0
        for state_path in self.root.glob("*.json"):
            try:
                payload = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            document_id = str(payload.get("document_id") or "").strip()
            if not document_id:
                continue
            with self._state_transaction(document_id) as state:
                lease = self._active_lease(state)
                if lease and lease.get("owner_id") == owner_id:
                    state["lease"] = None
                    released += 1
        return released

    def save_autosave(
        self,
        *,
        document_id: str,
        identity: EditorIdentity,
        client_id: str,
        lease_token: str,
        base_revision: int,
        idempotency_key: str,
        document: dict[str, Any],
    ) -> dict[str, Any]:
        now = time.time()
        with self._state_transaction(document_id) as state:
            self._assert_lease(state, identity, client_id, lease_token, now)
            existing = state.get("autosave") or {}
            if existing.get("idempotency_key") == idempotency_key:
                return self._public_snapshot(state, now=now, include_token=True)
            self._assert_revision(state, base_revision, now)
            state["revision"] = int(state.get("revision") or 1) + 1
            state["autosave"] = {
                "document": document,
                "updated_at": now,
                "owner_id": identity.id,
                "owner_name": identity.display_name,
                "idempotency_key": idempotency_key,
            }
            state["etag"] = self._etag(state)
            return self._public_snapshot(state, now=now, include_token=True)

    def close_editing_session(
        self,
        *,
        document_id: str,
        identity: EditorIdentity,
        client_id: str,
        lease_token: str,
        base_revision: int,
        idempotency_key: str,
        document: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Best-effort autosave followed by an immediate lease release.

        This operation is used when a browser tab is being closed or reloaded.
        A stale document is never written, but the lease is still released so
        another technician does not have to wait for the expiry timeout.
        """
        now = time.time()
        with self._state_transaction(document_id) as state:
            self._assert_lease(state, identity, client_id, lease_token, now)
            autosaved = False
            revision_conflict = False

            if document is not None:
                existing = state.get("autosave") or {}
                if existing.get("idempotency_key") == idempotency_key:
                    autosaved = True
                elif int(state.get("revision") or 1) != int(base_revision):
                    revision_conflict = True
                else:
                    state["revision"] = int(state.get("revision") or 1) + 1
                    state["autosave"] = {
                        "document": document,
                        "updated_at": now,
                        "owner_id": identity.id,
                        "owner_name": identity.display_name,
                        "idempotency_key": idempotency_key,
                    }
                    state["etag"] = self._etag(state)
                    autosaved = True

            state["lease"] = None
            return {
                "autosaved": autosaved,
                "revision_conflict": revision_conflict,
                "released": True,
                "editing": self._public_snapshot(state, now=now),
            }

    def discard_autosave(
        self,
        *,
        document_id: str,
        identity: EditorIdentity,
        client_id: str,
        lease_token: str,
        base_revision: int,
    ) -> dict[str, Any]:
        now = time.time()
        with self._state_transaction(document_id) as state:
            self._assert_lease(state, identity, client_id, lease_token, now)
            self._assert_revision(state, base_revision, now)
            state["autosave"] = None
            state["revision"] = int(state.get("revision") or 1) + 1
            state["etag"] = self._etag(state)
            return self._public_snapshot(state, now=now, include_token=True)

    def claim_operation(
        self,
        *,
        document_id: str,
        kind: str,
        idempotency_key: str,
        identity: EditorIdentity,
        client_id: str,
        lease_token: str,
        base_revision: int,
    ) -> dict[str, Any]:
        now = time.time()
        with self._state_transaction(document_id) as state:
            operations = state.setdefault("operations", {})
            existing = operations.get(idempotency_key)
            if existing:
                if existing.get("kind") != kind:
                    raise EditingStateError("A chave de idempotência já foi usada noutra operação.")
                if existing.get("status") == "complete":
                    return {"status": "replay", "operation": dict(existing)}
                if (
                    existing.get("status") == "pending"
                    and now - float(existing.get("updated_at") or now) < self.operation_timeout_seconds
                ):
                    raise OperationInProgressError("A operação já está em curso.")

            self._assert_lease(state, identity, client_id, lease_token, now)
            self._assert_revision(state, base_revision, now)
            operation = dict(existing or {})
            operation.update({
                "kind": kind,
                "status": "pending",
                "owner_id": identity.id,
                "created_at": operation.get("created_at") or now,
                "updated_at": now,
                "base_revision": base_revision,
                "context": dict(operation.get("context") or {}),
            })
            operations[idempotency_key] = operation
            return {"status": "started", "operation": dict(operation)}

    def operation(self, document_id: str, idempotency_key: str) -> dict[str, Any] | None:
        with self._state_transaction(document_id) as state:
            operation = (state.get("operations") or {}).get(idempotency_key)
            return dict(operation) if operation else None

    def update_operation_context(
        self,
        document_id: str,
        idempotency_key: str,
        **context: Any,
    ) -> None:
        with self._state_transaction(document_id) as state:
            operation = (state.setdefault("operations", {})).get(idempotency_key)
            if not operation:
                raise EditingStateError("Operação idempotente não encontrada.")
            operation.setdefault("context", {}).update(context)
            operation["updated_at"] = time.time()

    def fail_operation(self, document_id: str, idempotency_key: str, message: str) -> None:
        with self._state_transaction(document_id) as state:
            operation = (state.setdefault("operations", {})).get(idempotency_key)
            if not operation:
                return
            operation["status"] = "failed"
            operation["error"] = message
            operation["updated_at"] = time.time()

    def commit_operation(
        self,
        *,
        document_id: str,
        idempotency_key: str,
        path: str | Path | None,
        response: dict[str, Any],
        release_lease: bool = False,
        result_editing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._state_transaction(document_id) as state:
            state["revision"] = int(state.get("revision") or 1) + 1
            state["autosave"] = None
            if path is not None:
                state["fingerprint"] = self._fingerprint(Path(path))
                alias = Path(path).stem
                state.setdefault("aliases", [])
                if alias not in state["aliases"]:
                    state["aliases"].append(alias)
            state["etag"] = self._etag(state)
            if release_lease:
                state["lease"] = None

            result = dict(response)
            if result_editing is not None:
                result.update({
                    "document_id": result_editing["document_id"],
                    "revision": int(result_editing.get("revision") or 1),
                    "etag": result_editing.get("etag") or "",
                })
            else:
                result.update({
                    "document_id": document_id,
                    "revision": state["revision"],
                    "etag": state["etag"],
                })
            operation = (state.setdefault("operations", {})).get(idempotency_key)
            if operation is None:
                raise EditingStateError("Operação idempotente não encontrada.")
            operation.update({
                "status": "complete",
                "response": result,
                "updated_at": time.time(),
            })
            self._prune_operations(state)
            return result

    @contextmanager
    def _state_transaction(
        self,
        document_id: str,
        *,
        path: Path | None = None,
    ) -> Iterator[dict[str, Any]]:
        state_path = self._state_path(document_id)
        with self._file_lock(state_path):
            state = self._read_state(state_path, document_id)
            if path is not None:
                self._sync_fingerprint(state, path)
            yield state
            self._atomic_write_json(state_path, state)

    def _read_state(self, path: Path, document_id: str) -> dict[str, Any]:
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    return payload
            except (OSError, json.JSONDecodeError):
                pass
        return {
            "version": STATE_VERSION,
            "document_id": document_id,
            "revision": 1,
            "etag": "",
            "fingerprint": "",
            "aliases": [],
            "lease": None,
            "autosave": None,
            "operations": {},
        }

    def _sync_fingerprint(self, state: dict[str, Any], path: Path) -> None:
        fingerprint = self._fingerprint(path)
        previous = str(state.get("fingerprint") or "")
        if previous and fingerprint and previous != fingerprint:
            state["revision"] = int(state.get("revision") or 1) + 1
            state["autosave"] = None
            state["etag"] = self._etag(state)
        state["fingerprint"] = fingerprint
        state.setdefault("aliases", [])
        if path.stem not in state["aliases"]:
            state["aliases"].append(path.stem)
        if not state.get("etag"):
            state["etag"] = self._etag(state)

    def _assert_lease(
        self,
        state: dict[str, Any],
        identity: EditorIdentity,
        client_id: str,
        lease_token: str,
        now: float,
    ) -> None:
        lease = self._active_lease(state, now)
        if not lease or not self._lease_matches(lease, identity, client_id, lease_token):
            raise LeaseRequiredError(self._public_snapshot(state, now=now))

    def _assert_revision(self, state: dict[str, Any], base_revision: int, now: float) -> None:
        if int(base_revision) != int(state.get("revision") or 1):
            raise RevisionConflictError(self._public_snapshot(state, now=now))

    @staticmethod
    def _lease_matches(
        lease: dict[str, Any],
        identity: EditorIdentity,
        client_id: str,
        lease_token: str,
    ) -> bool:
        return (
            lease.get("owner_id") == identity.id
            and lease.get("client_id") == client_id
            and secrets.compare_digest(str(lease.get("token") or ""), str(lease_token or ""))
        )

    @staticmethod
    def _active_lease(state: dict[str, Any], now: float | None = None) -> dict[str, Any] | None:
        lease = state.get("lease")
        if not isinstance(lease, dict):
            return None
        if float(lease.get("expires_at") or 0) <= (now or time.time()):
            state["lease"] = None
            return None
        return lease

    def _public_snapshot(
        self,
        state: dict[str, Any],
        *,
        now: float | None = None,
        include_token: bool = False,
    ) -> dict[str, Any]:
        lease = self._active_lease(state, now)
        public_lease = None
        if lease:
            public_lease = {
                "owner_id": lease.get("owner_id"),
                "owner_name": lease.get("owner_name"),
                "client_id": lease.get("client_id"),
                "acquired_at": lease.get("acquired_at"),
                "expires_at": lease.get("expires_at"),
            }
            if include_token:
                public_lease["token"] = lease.get("token")
        autosave = state.get("autosave") or {}
        return {
            "document_id": state["document_id"],
            "revision": int(state.get("revision") or 1),
            "etag": state.get("etag") or self._etag(state),
            "lease": public_lease,
            "server_document": autosave.get("document"),
            "autosaved_at": autosave.get("updated_at"),
        }

    def _state_path(self, document_id: str) -> Path:
        safe = hashlib.sha256(document_id.encode("utf-8")).hexdigest()
        return self.root / f"{safe}.json"

    @contextmanager
    def _file_lock(self, state_path: Path) -> Iterator[None]:
        lock_path = state_path.with_suffix(state_path.suffix + ".lock")
        deadline = time.monotonic() + 10
        descriptor: int | None = None
        while descriptor is None:
            try:
                descriptor = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(descriptor, f"{os.getpid()} {time.time()}".encode("ascii"))
            except FileExistsError:
                try:
                    if time.time() - lock_path.stat().st_mtime > 30:
                        lock_path.unlink()
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() >= deadline:
                    raise EditingStateError("Não foi possível bloquear o estado de edição.")
                time.sleep(0.025)
        try:
            yield
        finally:
            try:
                os.close(descriptor)
            finally:
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass

    @staticmethod
    def _fingerprint(path: Path) -> str:
        parts: list[str] = []
        related = [
            path,
            path.with_name(f"{path.stem}__documento.json"),
            path.with_name(f"{path.stem}__assinatura_cliente.png"),
            path.with_name(f"{path.name}.graph.json"),
        ]
        for item in related:
            try:
                stat = item.stat()
                parts.append(f"{item.name}:{stat.st_mtime_ns}:{stat.st_size}")
            except FileNotFoundError:
                parts.append(f"{item.name}:missing")
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()

    @staticmethod
    def _etag(state: dict[str, Any]) -> str:
        revision = int(state.get("revision") or 1)
        digest = hashlib.sha256(
            f"{state.get('document_id')}:{revision}:{state.get('fingerprint', '')}".encode("utf-8")
        ).hexdigest()[:16]
        return f'W/"{revision}-{digest}"'

    @staticmethod
    def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
        try:
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            for attempt in range(4):
                try:
                    os.replace(str(temp_path), str(path))
                    break
                except PermissionError:
                    if attempt == 3:
                        raise
                    # Windows can briefly retain a handle after antivirus or
                    # sync-provider inspection. Keep the atomic replace and retry.
                    time.sleep(0.02 * (attempt + 1))
        finally:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _read_edit_metadata(path: Path) -> dict[str, Any]:
        metadata_path = path.parent / EDIT_METADATA_NAME
        if path.parent.name != path.stem or not metadata_path.exists():
            return {}
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _read_graph_item_id(path: Path) -> str:
        meta_path = path.with_name(f"{path.name}.graph.json")
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
            return str(payload.get("id") or "")
        except (OSError, json.JSONDecodeError):
            return ""

    @staticmethod
    def _original_logical_name(path: Path) -> str:
        metadata = EditingStateService._read_edit_metadata(path)
        if metadata.get("original_name"):
            return str(metadata["original_name"])
        return path.stem

    @staticmethod
    def _prune_operations(state: dict[str, Any]) -> None:
        operations = state.get("operations") or {}
        if len(operations) <= 64:
            return
        ordered = sorted(
            operations.items(),
            key=lambda item: float(item[1].get("updated_at") or 0),
            reverse=True,
        )
        state["operations"] = dict(ordered[:64])
