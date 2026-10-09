"""Non-destructive cancellation using fixed Graph identities and file versions.

A folder eTag does not protect descendants. Compare them before the move and
preserve the whole remote bundle, including a child added after that comparison.
An ambiguous PATCH is never replayed by the queue, even after process restart.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import quote

from src.services.graph_storage_service import GraphConflictError


def _text(value):
    return isinstance(value, str) and bool(value) and value == value.strip()


def _receipt(path):
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(result, dict) and _text(result.get("id")) and _text(result.get("eTag")):
            return result
    except (OSError, ValueError):
        pass
    raise GraphConflictError("Falta confirmação da identidade/versão remota; sincronize antes de cancelar.")


def _tree(graph, parent_id, *, local=None, seen=None):
    seen = set() if seen is None else seen
    if parent_id in seen or len(seen) > 10000:
        raise GraphConflictError("Inventário remoto incoerente; cancelamento suspenso.")
    seen.add(parent_id)
    result = []
    for child in graph._list_children(parent_id):
        if (not isinstance(child, dict) or not all(_text(child.get(key)) for key in ("id", "eTag", "name"))
                or child["name"] in {".", ".."} or any(c in child["name"] for c in "/\\")):
            raise GraphConflictError("Inventário remoto sem identidade ou versão; cancelamento suspenso.")
        folder = isinstance(child.get("folder"), dict)
        if not folder and not isinstance(child.get("file"), dict):
            raise GraphConflictError("Tipo remoto não suportado para cancelamento.")
        result.append({"id": child["id"], "eTag": child["eTag"], "name": child["name"],
                       "parent_id": parent_id, "folder": folder})
        child_local = local / child["name"] if local is not None else None
        if folder:
            result.extend(_tree(graph, child["id"], local=child_local, seen=seen))
        elif child_local is not None:
            receipt = _receipt(graph._item_meta_path(child_local))
            if receipt["id"] != child["id"] or receipt["eTag"] != child["eTag"]:
                raise GraphConflictError("Um ficheiro remoto mudou; sincronize antes de cancelar.")
    return sorted(result, key=lambda item: (item["parent_id"], item["id"]))


def prepare_cancellation(graph, source: Path, operation_id: str) -> dict:
    """Read-only proof, saved in the editing operation before any local move."""
    graph.validate_config()
    name = graph.active_source_name(source)
    remote = graph._get_item_by_path(graph._join_graph_path(graph.config.active_path, name))
    result = {"format": 1, "drive_id": graph.config.drive_id, "source_name": name,
              "destination_name": "cancel-" + hashlib.sha256(operation_id.encode()).hexdigest()}
    if remote is None:
        # Absence now is not permission to remove a future same-name item.
        return {**result, "absent": True}
    receipt = _receipt(source.parent / ".graph_bundle.json" if graph._is_local_bundle_file(source)
                       else graph._item_meta_path(source))
    if receipt["id"] != remote.get("id") or receipt["eTag"] != remote.get("eTag"):
        raise GraphConflictError("A folha remota mudou; sincronize antes de cancelar.")
    folder = isinstance(remote.get("folder"), dict)
    return {**result, "absent": False, "source_id": receipt["id"], "source_etag": receipt["eTag"],
            "folder": folder, "children": _tree(graph, receipt["id"], local=source.parent) if folder else []}


def execute_cancellation(graph, plan: dict) -> dict:
    """Move the proven item; never DELETE a folder or rediscover identity by name."""
    if (not isinstance(plan, dict) or type(plan.get("format")) is not int or plan["format"] != 1
            or plan.get("drive_id") != graph.config.drive_id or type(plan.get("absent")) is not bool
            or not _text(plan.get("source_name")) or any(c in plan["source_name"] for c in "/\\")
            or plan["source_name"] in {".", ".."}
            or not _text(plan.get("destination_name"))
            or len(plan["destination_name"]) != 71 or not plan["destination_name"].startswith("cancel-")
            or any(c not in "0123456789abcdef" for c in plan["destination_name"][7:])):
        raise GraphConflictError("Prova de cancelamento inválida; requer reconciliação.")
    if plan["absent"]:
        return {"removed_active": False, "remote_preserved": True, "remote_absent": True}
    if (not _text(plan.get("source_id")) or not _text(plan.get("source_etag"))
            or type(plan.get("folder")) is not bool or not isinstance(plan.get("children"), list)):
        raise GraphConflictError("Prova de identidade incompleta; requer reconciliação.")
    item = graph._get_item_by_path(graph._join_graph_path(graph.config.active_path, plan["source_name"]))
    if (not item or item.get("id") != plan["source_id"] or item.get("eTag") != plan["source_etag"]
            or isinstance(item.get("folder"), dict) != plan["folder"]):
        raise GraphConflictError("O ativo remoto mudou ou desapareceu; requer reconciliação.")
    if plan["folder"] and _tree(graph, plan["source_id"]) != plan["children"]:
        raise GraphConflictError("O conteúdo da pasta remota mudou; cancelamento suspenso.")
    destination = graph._join_graph_path(graph.config.archive_path, "Canceladas")
    graph.ensure_folder_path(destination)
    parent = graph._get_item_by_path(destination)
    if (not parent or not _text(parent.get("id")) or not isinstance(parent.get("folder"), dict)
            or parent["id"] == plan["source_id"]):
        raise GraphConflictError("Destino de cancelamento inválido; origem preservada.")
    body = {"parentReference": {"id": parent["id"]}, "name": plan["destination_name"],
            "@microsoft.graph.conflictBehavior": "fail"}
    raw = graph._graph_bytes("PATCH", f"/drives/{quote(graph.config.drive_id, safe='')}/items/{quote(plan['source_id'], safe='')}",
                             data=json.dumps(body).encode(), content_type="application/json",
                             headers={"If-Match": plan["source_etag"]})
    response = json.loads(raw)
    if (not isinstance(response, dict) or response.get("id") != plan["source_id"]
            or response.get("name") != plan["destination_name"]
            or response.get("parentReference", {}).get("id") != parent["id"]):
        raise RuntimeError("O movimento remoto não ficou confirmado; requer reconciliação.")
    return {"removed_active": True, "remote_preserved": True, "remote_id": plan["source_id"],
            "destination_parent_id": parent["id"], "destination_name": plan["destination_name"]}
