"""Recover an acknowledged operation without writing or resending its payload."""
import json
import pytest
from src.services.editing_state_service import EditorIdentity
from tests.test_editing_quarantine import quarantine_app, replace_stored_state, stored_evidence


@pytest.mark.parametrize('change', ['intervention_report', '_internal_observations', 'customer_email'])
@pytest.mark.parametrize('published', [False, True])
def test_partial_send_changed_content_requires_new_operation_and_preserves_latest_form(quarantine_app, monkeypatch, change, published):
    from src.services.finalization_service import FinalizationService
    from src.services.document_data_service import DocumentDataService
    app, service, document_id, path = quarantine_app
    replace_stored_state(service, document_id, app.config['SYNTHETIC_INITIAL_STATE'])
    state = json.loads(app.config['SYNTHETIC_INITIAL_STATE'])
    metadata = {'document_id':document_id, 'client_id':'tab-1',
                'lease_token':next(iter(state['sessions'])), 'base_revision':state['revision'],
                'idempotency_key':'synthetic-partial-send'}
    payload = {'customer_name':'Cliente fictício', 'intervention_report':'Texto inicial',
        '_internal_observations':'Nota privada fictícia', 'client_not_present':True,
        'service_types':{'assistencia':True}, 'equipments':{'vss':True},
        'technician_records':[{'technician':'Técnico fictício','date':'2026-10-14',
                               'start_time':'09:00','end_time':'10:00'}]}
    original = FinalizationService.publish
    def fail(self, *args):
        if published:
            original(self, *args)
        raise OSError('Synthetic interruption after prepare')
    monkeypatch.setattr(FinalizationService, 'publish', fail)
    client = app.test_client()
    scope = client.get('/api/graph/monitor-context').json['scope']
    headers = {'X-Mail-Monitor-Scope':scope}
    failed = client.post(f'/api/file/{path.stem}/send', json={**payload, '_edit':metadata}, headers=headers)
    assert failed.status_code == 500, failed.json
    receipt_url = f'/api/editing/operations/{document_id}/{metadata["idempotency_key"]}'
    receipt = client.get(receipt_url, headers=headers)
    assert receipt.status_code == 200 and receipt.json['operation']['status'] == 'failed'
    assert 'Nota privada' not in receipt.get_data(as_text=True)
    assert 'request_digest' not in receipt.get_data(as_text=True)
    assert path.exists()
    monkeypatch.setattr(FinalizationService, 'publish', original)
    reopened = client.get(f'/api/file/{path.stem}/bootstrap?client_id=tab-1')
    assert reopened.status_code == 200
    metadata.update(lease_token=reopened.json['editing']['lease']['token'],
                    base_revision=reopened.json['editing']['revision'])
    payload[change] = 'changed@example.invalid' if change == 'customer_email' else 'Alteração posterior preservada'
    rejected = client.post(f'/api/file/{path.stem}/send', json={**payload, '_edit':metadata}, headers=headers)
    assert rejected.status_code == 409 and rejected.json['code'] == 'operation_payload_changed'
    assert path.exists()
    assert service.operation(document_id, metadata['idempotency_key'])['status'] == 'failed'
    # The browser clears a confirmed failed hint; its next explicit click uses a fresh key.
    metadata['idempotency_key'] = 'synthetic-current-content'
    sent = client.post(f'/api/file/{path.stem}/send', json={**payload, '_edit':metadata}, headers=headers)
    assert sent.status_code == 200, sent.json
    operation = service.operation(document_id, metadata['idempotency_key'])
    from pathlib import Path
    archived = Path(operation['context']['archived_path'])
    if change == '_internal_observations':
        assert payload[change] in archived.with_name(archived.stem+'__observacoes_internas.txt').read_text(encoding='utf-8')
    else:
        assert DocumentDataService(archived).read()[change] == payload[change]
    assert not path.exists()
    receipt = client.get(f'/api/editing/operations/{document_id}/{metadata["idempotency_key"]}', headers=headers)
    assert receipt.status_code == 200 and receipt.json['operation']['status'] == 'complete'


@pytest.fixture
def completed(quarantine_app):
    app, service, document_id, path = quarantine_app
    replace_stored_state(service, document_id, app.config['SYNTHETIC_INITIAL_STATE'])
    state = json.loads(app.config['SYNTHETIC_INITIAL_STATE'])
    service.claim_operation(document_id=document_id, kind='send', idempotency_key='synthetic-send',
        identity=EditorIdentity('synthetic-user', 'Synthetic'), client_id='tab-1',
        lease_token=next(iter(state['sessions'])), base_revision=state['revision'])
    service.update_operation_context(document_id, 'synthetic-send',
        recovery_owner={'provider':'local','id':'synthetic-user'})
    service.commit_operation(document_id=document_id, idempotency_key='synthetic-send', path=path,
        response={'success':True,'email_status':'pending','graph_job_id':'send:synthetic',
                  'internal_observations':'PRIVATE SYNTHETIC MARKER'}, release_lease=True)
    path.unlink()  # Only this fixture; normal finalization retires its active file too.
    client = app.test_client()
    scope = client.get('/api/graph/monitor-context').json['scope']
    return client, service, document_id, scope


def test_completed_send_is_readable_after_file_retirement_without_private_fields_or_writes(completed):
    client, service, document_id, scope = completed
    before = stored_evidence(service, document_id)
    response = client.get(f'/api/editing/operations/{document_id}/synthetic-send',
                          headers={'X-Mail-Monitor-Scope':scope})
    assert response.status_code == 200
    assert response.json['operation']['status'] == 'complete'
    assert response.json['operation']['result']['graph_job_id'] == 'send:synthetic'
    assert 'PRIVATE' not in response.get_data(as_text=True)
    assert 'no-store' in response.headers['Cache-Control']
    assert stored_evidence(service, document_id) == before


@pytest.mark.parametrize('scope', [None, '', '0'*64])
def test_missing_or_wrong_session_scope_cannot_read_commit(completed, scope):
    client, service, document_id, _ = completed
    before = stored_evidence(service, document_id)
    response = client.get(f'/api/editing/operations/{document_id}/synthetic-send',
        headers={} if scope is None else {'X-Mail-Monitor-Scope':scope})
    assert response.status_code == 403
    assert 'send:synthetic' not in response.get_data(as_text=True)
    assert stored_evidence(service, document_id) == before


def test_matching_scope_does_not_grant_another_owners_operation(completed):
    client, service, document_id, scope = completed
    service.update_operation_context(document_id, 'synthetic-send',
        recovery_owner={'provider':'local','id':'another-user'})
    response = client.get(f'/api/editing/operations/{document_id}/synthetic-send',
                          headers={'X-Mail-Monitor-Scope':scope})
    assert response.status_code == 404
    assert 'send:synthetic' not in response.get_data(as_text=True)


def test_unknown_operation_lookup_does_not_create_editing_state(completed):
    client, service, _, scope = completed
    before = service.repository.document_ids()
    response = client.get('/api/editing/operations/unknown-document/unknown-operation',
                          headers={'X-Mail-Monitor-Scope':scope})
    assert response.status_code == 404
    assert service.repository.document_ids() == before
