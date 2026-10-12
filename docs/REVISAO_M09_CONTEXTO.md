# Contexto da revisão cumulativa M09 — fecho dos dois achados, 12/10/2026

A revisão recebe a fila completa, incluindo a escrita de last_error e os logs de falha. A única alteração M09 na fila acrescenta campos seguros ao evento graph_job_failed: não muda persistência, repetição nem reconciliação. O job_id já integrava o evento e permanece a chave de correlação exibida no cartão administrativo.

Busca `rg /api/graph/status src/web/static` confirmou um único consumidor: document-editor.js, fornecido integralmente, assim como mail-job-monitor.js. O legado field-app.js, já fornecido nas revisões anteriores e sem alterações M09, não usa esse endpoint; foi omitido desta cópia de contexto para incluir o teste completo da autenticação dentro do limite do pacote. As ações de admin_change_access são release_sheet e restore_sheet_access; o serviço de edição completo comprova a correspondência.

Para caber no limite do pacote, os excertos abaixo substituem apenas o contexto integral de graph_storage_service.py, que não mudou em M09. Incluem as duas classes de erro e os sete métodos completos de configuração, diagnóstico e transporte que produzem os estados/erros consumidos por M09. Foram extraídos por AST do código local, igual à base a1e65afd nestas funções. Os imports relevantes no módulo são urllib.error/parse/request, json e time. As alterações são fornecidas integralmente no diff, sem caminhos excluídos. Ficheiros novos e documentação integral já estão no diff e não precisam de cópia duplicada em target_context.

```python
class GraphStorageError(Exception):
    """Raised when Microsoft Graph cannot complete a storage operation."""
```

```python
class GraphConflictError(GraphStorageError):
    """Raised when a conditional Graph update targets an obsolete eTag."""

    retryable = False

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code
```

```python
def validate_config(self) -> None:
        missing = self.config.missing_fields()
        if missing:
            raise GraphStorageError(
                "Configuração Microsoft Graph incompleta: " + ", ".join(missing)
            )
```

```python
def status(self) -> dict[str, object]:
        return {
            "configured": not self.config.missing_fields(),
            "missing_fields": self.config.missing_fields(),
            "drive_id": bool(self.config.drive_id),
            "active_path": self.config.active_path,
            "archive_path": self.config.archive_path,
        }
```

```python
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
```

```python
def _graph_json(self, method: str, path: str, data: bytes | None = None, content_type: str | None = None) -> dict:
        url = f"{GRAPH_ROOT}{path}"
        headers = {"Accept": "application/json"}
        if content_type:
            headers["Content-Type"] = content_type
        return self._request_json(url, method=method, data=data, headers=headers)
```

```python
def _graph_bytes(
        self,
        method: str,
        path: str,
        data: bytes | None = None,
        content_type: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        url = f"{GRAPH_ROOT}{path}"
        request_headers = dict(headers or {})
        if content_type:
            request_headers["Content-Type"] = content_type
        return self._request_bytes(url, method=method, data=data, headers=request_headers)
```

```python
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
            return self._open_request_with_retry(req, method)
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code in {409, 412}:
                raise GraphConflictError(
                    "O ficheiro foi alterado no SharePoint por outro utilizador.",
                    status_code=exc.code) from exc
            raise GraphStorageError(f"Microsoft Graph HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise GraphStorageError(f"Microsoft Graph indisponível: {exc}") from exc
```

```python
def _open_request_with_retry(req: request.Request, method: str) -> bytes:
        attempts = 2 if method.upper() == "GET" else 1

        for attempt in range(attempts):
            started = time.perf_counter()
            http_status = None
            error_type = None
            response_headers = None
            try:
                with request.urlopen(req, timeout=60) as response:
                    http_status = getattr(response, "status", 200)
                    response_headers = getattr(response, "headers", None)
                    return response.read()
            except error.HTTPError as exc:
                http_status = exc.code
                error_type = type(exc).__name__
                response_headers = exc.headers
                if exc.code not in {429, 503} or attempt + 1 >= attempts:
                    raise

                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    delay_seconds = float(retry_after) if retry_after is not None else 1.0
                except (TypeError, ValueError):
                    delay_seconds = 1.0
                finally:
                    exc.close()

                time.sleep(max(0.0, min(delay_seconds, 5.0)))
            except Exception as exc:
                error_type = type(exc).__name__
                raise
            finally:
                graph_request_id = response_headers.get("request-id", "") if response_headers else ""
                # Never log arbitrary headers or a signed download URL.
                if not re.fullmatch(r"[0-9a-fA-F-]{36}", graph_request_id):
                    graph_request_id = None
                diagnostic("graph_http_attempt", level=logging.INFO if SYNC_ID.get() else logging.DEBUG,
                           method=method, attempt=attempt + 1, status_code=http_status,
                           error_type=error_type, graph_request_id=graph_request_id,
                           duration_ms=round((time.perf_counter() - started) * 1000, 2))

        raise GraphStorageError("Microsoft Graph indisponível após nova tentativa.")
```
