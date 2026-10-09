"""Offline, same-path recovery. No application/configuration/transport imports.

Stop every writer first, including old releases and external file writers. SQLite
exclusion and before/after hashes supplement that operational requirement; they
cannot fence an unrelated process which ignores the application's contract.
"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import re
import uuid

from src.services.file_mutex import file_mutex
from src.services.runtime_contract import RuntimeContract, CONTRACT_FILE, SUPPORTED_FORMATS
from src.services.sqlite_storage_format import validate_store
from src.services.runtime_migration import (
    STORES, MigrationError, _local, _digest, _json, _files, _stopped, _copy_database,
    _legacy_evidence, _store_digest,
)


class BackupError(ValueError):
    pass


def _key_check(key, snapshot_id):
    if not isinstance(key, bytes) or len(key) < 32:
        raise BackupError("Forneça a chave exata da aplicação por ficheiro privado separado (mínimo 32 bytes).")
    return hmac.new(key, ("runtime-backup-v1:" + snapshot_id).encode(), hashlib.sha256).hexdigest()


def _manifest_mac(manifest, key):
    # Authenticate paths, hashes AND privileged metadata; checksums alone allow
    # someone who can alter a backup to substitute a root-owned executable.
    content = {name: value for name, value in manifest.items() if name != "manifest_hmac"}
    canonical = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(key, b"runtime-backup-manifest-v1\n" + canonical, hashlib.sha256).hexdigest()


def _validate_access(metadata):
    if (any(type(metadata.get(name)) is not int or metadata[name] < 0 for name in ("mode", "uid", "gid", "mtime_ns"))
            or metadata["mode"] & ~0o777):
        raise BackupError("Atributos de acesso inválidos ou bits especiais; requerem avaliação explícita do instalador.")


def _separate(paths):
    if any(a == b or a.is_relative_to(b) or b.is_relative_to(a)
           for index, a in enumerate(paths) for b in paths[index + 1:]):
        raise BackupError("Raízes sobrepostas ou backup/workspace dentro dos dados.")


def _attributes(path):
    info = path.stat()
    attributes = {"mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid,
                  "mtime_ns": info.st_mtime_ns}
    _validate_access(attributes)
    return attributes


def _directories(roots):
    result = {}
    for index, root in enumerate(roots):
        for path in [root, *sorted(root.rglob("*"))]:
            _local(path)
            if path.is_dir():
                name = f"{index}" if path == root else f"{index}/{path.relative_to(root).as_posix()}"
                result[name] = _attributes(path)
    return result


def _current_stores(data):
    for kind, name in STORES.items():
        path = data / name
        if path.exists():
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                validate_store(db, kind)
        elif kind == "editing" and any(path.parent.glob("*.json")):
            raise BackupError("Estado legado requer migração M06 antes deste backup.")


def backup(data, extra_roots, destination, *, source_sha256, key):
    data, destination = _local(data), _local(destination)
    roots = [data, *map(_local, extra_roots)]
    _separate([*roots, destination])
    snapshot_id = uuid.uuid4().hex
    proof = _key_check(key, snapshot_id)
    if not all(path.is_dir() for path in roots):
        raise BackupError("Todas as raízes inventariadas têm de existir.")
    # A failed/incomplete attempt is evidence, not a directory to overwrite.
    destination.mkdir(mode=0o700)
    with file_mutex(data / ".runtime-migration.lock"), _stopped(data) as contract:
        if not contract or not contract[1]["formats_compatible"] or contract[1]["source_sha256"] != source_sha256:
            raise BackupError("Backup exige contrato atual, ativo e compatível com este artefacto.")
        _current_stores(data)
        files = _files(roots, data, work_id=snapshot_id)
        dirs = _directories(roots)
        metadata = {}
        for name in files:
            index, relative = name.split("/", 1)
            source = roots[int(index)] / relative
            target = destination / "files" / name
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            metadata[name] = _attributes(source)
            shutil.copy2(source, target)
            if _digest(target) != files[name]:
                raise BackupError("Ficheiro mudou durante o backup.")
            os.chmod(target, 0o600)
        for name in (*STORES.values(), CONTRACT_FILE):
            source = data / name
            if not source.exists():
                continue
            target = destination / "files/0" / name
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if name == CONTRACT_FILE:
                # Keep the POSIX SQLite lock: do not open/close a raw live fd.
                target.write_bytes(contract[0].serialize())
            else:
                _copy_database(source, target)
            os.chmod(target, 0o600)
            key_name = "0/" + name
            metadata[key_name] = _attributes(source)
        if files != _files(roots, data, work_id=snapshot_id) or set(dirs) != set(_directories(roots)):
            raise BackupError("Ficheiros alterados durante o backup.")
        for name, attributes in metadata.items():
            target = destination / "files" / name
            with target.open("r+b") as stream:
                os.fsync(stream.fileno())
            attributes["sha256"] = _digest(target)
        manifest = {"format": 1, "snapshot_id": snapshot_id, "created_at": datetime.now(timezone.utc).isoformat(),
                    "source_sha256": source_sha256, "roots": list(map(str, roots)), "files": metadata,
                    "directories": dirs, "key_check": proof}
        manifest["manifest_hmac"] = _manifest_mac(manifest, key)
        _json(destination / "backup.json", manifest)
        os.chmod(destination / "backup.json", 0o600)
    return verify(destination, key=key)


def _entry(name, roots):
    parts = PurePosixPath(name).parts
    if (not parts or parts[0] not in {str(i) for i in range(len(roots))}
            or "\\" in name or ":" in name or any(p in (".", "..") for p in parts)
            or name != "/".join(parts)):
        raise BackupError("Caminho inválido no manifesto.")
    return _local(roots[int(parts[0])].joinpath(*parts[1:]))


def _manifest(destination, key):
    destination = _local(destination)
    manifest = json.loads((destination / "backup.json").read_text(encoding="utf-8"))
    if manifest["format"] != 1 or not hmac.compare_digest(
            manifest["key_check"], _key_check(key, manifest["snapshot_id"])):
        raise BackupError("Formato ou chave da cópia incompatível; nenhum restauro publicado.")
    if (not isinstance(manifest.get("manifest_hmac"), str)
            or not hmac.compare_digest(manifest["manifest_hmac"], _manifest_mac(manifest, key))):
        raise BackupError("Autenticidade do manifesto não confirmada; nenhum restauro publicado.")
    roots = list(map(_local, manifest["roots"]))
    _separate([*roots, destination])
    for name, metadata in manifest["directories"].items():
        _validate_access(metadata)
        _entry(name, roots)
    for name, metadata in manifest["files"].items():
        _validate_access(metadata)
        _entry(name, roots)
        source = _local(destination / "files" / name)
        if _digest(source) != metadata["sha256"]:
            raise BackupError("Cópia incompleta ou alterada; checksum incompatível.")
    snapshot = destination / "files/0"
    with RuntimeContract(snapshot, manifest["source_sha256"]).operation():
        _current_stores(snapshot)
    return manifest


def verify(destination, *, key):
    manifest = _manifest(destination, key)
    return {"state": "verified", "snapshot_id": manifest["snapshot_id"],
            "files": len(manifest["files"]), "created_at": manifest["created_at"]}


HOLD_FORMATS = {**SUPPORTED_FORMATS, "restore_hold": 1}


def _hold_queue(path):
    if not path.exists():
        return
    with closing(sqlite3.connect(path)) as db:
        for job_id, kind, status, raw_result in db.execute(
                "SELECT id,kind,status,result_json FROM graph_sync_jobs WHERE status != 'complete'").fetchall():
            evidence = _legacy_evidence(kind, raw_result)
            result = {**evidence, "invalid_state": True, "restore_uncertain": True,
                      "previous_status": status, "previous_result_json": raw_result}
            db.execute("UPDATE graph_sync_jobs SET status='failed',retryable=0,attempt_token='',result_json=?,"
                       "last_error=? WHERE id=?", (json.dumps(result), "Retido após restauro; reconciliar antes de qualquer repetição.", job_id))
        db.commit()


def _access(path, metadata):
    # Preserve the app UID even when recovery is run by root. On Windows use the
    # private destination's inherited ACL; POSIX metadata cannot represent ACLs.
    _validate_access(metadata)
    if hasattr(os, "chown"):
        os.chown(path, metadata["uid"], metadata["gid"])
    os.chmod(path, metadata["mode"])
    os.utime(path, ns=(metadata["mtime_ns"], metadata["mtime_ns"]))


def _publish_recovery(source, target, metadata, snapshot_id):
    temporary = target.with_name(target.name + ".restore-" + snapshot_id)
    _local(temporary)
    shutil.copyfile(source, temporary)
    with temporary.open("r+b") as stream:
        os.fsync(stream.fileno())
    _access(temporary, metadata)
    os.replace(temporary, target)


def _prepare_restore(destination, workspace, manifest, source_sha256):
    roots = list(map(_local, manifest["roots"]))
    if any(root.exists() and any(root.iterdir()) for root in roots):
        raise BackupError("Restauro só para raízes vazias; preserve os dados existentes noutro local.")
    candidates = workspace / ("candidate-" + uuid.uuid4().hex)
    candidates.mkdir(mode=0o700)
    files = json.loads(json.dumps(manifest["files"]))
    for name in (CONTRACT_FILE, STORES["queue"]):
        source = destination / "files/0" / name
        if not source.exists():
            continue
        target = candidates / name
        shutil.copyfile(source, target)
        os.chmod(target, 0o600)
        if name == CONTRACT_FILE:
            with closing(sqlite3.connect(target)) as db:
                # An unknown required format blocks old releases too. Merely
                # replacing source_sha256 could be bypassed by old activate.
                db.execute("UPDATE state_contract SET source_sha256=?,formats_json=?,epoch=epoch+1",
                           (source_sha256, json.dumps(HOLD_FORMATS, sort_keys=True)))
                db.commit()
        else:
            _hold_queue(target)
        files["0/" + name]["sha256"] = _digest(target)
    for name in STORES.values():
        if "0/" + name in files:
            source = candidates / name if name == STORES["queue"] else destination / "files/0" / name
            files["0/" + name]["logical_sha256"] = _store_digest(source)
    plan = {"format": 1, "state": "restoring", "snapshot_id": manifest["snapshot_id"],
            "backup": str(destination), "backup_hash": _digest(destination / "backup.json"),
            "source_sha256": source_sha256, "candidate": candidates.name,
            "files": files, "directories": manifest["directories"], "roots": manifest["roots"]}
    _json(workspace / "restore.json", plan)
    return plan


def _matches(path, metadata):
    # A diagnostic reader may legitimately change SQLite's journal mode/header.
    # Compare committed logical content (including WAL), never discard journals.
    return (_store_digest(path) == metadata["logical_sha256"] if "logical_sha256" in metadata
            else _digest(path) == metadata["sha256"])


def _check_targets(plan, roots, *, skip_contract=False, complete=False):
    expected = {_entry(name, roots): metadata for name, metadata in plan["files"].items()}
    directories = {_entry(name, roots) for name in plan["directories"]}
    temporary = {path.with_name(path.name + ".restore-" + plan["snapshot_id"]) for path in expected}
    lock = roots[0] / ".runtime-migration.lock"
    if complete and (any(not path.is_file() for path in expected) or any(not path.is_dir() for path in directories)):
        raise BackupError("Restauro incompleto; faltam ficheiros ou diretórios.")
    for root in roots:
        for path in root.rglob("*"):
            _local(path)
            if skip_contract and path == roots[0] / CONTRACT_FILE:
                continue
            if path == lock or path in temporary:
                continue
            if path.is_dir() and path in directories:
                continue
            if path not in expected or not path.is_file() or not _matches(path, expected[path]):
                raise BackupError("Destino contém conteúdo diferente do restauro; nada será sobrescrito.")


def _load_plan(workspace, source_sha256, key):
    plan = json.loads((workspace / "restore.json").read_text(encoding="utf-8"))
    destination = _local(plan["backup"])
    manifest = _manifest(destination, key)
    roots = list(map(_local, plan["roots"]))
    _separate([*roots, destination, workspace])
    if (plan["format"] != 1 or plan["state"] not in {"restoring", "held", "releasing", "released"}
            or re.fullmatch(r"candidate-[0-9a-f]{32}", plan["candidate"]) is None
            or plan["source_sha256"] != source_sha256
            or plan["snapshot_id"] != manifest["snapshot_id"] or plan["roots"] != manifest["roots"]
            or plan["backup_hash"] != _digest(destination / "backup.json")):
        raise BackupError("Plano, cópia e versão não correspondem; usar o mesmo workspace/artefacto.")
    return plan, destination, roots


def restore(destination, workspace, *, source_sha256, key):
    destination, workspace = _local(destination), _local(workspace)
    manifest = _manifest(destination, key)  # Check key/integrity BEFORE touching targets.
    roots = list(map(_local, manifest["roots"]))
    _separate([*roots, destination, workspace])
    workspace.mkdir(mode=0o700, exist_ok=True)
    with file_mutex(workspace / ".lock"):
        if not (workspace / "restore.json").exists():
            _prepare_restore(destination, workspace, manifest, source_sha256)
        plan, actual_backup, roots = _load_plan(workspace, source_sha256, key)
        if destination != actual_backup:
            raise BackupError("Este workspace pertence a outra cópia.")
        if plan["state"] == "released":
            return {"state": "released", "snapshot_id": plan["snapshot_id"]}
        if plan["state"] == "releasing":
            # Never roll back the decision state or replace its original audit.
            # The contract may already be active after a lost completion reply.
            return {"state": "releasing", "snapshot_id": plan["snapshot_id"], "next_command": "release"}
        with file_mutex(roots[0] / ".runtime-migration.lock"):
            _check_targets(plan, roots, complete=plan["state"] == "held")
            for name, metadata in sorted(plan["directories"].items(), key=lambda pair: len(PurePosixPath(pair[0]).parts)):
                target = _entry(name, roots)
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
                _access(target, metadata)
            # Publish the global hold FIRST; absent contracts also refuse admission.
            order = sorted(plan["files"], key=lambda name: name != "0/" + CONTRACT_FILE)
            for name in order:
                metadata, target = plan["files"][name], _entry(name, roots)
                if name in {"0/" + CONTRACT_FILE, "0/" + STORES["queue"]}:
                    source = _local(workspace / plan["candidate"] / name[2:])
                else:
                    source = _local(destination / "files" / name)
                if _digest(source) != metadata["sha256"]:
                    raise BackupError("Candidato de recuperação alterado.")
                if not target.exists():
                    _publish_recovery(source, target, metadata, plan["snapshot_id"])
            _check_targets(plan, roots, complete=True)
            for name, metadata in plan["directories"].items():
                _access(_entry(name, roots), metadata)
            plan["state"] = "held"
            _json(workspace / "restore.json", plan)
    return {"state": "held", "snapshot_id": plan["snapshot_id"], "reconciliation_required": True}


def release_hold(workspace, *, source_sha256, key, actor, reason, external_effects_reconciled):
    """Explicit operator attestation, never an automatic external reconciliation.

    The operator must consider ALL effects after the snapshot, including jobs and
    documents absent from it. Restored non-complete jobs remain individually held.
    """
    if external_effects_reconciled is not True or not actor.strip() or len(reason.strip()) < 10:
        raise BackupError("Identifique responsável/motivo e confirme a reconciliação de todos os efeitos posteriores.")
    workspace = _local(workspace)
    with file_mutex(workspace / ".lock"):
        plan, _, roots = _load_plan(workspace, source_sha256, key)
        if plan["state"] not in {"held", "releasing", "released"}:
            raise BackupError("Concluir e verificar o restauro antes de libertar.")
        if plan["state"] == "released":
            return {"state": "released", "snapshot_id": plan["snapshot_id"]}
        with file_mutex(roots[0] / ".runtime-migration.lock"):
            contract = RuntimeContract(roots[0], source_sha256)
            with contract._session(exclusive=True, require_match=False) as (db, current):
                candidate = RuntimeContract(workspace / plan["candidate"], source_sha256).inspect()
                if (current["source_sha256"] != source_sha256 or current["epoch"] != candidate["epoch"]
                        or current["formats"] not in (HOLD_FORMATS, SUPPORTED_FORMATS)):
                    raise BackupError("Contrato de recuperação alterado; não libertar.")
                audit_path = workspace / "reconciliation.json"
                if current["formats"] == HOLD_FORMATS:
                    # Repeat after an interrupted decision too; reject unexpected
                    # files, without opening a raw descriptor of this live contract.
                    _check_targets(plan, roots, skip_contract=True, complete=True)
                if plan["state"] == "held":
                    if current["formats"] != HOLD_FORMATS:
                        raise BackupError("Bloqueio de recuperação inesperadamente ausente.")
                    if audit_path.exists():
                        # The decision can be durable even if saving 'releasing'
                        # failed. Preserve that first statement on the retry.
                        decision = json.loads(audit_path.read_text(encoding="utf-8"))
                        if (not isinstance(decision, dict) or decision.get("snapshot_id") != plan["snapshot_id"]
                                or decision.get("source_sha256") != source_sha256
                                or decision.get("external_effects_reconciled") is not True
                                or not isinstance(decision.get("actor"), str) or not decision["actor"].strip()
                                or not isinstance(decision.get("reason"), str) or len(decision["reason"].strip()) < 10):
                            raise BackupError("Existe uma decisão incompatível; preservada para diagnóstico.")
                    else:
                        decision = {"snapshot_id": plan["snapshot_id"], "source_sha256": source_sha256,
                                    "actor": actor.strip(), "reason": reason.strip(),
                                    "external_effects_reconciled": True, "at": datetime.now(timezone.utc).isoformat()}
                        _json(audit_path, decision)
                    plan["audit_hash"] = _digest(audit_path)
                    plan["state"] = "releasing"
                    _json(workspace / "restore.json", plan)
                if _digest(audit_path) != plan["audit_hash"]:
                    raise BackupError("Decisão de reconciliação alterada.")
                _current_stores(roots[0])
                db.execute("UPDATE state_contract SET formats_json=? WHERE singleton=1",
                           (json.dumps(SUPPORTED_FORMATS, sort_keys=True),))
                db.commit()
            plan["state"] = "released"
            _json(workspace / "restore.json", plan)
    return {"state": "released", "snapshot_id": plan["snapshot_id"]}
