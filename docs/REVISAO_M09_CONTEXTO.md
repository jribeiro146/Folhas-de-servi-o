# Contexto da revisão M09 — 12/10/2026

A fila não é alterada por M09. Estes excertos completos de `src/services/graph_sync_queue.py` (base a1e65afd, iguais no alvo M09) mostram os contratos usados pelas projeções administrativas. Extraídos por AST do código local para caber no pacote sem omitir os consumidores pedidos na ronda 1. A revisão recebe também application/admin, graph_job_visibility e o serviço/repositório de edição completos.

Busca `rg /api/graph/status src/web/static` confirmou um único consumidor: `document-editor.js`. `field-app.js` e `mail-job-monitor.js` são fornecidos integralmente na ronda 2. As ações de admin_change_access são release_sheet e restore_sheet_access; o serviço completo comprova a correspondência.

Os helpers seguintes conservam decisões de incerteza e prazo; M09 apenas reduz os campos exportados. M03 foi aprovado em a1e65afd. Isto não atribui essa aprovação ao código M09.

```python
def delivery_uncertain(kind: str, result: dict[str, Any]) -> bool:
    # Malformed legacy evidence cannot prove whether an external effect occurred.
    # Migration preserves its raw text separately and explicitly requires review.
    if result.get("migration_uncertain") is True or result.get("restore_uncertain") is True:
        return True
    if kind == "remove_active":
        return result.get("cancellation_started") is True and result.get("cancellation_confirmed") is not True
    if kind == "notify_teams":
        return (result.get("phase") in {"sending_teams", "teams_unknown"}
                and result.get("accepted") is not True)
    mail = result.get("mail") if isinstance(result.get("mail"), dict) else {}
    return result.get("phase") in {"sending_mail", "mail_unknown"} and mail.get("accepted") is not True
```

```python
def teams_retry_deadline(kind: str, result: dict[str, Any]) -> float | None:
    value = result.get("retry_not_before") if kind == "notify_teams" else None
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError("Prazo Teams inválido; requer revisão.")
    try:
        deadline = float(value)
    except OverflowError:
        raise ValueError("Prazo Teams inválido; requer revisão.") from None
    if not math.isfinite(deadline):
        raise ValueError("Prazo Teams inválido; requer revisão.")
    return deadline
```

```python
def summary(self) -> dict[str, int]:
        result = {"pending": 0, "running": 0, "failed": 0, "complete": 0,
                  "teams_missing": 0, "teams_conflicts": 0}
        with self._connect() as connection:
            connection.execute("BEGIN")
            rows = connection.execute(
                "SELECT status, COUNT(*) AS total FROM graph_sync_jobs GROUP BY status"
            ).fetchall()
            parents = connection.execute("SELECT p.payload_json,p.result_json,c.id AS child_id "
                "FROM graph_sync_jobs p LEFT JOIN graph_sync_jobs c ON c.id = 'teams:' || p.id "
                "WHERE p.status='complete' AND p.kind IN ('archive_and_remove','archive_and_mail_local')")
            for parent in parents:
                payload = self._json_object(parent["payload_json"])
                outcome = self._json_object(parent["result_json"])
                if outcome.get("teams_child_conflict"):
                    result["teams_conflicts"] += 1
                if payload.get("teams") and parent["child_id"] is None:
                    result["teams_missing"] += 1
        for row in rows:
            result[str(row["status"])] = int(row["total"])
        return result
```
