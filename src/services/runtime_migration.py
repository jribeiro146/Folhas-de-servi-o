"""Explicit offline format migration at unchanged storage paths.

No configuration, application or transport imports. The caller must stop ALL
writers, including releases which predate admission. Preparation keeps a private
snapshot; publication is resumable and activates the runtime contract last.
"""
from contextlib import ExitStack, closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import uuid

from src.services.editing_state_format import (
    decode_state_object, validate_editing_state, EditingStateQuarantinedError,
)
from src.services.file_mutex import file_mutex
from src.services.runtime_contract import RuntimeContract, CONTRACT_FILE, SUPPORTED_FORMATS
from src.services.sqlite_storage_format import (
    SCHEMAS, IDENTITIES, VERSIONS, _shape, _expected_shape, initialize_store,
)

STORES = {"editing": "editing-state/editing-state.sqlite3", "queue": "graph-sync.sqlite3"}


class MigrationError(ValueError):
    pass


def _digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _local(path):
    path = Path(path)
    if not path.is_absolute() or str(path).startswith(("//", "\\\\")):
        raise MigrationError("Indique caminhos absolutos em disco local.")
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise MigrationError("Ligações simbólicas/junções não são suportadas.")
    path = path.resolve()
    if path == Path(path.anchor):
        raise MigrationError("Não usar a raiz do disco.")
    return path


def _store_digest(path):
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
        db.execute("BEGIN")
        if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise MigrationError("Base SQLite inválida; original preservado.")
        for pragma in ("application_id", "user_version"):
            digest.update(str(db.execute(f"PRAGMA {pragma}").fetchone()[0]).encode())
        for line in db.iterdump():
            digest.update(line.encode("utf-8") + b"\n")
    return digest.hexdigest()


def _files(roots, data, *, work_id):
    excluded = {str(data / ".runtime-migration.lock")}
    for name in (*STORES.values(), CONTRACT_FILE):
        target = data / name
        excluded.update(str(target) + suffix for suffix in ("", "-wal", "-shm", "-journal"))
        excluded.add(str(target.with_name(target.name + "." + work_id + ".migration")))
    result = {}
    for index, root in enumerate(roots):
        for path in sorted(root.rglob("*")):
            if path.is_symlink() or path.is_junction():
                raise MigrationError("Ligação inesperada no armazenamento; cópia recusada.")
            if path.is_file() and str(path) not in excluded:
                result[f"{index}/{path.relative_to(root).as_posix()}"] = _digest(path)
    return result


@contextmanager
def _stopped(data):
    """Fence modern writers and reserve all stores for a coherent snapshot.

    SQLite locks cannot stop old file-only writers; --writers-stopped remains
    mandatory. Changes to ordinary files are detected before and after copying.
    """
    with ExitStack() as stack:
        contract = None
        if (data / CONTRACT_FILE).exists():
            contract = stack.enter_context(RuntimeContract(data, "0" * 64)._session(
                exclusive=True, require_match=False))
        for name in STORES.values():
            path = data / name
            if path.exists():
                db = stack.enter_context(closing(sqlite3.connect(
                    path.as_uri() + "?mode=rw", uri=True, timeout=1)))
                db.execute("BEGIN IMMEDIATE")
        yield contract


def _copy_database(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as reader:
        with closing(sqlite3.connect(target)) as writer:
            # A separate reader avoids backing up the connection that holds a
            # write reservation. SQLite includes committed WAL pages itself.
            reader.backup(writer)
            writer.execute("PRAGMA journal_mode=DELETE")


def _editing_state(raw, identity):
    state = decode_state_object(raw)
    if state.get("document_id") != identity:
        raise EditingStateQuarantinedError()
    if type(state.get("version")) is int and state["version"] == 1:
        required = {"version", "document_id", "revision", "etag", "fingerprint", "aliases", "lease", "autosave", "operations"}
        if (not required.issubset(state)
                or any(key in state for key in ("source_changed_at", "workspace_kind", "owner", "sessions"))):
            raise EditingStateQuarantinedError()
        state.update(version=2, source_changed_at=None, workspace_kind="draft", owner=None, sessions={})
    if state.get("version") == 2:
        # a1cac3c retained the v1 lease until normalization. Preserve even expired
        # reservations and derive ownership only from explicit historical proof.
        lease = state.get("lease")
        if isinstance(lease, dict) and "last_seen_at" not in lease:
            lease["last_seen_at"] = lease.get("acquired_at")
            token = lease.get("token")
            if token in state.get("sessions", {}) and state["sessions"][token] != lease:
                raise EditingStateQuarantinedError()
            state["sessions"][token] = dict(lease)
            if state.get("owner") is None:
                state["owner"] = {"owner_id": lease.get("owner_id"), "owner_name": lease.get("owner_name"),
                                  "assigned_at": lease.get("acquired_at")}
            state["lease"] = None
    validate_editing_state(state, identity)
    return json.dumps(state, ensure_ascii=False, separators=(",", ":"))


def _known_schema(db, kind):
    identity = db.execute("PRAGMA application_id").fetchone()[0]
    version = db.execute("PRAGMA user_version").fetchone()[0]
    shape = _shape(db)
    if identity == IDENTITIES[kind] and version == VERSIONS[kind] and shape == _expected_shape(kind):
        return version
    if kind == "editing" and identity == version == 0:
        with closing(sqlite3.connect(":memory:")) as legacy:
            legacy.execute(SCHEMAS["editing"][0])
            if shape == _shape(legacy):
                return 0  # exact a1cac3c schema, before admin audit table
    if kind == "queue" and ((identity == IDENTITIES[kind] and version in (1, 2))
                            or identity == version == 0):
        statements = list(SCHEMAS["queue"] if version == 2 else SCHEMAS["queue"][:1])
        if version == 0:
            # Exact a1cac3c schema. Earlier layouts missing retryable/max_attempts
            # are deliberately not inferred from a few familiar column names.
            statements[0] = statements[0].replace(",\n            attempt_token TEXT NOT NULL DEFAULT ''", "")
        with closing(sqlite3.connect(":memory:")) as legacy:
            for statement in statements:
                legacy.execute(statement)
            if shape == _shape(legacy):
                return version
    raise MigrationError(f"Formato {kind} desconhecido; nenhuma publicação permitida.")


def _convert_editing(snapshot, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = {}
    audit = []
    database = snapshot / STORES["editing"]
    if database.exists():
        with closing(sqlite3.connect(database)) as db:
            version = _known_schema(db, "editing")
            rows.update((row[0], (row[1], row[2])) for row in db.execute("SELECT * FROM editing_states"))
            if version:
                audit = list(db.execute("SELECT * FROM admin_actions"))
    for path in sorted((snapshot / "editing-state").glob("*.json")):
        raw = path.read_text(encoding="utf-8")
        state = decode_state_object(raw)
        identity = state.get("document_id")
        if (not isinstance(identity, str) or not identity.strip()
                or path.name != hashlib.sha256(identity.encode()).hexdigest() + ".json"):
            raise MigrationError("Snapshot sem identidade comprovada; preservado para diagnóstico.")
        # Historical JSON is recovery evidence. A later SQLite row is authoritative.
        rows.setdefault(identity, (raw, path.stat().st_mtime))
    counts = {"editing_converted": 0, "editing_quarantined": 0}
    with closing(sqlite3.connect(target)) as db:
        initialize_store(db, "editing")
        for identity, (raw, updated) in rows.items():
            try:
                converted = _editing_state(raw, identity)
                counts["editing_converted"] += 1
            except (EditingStateQuarantinedError, TypeError, KeyError):
                # Keep the exact original evidence. M04 refuses this aggregate;
                # migration must not create a fresh editable state in its place.
                converted = raw
                counts["editing_quarantined"] += 1
            db.execute("INSERT INTO editing_states VALUES(?,?,?)", (identity, converted, updated))
        db.executemany("INSERT INTO admin_actions VALUES(?,?,?,?,?,?,?)", audit)
        db.commit()
    return counts


def _convert_queue(source, target):
    with closing(sqlite3.connect(target)) as writer:
        initialize_store(writer, "queue")
        if not source.exists():
            return {"jobs_quarantined": 0}
        held = 0
        with closing(sqlite3.connect(source)) as reader:
            reader.row_factory = sqlite3.Row
            version = _known_schema(reader, "queue")
            for original in reader.execute("SELECT * FROM graph_sync_jobs"):
                row = dict(original)
                row.setdefault("attempt_token", "")
                if version < 3 and row["status"] != "complete":
                    # Legacy versions have weaker remote-effect evidence. Keep
                    # the raw receipt, payload and attempt count; no implicit retry
                    # or admin button can turn missing evidence into permission.
                    row.update(status="failed", retryable=0,
                               last_error="Trabalho legado preservado; requer reconciliação após migração.",
                               result_json=json.dumps({"invalid_state": True, "migration_hold": True,
                                                       "previous_result_json": original["result_json"],
                                                       "previous_status": original["status"],
                                                       "previous_error": original["last_error"]}))
                    held += 1
                columns = [info[1] for info in writer.execute("PRAGMA table_info(graph_sync_jobs)")]
                writer.execute("INSERT INTO graph_sync_jobs VALUES(" + ",".join("?" for _ in columns) + ")",
                               tuple(row[column] for column in columns))
            if version >= 2:
                writer.executemany("INSERT INTO graph_mail_messages VALUES(?,?,?,?,?,?,?,?,?)",
                                   (tuple(row) for row in reader.execute("SELECT * FROM graph_mail_messages")))
        writer.commit()
        return {"jobs_quarantined": held}


def prepare(data, extra_roots, work, *, source_sha256):
    data, work = _local(data), _local(work)
    roots = [data]
    for path in map(_local, extra_roots):
        if path.is_relative_to(data):
            continue
        if any(path.is_relative_to(root) or root.is_relative_to(path) for root in roots):
            raise MigrationError("Raízes de dados sobrepostas.")
        roots.append(path)
    if (any(work.is_relative_to(root) or root.is_relative_to(work) for root in roots)
            or any(not root.is_dir() for root in roots)):
        raise MigrationError("Usar workspace separado e raízes existentes.")
    work.mkdir(parents=True, exist_ok=True, mode=0o700)
    request = {"data": str(data), "roots": list(map(str, roots)), "source_sha256": source_sha256}
    with file_mutex(work / ".lock"), file_mutex(data / ".runtime-migration.lock"):
        binding = work / "request.json"
        if binding.exists() and json.loads(binding.read_text(encoding="utf-8")) != request:
            raise MigrationError("Workspace pertence a outra preparação.")
        _json(binding, request)
        plan_path = work / "plan.json"
        if plan_path.exists():
            return json.loads(plan_path.read_text(encoding="utf-8"))["report"]
        attempt = uuid.uuid4().hex
        stage = work / attempt
        stage.mkdir(mode=0o700)
        with _stopped(data) as contract:
            if contract:
                formats = contract[1]["formats"]
                if (set(formats) != set(SUPPORTED_FORMATS)
                        or formats.get("queue") not in (1, 2, 3)
                        or any(formats[key] != value for key, value in SUPPORTED_FORMATS.items() if key != "queue")):
                    raise MigrationError("Contrato persistente desconhecido; migração recusada.")
            files = _files(roots, data, work_id=attempt)
            before = {kind: _store_digest(data / name) for kind, name in STORES.items()}
            for name, expected in files.items():
                index, relative = name.split("/", 1)
                source = roots[int(index)] / relative
                target = stage / "original" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                if _digest(target) != expected:
                    raise MigrationError("Ficheiro alterado durante a cópia.")
            for kind, name in STORES.items():
                if before[kind] is not None:
                    _copy_database(data / name, stage / "original" / "0" / name)
            if contract:
                # Never open/close a raw descriptor for a live SQLite file: on
                # POSIX that can release this process's locks. Serialize through
                # the connection which already owns the exclusive transaction.
                original_contract = stage / "original" / "0" / CONTRACT_FILE
                original_contract.parent.mkdir(parents=True, exist_ok=True)
                original_contract.write_bytes(contract[0].serialize())
            if files != _files(roots, data, work_id=attempt):
                raise MigrationError("Armazenamento mudou durante a preparação.")
            target_root = stage / "prepared"
            RuntimeContract(target_root, source_sha256).initialize_empty()
            report = _convert_editing(stage / "original" / "0", target_root / STORES["editing"])
            queue = stage / "original" / "0" / STORES["queue"]
            report.update(_convert_queue(queue, target_root / STORES["queue"]))
            prepared = {kind: _store_digest(target_root / name) for kind, name in STORES.items()}
            plan = {**request, "format": 1, "attempt": attempt, "state": "prepared", "files": files,
                    "before": before, "prepared": prepared, "runtime_before": contract[1] if contract else None,
                    "runtime_prepared_hash": _digest(target_root / CONTRACT_FILE),
                    "runtime_original_hash": _digest(stage / "original" / "0" / CONTRACT_FILE) if contract else None,
                    "report": {**report, "state": "prepared", "files_preserved": len(files)}}
            _json(plan_path, plan)
            return plan["report"]


def _publish(source, target, work_id):
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + "." + work_id + ".migration")
    shutil.copy2(source, temporary)
    with temporary.open("r+b") as stream:
        os.fsync(stream.fileno())
    os.replace(temporary, target)


def apply(work, *, source_sha256):
    work = _local(work)
    with file_mutex(work / ".lock"), ExitStack() as publication_locks:
        plan_path = work / "plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        if plan["source_sha256"] != source_sha256:
            raise MigrationError("Usar a mesma versão que preparou a migração.")
        if plan["state"] == "complete":
            return plan["report"]
        data = _local(plan["data"])
        publication_locks.enter_context(file_mutex(data / ".runtime-migration.lock"))
        roots = list(map(_local, plan["roots"]))
        stage = work / plan["attempt"]
        if _digest(stage / "prepared" / CONTRACT_FILE) != plan["runtime_prepared_hash"]:
            raise MigrationError("Contrato candidato alterado.")
        if (plan["runtime_original_hash"] is not None
                and _digest(stage / "original" / "0" / CONTRACT_FILE) != plan["runtime_original_hash"]):
            raise MigrationError("Cópia do contrato original alterada.")
        for name, expected in plan["files"].items():
            if _digest(stage / "original" / name) != expected:
                raise MigrationError("Cópia original alterada; publicação recusada.")
        for kind, name in STORES.items():
            if _store_digest(stage / "original" / "0" / name) != plan["before"][kind]:
                raise MigrationError("Cópia SQLite original alterada.")
        with _stopped(data) as contract:
            if _files(roots, data, work_id=plan["attempt"]) != plan["files"]:
                raise MigrationError("Ficheiros alterados desde a preparação; publicação recusada.")
            for kind, name in STORES.items():
                if _store_digest(stage / "prepared" / name) != plan["prepared"][kind]:
                    raise MigrationError("Candidato de migração alterado.")
                if _store_digest(data / name) not in (plan["before"][kind], plan["prepared"][kind]):
                    raise MigrationError("Base alterada desde a preparação; publicação recusada.")
            if contract:
                previous = plan["runtime_before"]
                expected = previous["source_sha256"] if previous else None
                completed = RuntimeContract(stage / "prepared", source_sha256).inspect()
                # The last rename may succeed before the process can record
                # completion. Recognize that exact published contract on resume.
                already_published = plan["state"] == "applying" and contract[1] == {
                    **completed, "matches_release": source_sha256 == "0" * 64}
                if already_published and all(_store_digest(data / name) == plan["prepared"][kind]
                                             for kind, name in STORES.items()):
                    plan["state"] = plan["report"]["state"] = "complete"
                    _json(plan_path, plan)
                    return plan["report"]
                allowed = {expected} if plan["state"] == "prepared" else {expected, "0" * 64, source_sha256}
                if (contract[1]["source_sha256"] not in allowed
                        or not previous or any(contract[1][key] != previous[key] for key in ("epoch", "formats"))):
                    raise MigrationError("Contrato alterado desde a preparação.")
            elif plan["runtime_before"] is not None:
                raise MigrationError("Contrato original desapareceu.")
            plan["state"] = "applying"
            _json(plan_path, plan)
            if contract:
                # Revoke admission BEFORE publishing the first changed database.
                # A crash leaves all modern releases refused until this plan resumes.
                contract[0].execute("UPDATE state_contract SET source_sha256=? WHERE singleton=1", ("0" * 64,))
                contract[0].commit()
        for kind, name in STORES.items():
            target = data / name
            if _store_digest(target) == plan["prepared"][kind]:
                continue
            if target.exists():
                with closing(sqlite3.connect(target, timeout=1)) as db:
                    result = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                    if result[0] != 0:
                        raise MigrationError("SQLite ocupado; manter escritores parados e retomar.")
                if any(Path(str(target) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
                    raise MigrationError("SQLite ainda aberto; não substituir uma base com journals.")
            _publish(stage / "prepared" / name, target, plan["attempt"])
        _publish(stage / "prepared" / CONTRACT_FILE, data / CONTRACT_FILE, plan["attempt"])
        RuntimeContract(data, source_sha256).inspect()
        plan["state"] = "complete"
        plan["report"]["state"] = "complete"
        _json(plan_path, plan)
        return plan["report"]
