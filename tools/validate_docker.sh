#!/usr/bin/env bash
# Synthetic acceptance only. No operational env, bind mounts, ports or transports.
set -euo pipefail
if [ "$#" -ne 4 ]; then echo "Usage: $0 EXTRACTED_RELEASE NEW_EVIDENCE_DIR UID GID" >&2; exit 2; fi
[[ "$1" == /* && "$2" == /* ]] || { echo 'Use absolute release/evidence paths' >&2; exit 2; }
cd "$1"
QA_EVIDENCE="$2"
mkdir "$QA_EVIDENCE"
QA_UID="$3"; QA_GID="$4"
[[ "$QA_UID" =~ ^[1-9][0-9]*$ && "$QA_GID" =~ ^[1-9][0-9]*$ ]]
QA_REVISION=$(python3 -c 'import json; print(json.load(open("release-manifest.json"))["revision"])')
QA_SOURCE=$(python3 -c 'import json; print(json.load(open("release-manifest.json"))["source_sha256"])')
QA_RUN="fsqa-$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
QA_IMAGE="$QA_RUN:production"
QA_TEST_IMAGE="$QA_RUN:qa"
QA_VOLUME="$QA_RUN-fresh"
QA_LEGACY="$QA_RUN-legacy"
printf '%s\n' "$QA_RUN" > "$QA_EVIDENCE/resources-prefix.txt"
printf '%s\n' "$QA_REVISION" > "$QA_EVIDENCE/commit.txt"
docker version > "$QA_EVIDENCE/docker-version.txt"
docker compose version > "$QA_EVIDENCE/compose-version.txt"
uname -a > "$QA_EVIDENCE/kernel.txt"
docker build --build-arg "APP_UID=$QA_UID" --build-arg "APP_GID=$QA_GID" --target production -t "$QA_IMAGE" .
docker build --build-arg "APP_UID=$QA_UID" --build-arg "APP_GID=$QA_GID" --target qa -t "$QA_TEST_IMAGE" .
docker image inspect "$QA_IMAGE" --format '{{.Id}}' | tee "$QA_EVIDENCE/image.txt"
QA_ACTUAL=$(docker run --rm --network none "$QA_IMAGE" sh -c 'printf "%s:%s" "$(id -u)" "$(id -g)"')
printf '%s\n' "$QA_ACTUAL" | tee "$QA_EVIDENCE/identity.txt"
test "$QA_ACTUAL" = "$QA_UID:$QA_GID"
docker run --rm --network none -e FS_EXPECTED_SOURCE="$QA_SOURCE" "$QA_IMAGE" python -B -c 'import json,os; from src.services.release_identity import get_release_info; r=get_release_info(); assert r["verified"] and r["source_sha256"]==os.environ["FS_EXPECTED_SOURCE"]; print(json.dumps(r))' | tee "$QA_EVIDENCE/release.json"

docker run --rm --network none --mount "type=volume,src=$QA_VOLUME,dst=/app/data" "$QA_IMAGE" python -B -c 'from pathlib import Path; assert list(Path("/app/data").iterdir())==[]'
docker run --rm --network none --mount "type=volume,src=$QA_VOLUME,dst=/app/data" "$QA_IMAGE" python -B tools/prepare_runtime.py init --data-dir /app/data | tee "$QA_EVIDENCE/init.json"

# Não usar configure(): criaria outra pasta e deixaria de testar este volume.
docker run -i --rm --network none --mount "type=volume,src=$QA_VOLUME,dst=/app/data" \
  -e FS_ENVIRONMENT=production -e FS_ENV_FILE=/tmp/fsqa-absent.env \
  -e FS_AUTH_PROVIDER=none -e FS_STORAGE_BACKEND=local \
  -e FS_SECRET_KEY=synthetic-container-key-not-for-production-2026 \
  -e FS_MAIL_ENABLED=false -e FS_TEAMS_NOTIFICATIONS_ENABLED=false \
  -e FS_GRAPH_QUEUE_IN_WEB=false -e FS_LOG_ENABLED=false \
  -e FS_EXCEL_ROOT=/app/data/Excel -e FS_EXPECTED_SOURCE="$QA_SOURCE" \
  "$QA_IMAGE" python -B - <<'PY' | tee "$QA_EVIDENCE/factories.json"
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

docker run --rm --network none --mount "type=volume,src=$QA_LEGACY,dst=/app/data" "$QA_IMAGE" python -B -c 'from pathlib import Path; p=Path("/app/data"); assert not list(p.iterdir()); (p/"legacy.txt").write_bytes(b"synthetic legacy evidence")'
if docker run --rm --network none --mount "type=volume,src=$QA_LEGACY,dst=/app/data" "$QA_IMAGE" python -B tools/prepare_runtime.py init --data-dir /app/data 2>"$QA_EVIDENCE/legacy-error.json"; then
  echo 'ERRO: init aceitou dados legados'; exit 1
else
  QA_EXIT=$?; test "$QA_EXIT" -eq 2
fi
docker run --rm --network none --mount "type=volume,src=$QA_LEGACY,dst=/app/data" "$QA_IMAGE" python -B -c 'from pathlib import Path; p=Path("/app/data"); assert [f.name for f in p.iterdir()]==["legacy.txt"]; assert (p/"legacy.txt").read_bytes()==b"synthetic legacy evidence"'

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
  --workspace /maintenance/m06 --writers-stopped --roots-complete | tee "$QA_EVIDENCE/migrate-prepare.json"
qa_offline tools/migrate_runtime.py apply --workspace /maintenance/m06 \
  --writers-stopped | tee "$QA_EVIDENCE/migrate-apply.json"
qa_offline tools/prepare_runtime.py inspect --data-dir /app/data | tee "$QA_EVIDENCE/migrate-inspect.json"
qa_offline -c 'from pathlib import Path; assert (Path("/app/data")/"legacy.txt").read_bytes()==b"synthetic legacy evidence"'

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

# Backup/restore uses another NEW volume at the same absolute data path.
# The original migrated volume and all evidence remain untouched.
QA_KEYS="$QA_RUN-keys"
QA_RESTORED="$QA_RUN-restored"
docker run --rm --network none --user 0 --mount "type=volume,src=$QA_KEYS,dst=/qa-keys" "$QA_IMAGE" python -B -c 'import os,pwd; from pathlib import Path; p=Path("/qa-keys"); assert not list(p.iterdir()); u=pwd.getpwnam("appuser"); os.chown(p,u.pw_uid,u.pw_gid); p.chmod(0o700)'
docker run --rm --network none --mount "type=volume,src=$QA_KEYS,dst=/qa-keys" "$QA_IMAGE" python -B -c 'import secrets; from pathlib import Path; p=Path("/qa-keys/synthetic.key"); p.write_text(secrets.token_hex(32)); p.chmod(0o600)'
qa_backup() {
  docker run --rm --network none \
    --mount "type=volume,src=$QA_TARGET,dst=/app/data" \
    --mount "type=volume,src=$QA_WORK,dst=/maintenance" \
    --mount "type=volume,src=$QA_KEYS,dst=/qa-keys,readonly" \
    "$QA_IMAGE" python -B tools/backup_runtime.py "$@" --key-file /qa-keys/synthetic.key
}
QA_TARGET="$QA_LEGACY"
qa_backup backup --data-dir /app/data --backup /maintenance/backup --writers-stopped --roots-complete | tee "$QA_EVIDENCE/backup.json"
qa_backup verify --backup /maintenance/backup | tee "$QA_EVIDENCE/backup-verify.json"
qa_offline tools/prepare_runtime.py activate --data-dir /app/data --expected-source "$QA_SOURCE" --writers-stopped | tee "$QA_EVIDENCE/activate.json"
QA_TARGET="$QA_RESTORED"
docker run --rm --network none --mount "type=volume,src=$QA_TARGET,dst=/app/data" "$QA_IMAGE" python -B -c 'from pathlib import Path; assert not list(Path("/app/data").iterdir())'
qa_backup restore --backup /maintenance/backup --workspace /maintenance/restore --writers-stopped | tee "$QA_EVIDENCE/restore.json"
# No real writers or external effects ever existed in these synthetic volumes.
qa_backup release --workspace /maintenance/restore --writers-stopped --actor synthetic-ci --reason "Synthetic offline acceptance; no external effects" --external-effects-reconciled | tee "$QA_EVIDENCE/restore-release.json"
docker run --rm --network none --mount "type=volume,src=$QA_TARGET,dst=/app/data" "$QA_IMAGE" python -B -c 'from pathlib import Path; assert (Path("/app/data")/"legacy.txt").read_bytes()==b"synthetic legacy evidence"'
python3 - "$QA_EVIDENCE" <<'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
assert json.loads((p / 'backup-verify.json').read_text())['state'] == 'verified'
assert json.loads((p / 'restore.json').read_text())['state'] == 'held'
assert json.loads((p / 'restore-release.json').read_text())['state'] == 'released'
print('PASS: offline CLI backup/verify/activate/restore/release')
PY

# Keep JUnit even on pytest failure, without a writable bind of the host checkout.
set +e
docker run --name "$QA_RUN-tests" --network none "$QA_TEST_IMAGE" python -B -m pytest tests -q -ra -p no:cacheprovider --tb=short --junitxml=/tmp/pytest.xml | tee "$QA_EVIDENCE/pytest.txt"
QA_TEST_EXIT=$?
set -e
docker cp "$QA_RUN-tests:/tmp/pytest.xml" "$QA_EVIDENCE/pytest.xml"
docker rm "$QA_RUN-tests"
test "$QA_TEST_EXIT" -eq 0

docker run --rm --network none -e FS_PDF_BROWSER_NO_SANDBOX=true "$QA_IMAGE" python -B tools/smoke_pdf.py | tee "$QA_EVIDENCE/pdf.json"

# Normalize only a newly written synthetic env; never use the checkout's .env.
QA_COMPOSE=$(mktemp -d)
cp docker-compose.yml "$QA_COMPOSE/docker-compose.yml"
mkdir "$QA_COMPOSE/logs"
printf 'FS_IMAGE=%s\nFS_LOG_HOST_DIR=%s\n' "$QA_IMAGE" "$QA_COMPOSE/logs" > "$QA_COMPOSE/.env"
docker compose --env-file "$QA_COMPOSE/.env" -f "$QA_COMPOSE/docker-compose.yml" -p "$QA_RUN" --profile maintenance config --format json | python3 tools/check_compose.py --expected-image "$QA_IMAGE" | tee "$QA_EVIDENCE/compose-check.json"
# Prove that normalized true is still refused on each actual Compose version.
python3 - "$QA_COMPOSE/docker-compose.yml" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text()
assert 'create_host_path: false' in s
p.write_text(s.replace('create_host_path: false', 'create_host_path: true'))
PY
# Separate normalization from the expected refusal: a Compose failure is not a
# successful negative test. This temporary JSON contains only synthetic values.
docker compose --env-file "$QA_COMPOSE/.env" -f "$QA_COMPOSE/docker-compose.yml" -p "$QA_RUN" --profile maintenance config --format json > "$QA_COMPOSE/unsafe.json"
if python3 tools/check_compose.py --expected-image "$QA_IMAGE" < "$QA_COMPOSE/unsafe.json"; then
  echo 'ERRO: bind inseguro foi aceite'; exit 1
else
  test "$?" -eq 2
fi
printf 'PASS\n' | tee "$QA_EVIDENCE/acceptance.txt"
# No prune/down/delete of volumes. A GitHub-hosted runner is discarded after its job.
# On a manual host, resources-prefix.txt identifies these synthetic volumes for review.
