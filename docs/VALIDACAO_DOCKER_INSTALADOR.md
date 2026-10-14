# Nota ao instalador — ensaio Linux/Docker M14

**Execução e resultados:** consultar [o registo CI](VALIDACAO_CI.md).
Este roteiro mantém os passos reproduzíveis para o instalador. Executar estes critérios com dados fictícios e
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

## 3b. Migração sintética no mesmo caminho

Continuar sobre QA_LEGACY, nunca sobre um volume operacional. O código está em
/opt/folhas e o volume mantém /app/data. Um workspace novo e separado guarda o
plano e as cópias. O único uso de root aqui prepara **a raiz vazia desse volume
fictício**, sem alterar dados ou donos de ficheiros existentes.

~~~bash
QA_WORK="$QA_RUN-migration"
docker run --rm --network none --user 0 \
  --mount "type=volume,src=$QA_WORK,dst=/maintenance" "$QA_IMAGE" python -B -c 'import os,pwd; from pathlib import Path; p=Path("/maintenance"); assert not list(p.iterdir()); u=pwd.getpwnam("appuser"); os.chown(p,u.pw_uid,u.pw_gid); p.chmod(0o700)'
qa_offline() {
  docker run --rm --network none \
    --mount "type=volume,src=$QA_LEGACY,dst=/app/data" \
    --mount "type=volume,src=$QA_WORK,dst=/maintenance" \
    "$QA_IMAGE" python -B "$@"
}
# Cópia independente ANTES do M06; este volume sintético só contém legacy.txt.
qa_offline -c 'from pathlib import Path; import shutil; p=Path("/app/data"); assert {f.name for f in p.iterdir()}=={"legacy.txt"}; shutil.copytree(p, "/maintenance/pre-m06")'
qa_offline tools/migrate_runtime.py prepare --data-dir /app/data \
  --workspace /maintenance/m06 --writers-stopped --roots-complete | tee "evidence-$QA_RUN/migrate-prepare.json"
qa_offline tools/migrate_runtime.py apply --workspace /maintenance/m06 \
  --writers-stopped | tee "evidence-$QA_RUN/migrate-apply.json"
qa_offline tools/prepare_runtime.py inspect --data-dir /app/data | tee "evidence-$QA_RUN/migrate-inspect.json"
qa_offline -c 'from pathlib import Path; assert (Path("/app/data")/"legacy.txt").read_bytes()==b"synthetic legacy evidence"'
~~~

Esperado: prepared, complete e inspect com matches_release=true/epoch=1; bytes
legados preservados, plano fora do artefacto/dados. As fixtures da bateria cobrem
os formatos legados de edição/fila, WAL e interrupções; este exemplo testa a
montagem e o CLI da imagem. Para backup/verify/activate/restauro, o
[guia Docker](10_ALOJAMENTO_DOCKER.md#ferramentas-offline-dentro-do-contentor)
inclui os wrappers, chave separada só de leitura e seleção da imagem correta.
Adaptar esses comandos exclusivamente a volumes/chave fictícios durante ensaios;
os testes locais não autorizam operações no servidor.

## 3c. Reposição sintética do conjunto pré-M06

Este ensaio usa a cópia independente guardada **antes** do prepare na secção 3b.
O volume migrado QA_LEGACY fica intacto. Monta-se um quarto volume fictício vazio
no mesmo destino /app/data, só para verificar a reposição do conjunto original.
Não se executa a aplicação ou imagem legada: não há operações externas a reconciliar.

~~~bash
QA_ROLLBACK="$QA_RUN-rollback"
docker run -i --rm --network none \
  --mount "type=volume,src=$QA_ROLLBACK,dst=/app/data" \
  --mount "type=volume,src=$QA_WORK,dst=/maintenance,readonly" \
  "$QA_IMAGE" python -B - <<'PY'
from pathlib import Path
import shutil
original = Path('/maintenance/pre-m06')
data = Path('/app/data')
assert not list(data.iterdir())
# O conjunto original deste ensaio contém exatamente um ficheiro fictício.
assert {p.name for p in original.iterdir()} == {'legacy.txt'}
assert (original / 'legacy.txt').read_bytes() == b'synthetic legacy evidence'
shutil.copytree(original, data, dirs_exist_ok=True)
assert {p.name for p in data.iterdir()} == {'legacy.txt'}
assert (data / 'legacy.txt').read_bytes() == b'synthetic legacy evidence'
assert not (data / 'runtime-contract.sqlite3').exists()
assert not list(data.rglob('*.sqlite3'))
print('PASS: conjunto pré-M06 reposto no mesmo destino; volume migrado preservado')
PY
~~~

Esperado: PASS/exit 0, bytes originais e ausência de contrato/bases convertidas.
É uma verificação sintética de cópia e montagem, não um ensaio do procedimento de
backup escolhido pelo profissional para produção. A
[reversão legada real](10_ALOJAMENTO_DOCKER.md#reverter-uma-instalação-legada-após-m06)
exige preservar todo o estado posterior, recuperar o conjunto completo anterior
com imagem/configuração/código correspondentes e reconciliar efeitos externos
antes de reabrir qualquer escritor. Sem essa prova, manter parado.

## 4. Ferramenta adulterada detetada

O root abaixo só altera a camada descartável deste container, sem volumes.
O arranque e os comandos de manutenção usam appuser; só a preparação do workspace
vazio na secção 3b também exige root.

~~~bash
docker run -i --rm --network none --user 0 "$QA_IMAGE" python -B - <<'PY'
from pathlib import Path
from src.services.release_identity import get_release_info, ReleaseIdentityError
path = Path('/opt/folhas/tools/prepare_runtime.py')
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

Os testes de manifesto ausente criam uma raiz sintética explícita: não dependem
de o checkout ou a imagem de testes terem manifesto. O código da imagem é de
root, só de leitura para appuser; os testes escrevem em temporários próprios.

Esperado: zero testes falhados; casos POSIX de migração/restauro/SADI efetivamente
executados. Explicar os skips específicos de Windows. PDF: status=ok, duas páginas
legíveis, UID/GID corretos. Sandbox desligado é explícito neste ensaio; não mudar
a política de produção sem decisão do instalador. O target qa acrescenta
dependências de teste à mesma camada runtime
([multi-stage Docker](https://docs.docker.com/build/building/multi-stage/)).

## 6. Compose e aceitação

Usar apenas .env fictício; este comando não cria containers nem volumes.
A configuração expandida é enviada diretamente ao verificador, que emite
somente os campos não sensíveis indicados. Não guardar a configuração real.

~~~bash
QA_COMPOSE=$(mktemp -d)
cp docker-compose.yml "$QA_COMPOSE/docker-compose.yml"
mkdir "$QA_COMPOSE/logs"
printf 'FS_IMAGE=%s\nFS_LOG_HOST_DIR=%s\n' "$QA_IMAGE" "$QA_COMPOSE/logs" > "$QA_COMPOSE/.env"
docker compose --env-file "$QA_COMPOSE/.env" -f "$QA_COMPOSE/docker-compose.yml" \
  -p "$QA_RUN" --profile maintenance config --format json \
  | python3 tools/check_compose.py --expected-image "$QA_IMAGE" --compose-version "$(docker compose version --short)" | tee "evidence-$QA_RUN/compose-check.json"
~~~

Esperado: PASS, mesma imagem em todos os serviços, destinos /app/data e /app/logs
(só data em prepare), network_mode=none e create_host_path=false. O verificador aceita false explícito e false omitido no JSON normalizado do
Compose 2.27.0 identificado pelo próprio comando. Nas restantes versões exige
false explícito; true, ausência ou valores de outro tipo são recusados. Não é um ensaio
de arranque Compose. Não executar up/run aqui: os nomes de containers de produção
são fixos. O ensaio de execução acima usa apenas docker run e volumes QA únicos.

A tabela seguinte é um formulário em branco para uma **nova execução do instalador**, não o estado dos ensaios GitHub já concluídos. Resultados anteriores e seus SHAs estão em [VALIDACAO_CI.md](VALIDACAO_CI.md).

| Verificação | Resultado desta nova execução |
|---|---|
| Commit / ZIP SHA256 / source_sha256 | Pendente |
| Docker / Compose / arquitetura | Pendente |
| ID da imagem / UID / GID | Pendente |
| Manifesto / volume vazio / init | Pendente |
| Web e worker / identidade / HTTP | Pendente |
| Legado recusado / bytes preservados | Pendente |
| Migração prepare/apply no volume / caminho preservado | Pendente |
| Reposição pré-M06 no mesmo destino / conjunto migrado preservado | Pendente |
| Adulteração recusada | Pendente |
| Pytest / skips explicados / POSIX | Pendente |
| PDF real / páginas / sandbox | Pendente |
| Compose / montagens / logs | Pendente |

Preservar evidências e os quatro volumes fictícios identificados por QA_RUN.
Limpeza posterior é deliberada e apenas desses recursos. Uma falha suspende a
aceitação Linux; não apagar filas, relaxar proteções ou repetir operações reais
para obter resultado verde.
