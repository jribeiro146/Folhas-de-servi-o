# Estado Atual da WebApp

Webapp ativa:

- Backend: `src/web/application.py`
- Entrada local: `src/main.py`
- Template: `src/web/templates/field_app.html`
- JS: `src/web/static/js/field-app.js`
- CSS: `src/web/static/css/field-app.css`

Fluxos atualmente validados:

- `GET /api/files`
- `GET /api/file/<name>`
- `POST /api/file/<name>/draft`
- `POST /api/file/<name>/send`
- `POST /api/file/<name>/cancel`

Consistencia atual:

- Sem geracao de PDF na webapp.
- Porta preferida `5001`, com fallback automatico.
- Confirmacoes integradas na UI, sem `alert()` ou `window.confirm()`.
- Em servidor, a app deve correr por WSGI/Gunicorn e usar Microsoft Graph ou volume persistente.

Limpeza aplicada:

- Removidos dados locais, `.env`, Excels operacionais, pacote `release/`, `.runtime/` e `.vscode/`.
- Removidos `tools/manual/`, `docs/archive/`, documentos antigos de planeamento/build EXE e assets sem uso.
- Mantidos `src/`, `tests/`, fixtures de teste, Docker, requirements e documentacao atual de servidor/Graph/login.
