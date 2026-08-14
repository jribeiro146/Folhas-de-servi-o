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
from src.services.editing_state_repository import SqliteStateRepository


EDIT_METADATA_NAME = ".fs_edit.json"
STATE_VERSION = 2


class EditingStateError(Exception):
    """Base error for editing-state operations."""


class EditingSessionMetadataError(EditingStateError):
    """Raised when a browser write is missing its editing-session contract."""

    def __init__(self, missing: list[str]):
        self.missing = tuple(sorted(set(missing)))
        super().__init__(
            "A sessão de edição não está pronta. Aguarde alguns segundos e tente novamente."
        )


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
    """Coordinates private workspaces, revisions and idempotent operations."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        lease_seconds: int | None = None,
        operation_timeout_seconds: int = 120,
    ):
        self.root = Path(root or (APP_DATA_DIR / "editing-state"))
        self.root.mkdir(parents=True, exist_ok=True)
        configured_session = int(os.environ.get("FS_EDIT_SESSION_SECONDS", "2592000"))
        self.session_seconds = max(int(lease_seconds or configured_session), 60)
        # Public compatibility alias retained for integrations that inspect it.
        self.lease_seconds = self.session_seconds
        self.operation_timeout_seconds = max(int(operation_timeout_seconds), 1)
        self.repository = SqliteStateRepository(
            self.root / "editing-state.sqlite3",
            legacy_root=self.root,
        )

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
        owner: EditorIdentity | None = None,
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
            state["workspace_kind"] = "draft"
            if owner is not None and not state.get("owner"):
                state["owner"] = self._owner_payload(owner)

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
        return self._acquire_document_session(
            file_path,
            document_id,
            identity,
            client_id,
            workspace_kind="draft",
        )

    def acquire_private_workspace(
        self,
        path: str | Path,
        identity: EditorIdentity,
        client_id: str,
    ) -> dict[str, Any]:
        """Acquire an isolated workspace without reserving the shared source file."""
        file_path = Path(path)
        workspace_id = self.resolve_private_workspace_id(file_path, identity, client_id)
        return self._acquire_document_session(
            file_path,
            workspace_id,
            identity,
            client_id,
            workspace_kind="private",
        )

    def _acquire_document_session(
        self,
        file_path: Path,
        document_id: str,
        identity: EditorIdentity,
        client_id: str,
        *,
        workspace_kind: str,
    ) -> dict[str, Any]:
        if not client_id.strip():
            raise EditingStateError("Identificador da sessão de edição em falta.")

        now = time.time()
        with self._state_transaction(document_id, path=file_path) as state:
            state["workspace_kind"] = workspace_kind
            self._normalize_sessions(state, now)
            owner = state.get("owner")
            if owner and owner.get("owner_id") != identity.id:
                raise LeaseConflictError(self._public_snapshot(state, now=now))
            if not owner:
                state["owner"] = self._owner_payload(identity, now=now)

            sessions = state.setdefault("sessions", {})
            token = next(
                (
                    value
                    for value, session in sessions.items()
                    if session.get("owner_id") == identity.id
                    and session.get("client_id") == client_id
                ),
                "",
            ) or secrets.token_urlsafe(32)
            previous = sessions.get(token) or {}
            sessions[token] = {
                "token": token,
                "owner_id": identity.id,
                "owner_name": identity.display_name,
                "client_id": client_id,
                "acquired_at": previous.get("acquired_at", now),
                "last_seen_at": now,
                "expires_at": now + self.session_seconds,
            }
            state["lease"] = None
            return self._public_snapshot(
                state,
                now=now,
                include_token=True,
                session_token=token,
            )

    def renew_lease(
        self,
        document_id: str,
        identity: EditorIdentity,
        client_id: str,
        lease_token: str,
    ) -> dict[str, Any]:
        now = time.time()
        with self._state_transaction(document_id) as state:
            session = self._assert_session(state, identity, client_id, lease_token, now)
            session["last_seen_at"] = now
            session["expires_at"] = now + self.session_seconds
            return self._public_snapshot(
                state, now=now, include_token=True, session_token=lease_token
            )

    def release_lease(
        self,
        document_id: str,
        identity: EditorIdentity,
        client_id: str,
        lease_token: str,
    ) -> bool:
        with self._state_transaction(document_id) as state:
            self._normalize_sessions(state)
            session = (state.get("sessions") or {}).get(lease_token)
            if not session:
                return False
            if not self._session_matches(session, identity, client_id, lease_token):
                return False
            state["sessions"].pop(lease_token, None)
            return True

    def release_user_leases(self, owner_id: str) -> int:
        """Release browser sessions without changing persistent draft ownership."""
        released = 0
        for document_id in self.repository.document_ids():
            with self._state_transaction(document_id) as state:
                self._normalize_sessions(state)
                matching = [
                    token
                    for token, session in state.get("sessions", {}).items()
                    if session.get("owner_id") == owner_id
                ]
                for token in matching:
                    state["sessions"].pop(token, None)
                released += len(matching)
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
            self._assert_session(state, identity, client_id, lease_token, now)
            existing = state.get("autosave") or {}
            if existing.get("idempotency_key") == idempotency_key:
                return self._public_snapshot(
                    state, now=now, include_token=True, session_token=lease_token
                )
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
            return self._public_snapshot(
                state, now=now, include_token=True, session_token=lease_token
            )

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
        """Best-effort autosave followed by browser-session cleanup."""
        now = time.time()
        with self._state_transaction(document_id) as state:
            self._assert_session(state, identity, client_id, lease_token, now)
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

            state.setdefault("sessions", {}).pop(lease_token, None)
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
            self._assert_session(state, identity, client_id, lease_token, now)
            self._assert_revision(state, base_revision, now)
            state["autosave"] = None
            state["revision"] = int(state.get("revision") or 1) + 1
            state["etag"] = self._etag(state)
            return self._public_snapshot(
                state, now=now, include_token=True, session_token=lease_token
            )

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

            self._assert_session(state, identity, client_id, lease_token, now)
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
                state["sessions"] = {}

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
        legacy_path = self._state_path(document_id)
        with self.repository.transaction(
            document_id,
            lambda: self._read_state(legacy_path, document_id),
        ) as state:
            self._normalize_state(state)
            if path is not None:
                self._sync_fingerprint(state, path)
            yield state

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
            "source_changed_at": None,
            "aliases": [],
            "workspace_kind": "draft",
            "owner": None,
            "sessions": {},
            "lease": None,
            "autosave": None,
            "operations": {},
        }

    def _sync_fingerprint(self, state: dict[str, Any], path: Path) -> None:
        fingerprint = self._fingerprint(path)
        previous = str(state.get("fingerprint") or "")
        if previous and fingerprint and previous != fingerprint:
            if state.get("workspace_kind") == "private":
                state["source_changed_at"] = time.time()
            else:
                state["revision"] = int(state.get("revision") or 1) + 1
                state["autosave"] = None
                state["etag"] = self._etag(state)
        state["fingerprint"] = fingerprint
        state.setdefault("aliases", [])
        if path.stem not in state["aliases"]:
            state["aliases"].append(path.stem)
        if not state.get("etag"):
            state["etag"] = self._etag(state)

    def _assert_session(
        self,
        state: dict[str, Any],
        identity: EditorIdentity,
        client_id: str,
        lease_token: str,
        now: float,
    ) -> dict[str, Any]:
        self._normalize_sessions(state, now)
        session = (state.get("sessions") or {}).get(lease_token)
        if not session or not self._session_matches(session, identity, client_id, lease_token):
            raise LeaseRequiredError(self._public_snapshot(state, now=now))
        return session

    def _assert_revision(self, state: dict[str, Any], base_revision: int, now: float) -> None:
        if int(base_revision) != int(state.get("revision") or 1):
            raise RevisionConflictError(self._public_snapshot(state, now=now))

    @staticmethod
    def _session_matches(
        session: dict[str, Any],
        identity: EditorIdentity,
        client_id: str,
        lease_token: str,
    ) -> bool:
        return (
            session.get("owner_id") == identity.id
            and session.get("client_id") == client_id
            and secrets.compare_digest(str(session.get("token") or ""), str(lease_token or ""))
        )

    def _normalize_state(self, state: dict[str, Any]) -> None:
        state["version"] = STATE_VERSION
        state.setdefault("revision", 1)
        state.setdefault("etag", "")
        state.setdefault("fingerprint", "")
        state.setdefault("source_changed_at", None)
        state.setdefault("aliases", [])
        state.setdefault("workspace_kind", "draft")
        state.setdefault("owner", None)
        state.setdefault("sessions", {})
        state.setdefault("autosave", None)
        state.setdefault("operations", {})
        self._normalize_sessions(state)

    def _normalize_sessions(self, state: dict[str, Any], now: float | None = None) -> None:
        current_time = now or time.time()
        sessions = state.setdefault("sessions", {})
        legacy = state.get("lease")
        if isinstance(legacy, dict) and legacy.get("token"):
            token = str(legacy["token"])
            sessions.setdefault(token, dict(legacy))
            if not state.get("owner"):
                state["owner"] = {
                    "owner_id": legacy.get("owner_id"),
                    "owner_name": legacy.get("owner_name"),
                    "assigned_at": legacy.get("acquired_at") or current_time,
                }
        state["lease"] = None
        expired = [
            token
            for token, session in sessions.items()
            if float(session.get("expires_at") or 0) <= current_time
        ]
        for token in expired:
            sessions.pop(token, None)

    @staticmethod
    def _owner_payload(identity: EditorIdentity, *, now: float | None = None) -> dict[str, Any]:
        return {
            "owner_id": identity.id,
            "owner_name": identity.display_name,
            "assigned_at": now or time.time(),
        }

    def _public_snapshot(
        self,
        state: dict[str, Any],
        *,
        now: float | None = None,
        include_token: bool = False,
        session_token: str | None = None,
    ) -> dict[str, Any]:
        self._normalize_sessions(state, now)
        owner = state.get("owner") or {}
        session = (state.get("sessions") or {}).get(str(session_token or ""))
        public_lease = None
        if session:
            public_lease = {
                "owner_id": session.get("owner_id"),
                "owner_name": session.get("owner_name"),
                "client_id": session.get("client_id"),
                "acquired_at": session.get("acquired_at"),
                "expires_at": session.get("expires_at"),
            }
            if include_token:
                public_lease["token"] = session.get("token")
        elif owner:
            public_lease = {
                "owner_id": owner.get("owner_id"),
                "owner_name": owner.get("owner_name"),
                "client_id": None,
                "acquired_at": owner.get("assigned_at"),
                "expires_at": None,
            }
        autosave = state.get("autosave") or {}
        return {
            "document_id": state["document_id"],
            "revision": int(state.get("revision") or 1),
            "etag": state.get("etag") or self._etag(state),
            "lease": public_lease,
            "owner": owner or None,
            "workspace_kind": state.get("workspace_kind") or "draft",
            "server_document": autosave.get("document"),
            "autosaved_at": autosave.get("updated_at"),
            "source_changed_at": state.get("source_changed_at"),
        }

    def _state_path(self, document_id: str) -> Path:
        safe = hashlib.sha256(document_id.encode("utf-8")).hexdigest()
        return self.root / f"{safe}.json"

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
