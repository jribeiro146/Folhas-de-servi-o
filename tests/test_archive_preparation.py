"""An archive upload failure is not evidence that mail may have been sent."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from src.services import admin_access
from src.services.editing_state_service import EditingStateService
from src.services.graph_sync_queue import GraphSyncQueue
import src.web.application as web
from tests.fixed_mail_fake import FixedMailFake
from tests.test_durable_mail import Graph
from tests.test_operational_admin import ADMIN, login


class UploadGraph(Graph):
    def __init__(self, root, *, crash=False):
        super().__init__(root)
        self.fail = True
        self.crash_upload = crash
        self.uploads = 0

    def upload_archive_bundle(self, path):
        self.uploads += 1
        if self.crash_upload:
            os._exit(72)
        if self.fail:
            raise ConnectionError('Synthetic archive upload unavailable')
        return super().upload_archive_bundle(path)


class Mail(FixedMailFake):
    def __init__(self):
        super().__init__()
        self.calls = []

    def send_delivery(self, delivery, *, request_id=None):
        self.validate_delivery(delivery)
        self.calls.append(delivery)
        return {'accepted': True}


def make_queue(root, *, crash=False):
    return GraphSyncQueue(UploadGraph(root, crash=crash), root / 'queue.sqlite3',
                          mail_service=Mail(), auto_start=False)


def enqueue(queue, root):
    return queue.enqueue('archive_and_remove', {'archived_path':str(root / 'synthetic.xlsx'),
        'source_name':'synthetic.xlsx', 'expected_etag':'synthetic-v1',
        'mail':{'to':'customer@example.invalid', 'subject':'[TESTE] Preparação fictícia'}},
        job_id='send:synthetic-preparation')['id']


@pytest.fixture
def admin_client(monkeypatch, tmp_path):
    monkeypatch.setattr(admin_access, 'ADMIN_TENANT_ID', ADMIN.tenant_id)
    monkeypatch.setattr(admin_access, 'ADMIN_OBJECT_ID', ADMIN.id)
    monkeypatch.setattr(web, 'AUTH_ENABLED', True)
    monkeypatch.setattr(web, 'ACTIVE_AUTH_PROVIDER', 'microsoft')
    class Auth:
        def user_from_session(self, payload):
            return SimpleNamespace(**payload) if payload else None
    def create(queue):
        app = web.create_app(graph_sync_queue=queue,
            editing_state_service=EditingStateService(tmp_path / 'editing'), microsoft_auth_service=Auth())
        return login(app, ADMIN.email)
    return create


def assert_retryable_preparation(queue, client, job):
    overview = client.get('/api/admin/overview')
    assert overview.status_code == 200
    projected = next(row for row in overview.json['jobs'] if row['id'] == job)
    assert projected['can_retry'] is True
    assert projected['reconciliation_required'] is False
    assert projected['phase'] == 'uploading_archive'
    assert queue.reconciliation(job, actor=ADMIN)['allowed_decisions'] == []
    public = client.get('/api/graph/jobs/' + job).json['job']
    assert public['result']['phase'] == 'uploading_archive'
    assert public['reconciliation_required'] is False


def resume_once(queue, client, job, root):
    queue.graph_service.fail = False
    response = client.post('/api/admin/jobs/' + job + '/retry',
        json={'reason':'Indisponibilidade sintética resolvida'}, headers={'X-Admin-CSRF':'synthetic-csrf'})
    assert response.status_code == 202
    queue.run_until_idle()
    assert queue.status(job)['status'] == 'complete'
    assert len(queue.mail_service.calls) == 1 and (root / 'remote-removed').is_file()
    queue.run_until_idle()
    assert len(queue.mail_service.calls) == 1


def test_exhausted_upload_can_be_retried_without_uncertain_mail(tmp_path, admin_client):
    queue = make_queue(tmp_path)
    queue.retry_base_seconds = 100  # Advance only this synthetic queue explicitly.
    job = enqueue(queue, tmp_path)
    for _ in range(3):
        with queue._connect() as db:
            db.execute('UPDATE graph_sync_jobs SET next_attempt_at=0 WHERE id=?', (job,))
        queue.run_until_idle()
    assert queue.status(job)['attempts'] == 3 and not queue.status(job)['will_retry']
    assert queue.graph_service.uploads == 3 and queue.mail_service.calls == []
    assert not (tmp_path / 'remote-removed').exists()
    client = admin_client(queue)
    assert_retryable_preparation(queue, client, job)
    resume_once(queue, client, job, tmp_path)


def test_process_death_during_upload_preserves_pre_send_checkpoint(tmp_path, admin_client):
    queue = make_queue(tmp_path)
    job = enqueue(queue, tmp_path)
    script = '''
from tools.test_runtime import configure, block_outbound_network
configure()
block_outbound_network()
from tests.test_archive_preparation import make_queue
from pathlib import Path
import sys
make_queue(Path(sys.argv[1]), crash=True).run_until_idle()
'''
    child = subprocess.run([sys.executable, '-B', '-c', script, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=30)
    assert child.returncode == 72, child.stderr.decode('utf-8', errors='replace')
    with queue._connect() as db:
        db.execute('UPDATE graph_sync_jobs SET updated_at=0 WHERE id=?', (job,))
    assert queue.status(job)['status'] == 'failed'
    client = admin_client(queue)
    assert_retryable_preparation(queue, client, job)
    resume_once(queue, client, job, tmp_path)


def test_checkpoint_failure_prevents_upload_and_mail(tmp_path, monkeypatch):
    queue = make_queue(tmp_path)
    job = enqueue(queue, tmp_path)
    def cannot_persist(*_args):
        raise OSError('Synthetic checkpoint unavailable')
    monkeypatch.setattr(queue, '_checkpoint_result', cannot_persist)
    queue.run_until_idle()
    assert queue.graph_service.uploads == 0 and queue.mail_service.calls == []
    assert queue.status(job)['status'] == 'failed'


@pytest.mark.parametrize('phase', [None, 'sending_mail', 'mail_unknown'])
def test_existing_uncertain_or_unphased_legacy_is_not_reclassified(tmp_path, admin_client, phase):
    queue = make_queue(tmp_path)
    job = enqueue(queue, tmp_path)
    result = {'phase':phase} if phase else {}
    with queue._connect() as db:
        db.execute("UPDATE graph_sync_jobs SET status='failed',attempts=3,retryable=0,result_json=? WHERE id=?",
                   (json.dumps(result), job))
    before = queue.status(job)
    client = admin_client(queue)
    queue.run_until_idle()
    assert not client.get('/api/admin/overview').json['jobs'][0]['can_retry']
    assert 'indeterminate' in queue.reconciliation(job, actor=ADMIN)['allowed_decisions']
    for prefix in ('/api/admin/jobs/', '/api/graph/jobs/'):
        response = client.post(prefix + job + '/retry', json={'reason':'Tentativa sintética recusada'},
            headers={'X-Admin-CSRF':'synthetic-csrf'})
        assert response.status_code == 409
    assert queue.status(job) == before
    assert queue.graph_service.uploads == 0 and queue.mail_service.calls == []
