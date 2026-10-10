# Nota ao instalador — ensaio Linux/Docker M14

**Pendente de execução.** Não há resultado real de build ou execução Linux.
João está a obter um ambiente. Executar estes critérios com dados fictícios e
registar saídas/falhas sem segredos. Testes Windows e revisão estática não
substituem esta validação.

Usar Bash, Docker Engine/Compose v2 e Python 3 numa máquina de testes isolada.
A pasta atual deve ser o ZIP extraído do commit entregue, com SHA256 conferido.
Builds precisam de rede para dependências; os containers de teste ficam sem rede,
sem .env operacional, sem credenciais e sem volumes reais. Não copiar dados Plesk.

## 1. Imagem e identidade

~~~bash
set -euo pipefail
QA_REVISION=$(python3 -c 'import json; print(json.load(open("release-manifest.json"))["revision"])')
QA_SOURCE=$(python3 -c 'import json; print(json.load(open("release-manifest.json"))["source_sha256"])')
QA_IMAGE="folhas-servico-qa:$QA_REVISION"
QA_TEST_IMAGE="folhas-servico-tests:$QA_REVISION"
QA_RUN="fsqa-$(date -u +%Y%m%dT%H%M%S)-$$"
QA_VOLUME="$QA_RUN-fresh"
QA_LEGACY="$QA_RUN-legacy"
mkdir -p "evidence-$QA_RUN"
docker build --target production -t "$QA_IMAGE" .
docker build --target qa -t "$QA_TEST_IMAGE" .
docker image inspect "$QA_IMAGE" --format '{{.Id}}' | tee "evidence-$QA_RUN/image.txt"
docker run --rm --network none "$QA_IMAGE" id
docker run --rm --network none -e FS_EXPECTED_SOURCE="$QA_SOURCE" "$QA_IMAGE" python -B -c 'import json,os; from src.services.release_identity import get_release_info; r=get_release_info(); assert r["verified"] and r["source_sha256"]==os.environ["FS_EXPECTED_SOURCE"]; print(json.dumps(r))' | tee "evidence-$QA_RUN/release.json"
~~~

Esperado: build sem erro, UID/GID 10001:10001, verified=true, SHA igual ao
manifesto. Repetir com tags próprias e argumentos de build APP_UID=100/APP_GID=101
se esse for o destino confirmado; esperar 100:101. Registar ID de cada imagem:
a identidade do código não inclui argumentos de build ou dependências instaladas.

## 2. Volume novo, preparação e factories

~~~bash
docker run --rm --network none --mount "type=volume,src=$QA_VOLUME,dst=/app/data" "$QA_IMAGE" python -B -c 'from pathlib import Path; assert list(Path("/app/data").iterdir())==[]'
docker run --rm --network none --mount "type=volume,src=$QA_VOLUME,dst=/app/data" "$QA_IMAGE" python -B tools/prepare_runtime.py init --data-dir /app/data | tee "evidence-$QA_RUN/init.json"

# Não usar configure(): criaria outra pasta e deixaria de testar este volume.
docker run -i --rm --network none --mount "type=volume,src=$QA_VOLUME,dst=/app/data" \
  -e FS_ENVIRONMENT=production -e FS_ENV_FILE=/tmp/fsqa-absent.env \
  -e FS_AUTH_PROVIDER=none -e FS_STORAGE_BACKEND=local \
  -e FS_SECRET_KEY=synthetic-container-key-not-for-production-2026 \
  -e FS_MAIL_ENABLED=false -e FS_TEAMS_NOTIFICATIONS_ENABLED=false \
  -e FS_GRAPH_QUEUE_IN_WEB=false -e FS_LOG_ENABLED=false \
  -e FS_EXCEL_ROOT=/app/data/Excel -e FS_EXPECTED_SOURCE="$QA_SOURCE" \
  "$QA_IMAGE" python -B - <<'PY' | tee "evidence-$QA_RUN/factories.json"
import json, os
from tools.test_runtime import block_outbound_network
block_outbound_network()
from src.web.application import create_app
from src.queue_worker import build_queue
response = create_app().test_client().get('/static/css/field-app.css')
queue = build_queue()
assert response.status_code == 200
assert response.headers['X-App-Version'] == queue.release_info['source_sha256'] == os.environ['FS_EXPECTED_SOURCE']
assert queue.release_info['verified']
print(json.dumps({'http': response.status_code, 'release': queue.release_info}))
PY
~~~

Esperado: init exit 0, command=init/epoch=1; factories HTTP 200 e mesma revisão/SHA.
Sem transporte externo. Isto verifica construção/HTTP interno, não login Microsoft
nem conectividade do servidor. A bateria abaixo cobre recusa sem contrato.

## 3. Volume legado recusado, sem perda

~~~bash
docker run --rm --network none --mount "type=volume,src=$QA_LEGACY,dst=/app/data" "$QA_IMAGE" python -B -c 'from pathlib import Path; p=Path("/app/data"); assert not list(p.iterdir()); (p/"legacy.txt").write_bytes(b"synthetic legacy evidence")'
if docker run --rm --network none --mount "type=volume,src=$QA_LEGACY,dst=/app/data" "$QA_IMAGE" python -B tools/prepare_runtime.py init --data-dir /app/data 2>"evidence-$QA_RUN/legacy-error.json"; then
  echo 'ERRO: init aceitou dados legados'; exit 1
else
  QA_EXIT=$?; test "$QA_EXIT" -eq 2
fi
docker run --rm --network none --mount "type=volume,src=$QA_LEGACY,dst=/app/data" "$QA_IMAGE" python -B -c 'from pathlib import Path; p=Path("/app/data"); assert [f.name for f in p.iterdir()]==["legacy.txt"]; assert (p/"legacy.txt").read_bytes()==b"synthetic legacy evidence"'
~~~

Esperado: exit 2/erro JSON, bytes preservados, nenhum contrato criado.
Um volume real legado exige M06; não usar init para o adotar.

## 4. Ferramenta adulterada detetada

O root abaixo só altera a camada descartável deste container, sem volumes.
O arranque normal e todos os outros ensaios usam appuser.

~~~bash
docker run -i --rm --network none --user 0 "$QA_IMAGE" python -B - <<'PY'
from pathlib import Path
from src.services.release_identity import get_release_info, ReleaseIdentityError
path = Path('/app/tools/prepare_runtime.py')
path.write_bytes(path.read_bytes() + b'\n# synthetic tamper\n')
try:
    get_release_info()
except ReleaseIdentityError:
    print('PASS: ferramenta alterada recusada')
else:
    raise AssertionError('Ferramenta adulterada foi aceite')
assert not list(Path('/app/data').iterdir())
PY
~~~

Esperado: PASS e exit 0 do ensaio; não regenerar manifesto. Os testes de identidade
cobrem também código src alterado/ausente e manifesto em falta.

## 5. Bateria e PDF, sem rede

~~~bash
docker run --rm --network none "$QA_TEST_IMAGE" | tee "evidence-$QA_RUN/pytest.txt"
docker run --rm --network none -e FS_PDF_BROWSER_NO_SANDBOX=true "$QA_IMAGE" python -B tools/smoke_pdf.py | tee "evidence-$QA_RUN/pdf.json"
~~~

Esperado: zero testes falhados; casos POSIX de migração/restauro/SADI efetivamente
executados. Explicar os skips específicos de Windows. PDF: status=ok, duas páginas
legíveis, UID/GID corretos. Sandbox desligado é explícito neste ensaio; não mudar
a política de produção sem decisão do instalador. O target qa acrescenta
dependências de teste à mesma camada runtime
([multi-stage Docker](https://docs.docker.com/build/building/multi-stage/)).

## 6. Compose e aceitação

Conferir docker compose config --quiet numa cópia isolada com .env **fictício**,
FS_IMAGE apontando à imagem construída e FS_LOG_HOST_DIR a pasta privada de teste
existente/gravável pelo UID/GID escolhido. Não guardar a configuração real expandida.
Confirmar mesma imagem app/worker, nenhuma montagem src/tools, prepare explícito
e sem rede, logs sem criação silenciosa como root. run --rm --no-deps prepare só
para volume vazio e projeto de teste escolhido. Nunca usar projeto/volume real
neste ensaio nem executar down -v.

| Verificação | Resultado real a preencher |
|---|---|
| Commit / ZIP SHA256 / source_sha256 | Pendente |
| Docker / Compose / arquitetura | Pendente |
| ID da imagem / UID / GID | Pendente |
| Manifesto / volume vazio / init | Pendente |
| Web e worker / identidade / HTTP | Pendente |
| Legado recusado / bytes preservados | Pendente |
| Adulteração recusada | Pendente |
| Pytest / skips explicados / POSIX | Pendente |
| PDF real / páginas / sandbox | Pendente |
| Compose / montagens / logs | Pendente |

Preservar evidências e os dois volumes fictícios identificados por QA_RUN.
Limpeza posterior é deliberada e apenas desses recursos. Uma falha suspende a
aceitação Linux; não apagar filas, relaxar proteções ou repetir operações reais
para obter resultado verde.
