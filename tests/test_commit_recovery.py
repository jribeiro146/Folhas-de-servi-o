"""Recover an acknowledged operation without writing or resending its payload."""
import json
import pytest
from src.services.editing_state_service import EditorIdentity
from tests.test_editing_quarantine import quarantine_app, replace_stored_state, stored_evidence


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
