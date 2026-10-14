"""A known failed draft write must not masquerade as an external replacement."""
import errno
import json
import pytest

from src.services.document_data_service import DocumentDataService
from tests.test_editing_quarantine import quarantine_app, replace_stored_state


def test_partial_draft_then_reload_keeps_autosave_but_later_external_change_invalidates(quarantine_app, monkeypatch):
    app, service, document_id, path = quarantine_app
    raw = app.config['SYNTHETIC_INITIAL_STATE']
    replace_stored_state(service, document_id, raw)
    initial = json.loads(raw)
    metadata = {'document_id':document_id,'client_id':'tab-1',
                'lease_token':next(iter(initial['sessions'])),'base_revision':initial['revision'],
                'idempotency_key':'synthetic-autosave-before-disk-full'}
    client = app.test_client()
    payload = {'customer_name':'Cliente fictício', 'intervention_report':'Trabalho já guardado no servidor'}
    saved = client.post(f'/api/file/{path.stem}/autosave', json={'_edit':metadata,'document':payload})
    assert saved.status_code == 200
    metadata.update(base_revision=saved.json['editing']['revision'], idempotency_key='synthetic-partial-draft')
    original_write = DocumentDataService.write
    prior = DocumentDataService(path).read()
    def full_disk(*args):
        raise OSError(errno.ENOSPC, 'Synthetic disk full at the sidecar boundary')
    monkeypatch.setattr(DocumentDataService, 'write', full_disk)
    failed = client.post(f'/api/file/{path.stem}/draft', json={**payload, '_edit':metadata})
    assert failed.status_code == 500
    assert DocumentDataService(path).read() == prior
    assert service.operation(document_id, metadata['idempotency_key'])['status'] == 'failed'
    reopened = client.get(f'/api/file/{path.stem}/bootstrap?client_id=tab-1')
    assert reopened.status_code == 200
    assert (reopened.json['editing']['server_document'] or {}).get('intervention_report') == payload['intervention_report']
    assert reopened.json['editing']['revision'] == saved.json['editing']['revision']

    # A different write AFTER the failed operation remains an external change.
    monkeypatch.setattr(DocumentDataService, 'write', original_write)
    DocumentDataService(path).write({'intervention_report':'Alteração externa posterior e distinta'})
    external = client.get(f'/api/file/{path.stem}/bootstrap?client_id=tab-1')
    assert external.status_code == 200
    assert external.json['editing']['server_document'] is None
    assert external.json['editing']['revision'] > reopened.json['editing']['revision']


@pytest.mark.parametrize('kind,other_path', [('send',False), ('draft',True)])
def test_partial_write_acknowledgment_rejects_other_operation_or_document(tmp_path, kind, other_path):
    from src.services.editing_state_service import EditingStateService, EditorIdentity, EditingStateError
    file = tmp_path / 'synthetic.xlsx'
    file.write_bytes(b'synthetic')
    other = tmp_path / 'other.xlsx'
    other.write_bytes(b'other synthetic')
    service = EditingStateService(tmp_path / 'editing')
    identity = EditorIdentity('synthetic-owner', 'Técnico fictício')
    lease = service.acquire_lease(file, identity, 'tab')
    document_id = lease['document_id']
    with service.operation_guard(document_id, identity):
        service.claim_operation(document_id=document_id, kind=kind, idempotency_key='operation',
            identity=identity, client_id='tab', lease_token=lease['lease']['token'], base_revision=lease['revision'])
        before = service.operation(document_id, 'operation')
        with pytest.raises(EditingStateError, match='não pertence'):
            service.fail_operation(document_id, 'operation', 'Synthetic failure',
                                   partial_draft_path=other if other_path else file)
    assert service.operation(document_id, 'operation') == before
