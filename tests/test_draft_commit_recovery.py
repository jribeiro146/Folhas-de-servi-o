"""Draft receipts recover navigation and retain the partial copy on explicit retry."""
import json
from pathlib import Path
import pytest

from src.services.document_data_service import DocumentDataService
from tests.test_editing_quarantine import quarantine_app, replace_stored_state, stored_evidence


def draft_session(quarantine_app, original):
    app, service, _, draft = quarantine_app
    path = draft.parent.parent / 'synthetic.xlsx' if original else draft
    replace_stored_state(service, quarantine_app[2], app.config['SYNTHETIC_INITIAL_STATE'])
    client = app.test_client()
    headers = {'X-Mail-Monitor-Scope':client.get('/api/graph/monitor-context').json['scope']}
    bootstrap = client.get(f'/api/file/{path.stem}/bootstrap?client_id=tab-1')
    assert bootstrap.status_code == 200, bootstrap.json
    editing = bootstrap.json['editing']
    metadata = dict(document_id=editing['document_id'], client_id='tab-1',
        lease_token=editing['lease']['token'], base_revision=editing['revision'], idempotency_key='recover-draft')
    return app, service, path, client, headers, metadata


@pytest.mark.parametrize('original', [False, True])
def test_draft_receipt_opens_correct_file_without_private_data_or_mutation(quarantine_app, original):
    app, service, path, client, headers, metadata = draft_session(quarantine_app, original)
    before_file = path.read_bytes()
    payload = {'intervention_report':'Texto completo fictício', '_internal_observations':'PRIVATE SYNTHETIC MARKER',
               '_edit':metadata}
    saved = client.post(f'/api/file/{path.stem}/draft', json=payload, headers=headers)
    assert saved.status_code == 200, saved.json
    before_state = stored_evidence(service, metadata['document_id'])
    receipt = client.get(f'/api/editing/operations/{metadata["document_id"]}/recover-draft', headers=headers)
    assert receipt.status_code == 200
    operation = receipt.json['operation']
    assert operation['kind'] == 'draft' and operation['status'] == 'complete'
    assert operation['result']['file'] == saved.json['file']
    assert operation['result']['created_copy'] == original
    assert 'PRIVATE' not in receipt.text and 'lease' not in receipt.text and 'draft_path' not in receipt.text
    assert stored_evidence(service, metadata['document_id']) == before_state
    assert service.recover_operation(metadata['document_id'], 'recover-draft', {'provider':'local','id':'foreign'}) is None
    assert client.get(f'/api/file/{saved.json["file"]}/bootstrap?client_id=tab-1').status_code == 200
    if original:
        assert path.read_bytes() == before_file


@pytest.mark.parametrize('death', [False, True])
def test_failed_copy_retains_key_and_latest_form_across_bootstrap(quarantine_app, monkeypatch, death):
    app, service, path, client, headers, metadata = draft_session(quarantine_app, True)
    before_file = path.read_bytes()
    original_write = DocumentDataService.write
    def interrupted(*args):
        if death:
            raise SystemExit(76)
        raise OSError('Synthetic draft write failure')
    monkeypatch.setattr(DocumentDataService, 'write', interrupted)
    payload = {'intervention_report':'Primeira tentativa', '_edit':metadata}
    if death:
        with pytest.raises(SystemExit):
            client.post(f'/api/file/{path.stem}/draft', json=payload, headers=headers)
    else:
        assert client.post(f'/api/file/{path.stem}/draft', json=payload, headers=headers).status_code == 500
    receipt = client.get(f'/api/editing/operations/{metadata["document_id"]}/recover-draft', headers=headers)
    assert receipt.json['operation']['status'] == ('interrupted' if death else 'failed')
    copy = Path(service.operation(metadata['document_id'], 'recover-draft')['context']['draft_path'])
    before_copies = set(path.parent.glob('*/*.xlsx'))
    monkeypatch.setattr(DocumentDataService, 'write', original_write)
    reopened = client.get(f'/api/file/{path.stem}/bootstrap?client_id=tab-1').json['editing']
    metadata.update(lease_token=reopened['lease']['token'], base_revision=reopened['revision'])
    payload['intervention_report'] = 'Texto mais recente após retoma'
    result = client.post(f'/api/file/{path.stem}/draft', json=payload, headers=headers)
    assert result.status_code == 200, result.json
    assert result.json['file'] == copy.stem
    assert set(path.parent.glob('*/*.xlsx')) == before_copies
    assert DocumentDataService(copy).read()['intervention_report'] == payload['intervention_report']
    assert path.read_bytes() == before_file


@pytest.mark.parametrize('retired', [False, True])
def test_partial_copy_closed_or_removed_allows_explicit_new_key(quarantine_app, monkeypatch, retired):
    app, service, path, client, headers, metadata = draft_session(quarantine_app, True)
    original_bytes = path.read_bytes()
    original_write = DocumentDataService.write
    monkeypatch.setattr(DocumentDataService, 'write', lambda *args: (_ for _ in ()).throw(OSError('Synthetic failure')))
    payload = {'intervention_report':'Texto preservado', '_edit':metadata}
    assert client.post(f'/api/file/{path.stem}/draft', json=payload, headers=headers).status_code == 500
    operation = service.operation(metadata['document_id'], 'recover-draft')
    copy = Path(operation['context']['draft_path'])
    # Simulate the durable cancellation boundary, with or without source retirement.
    from src.services.editing_state_service import EditorIdentity
    owner = EditorIdentity('synthetic-user', 'Synthetic')
    lease = service.acquire_lease(copy, owner, 'cancel-tab')
    assert lease['document_id'] == operation['context']['draft_document_id']
    service.claim_operation(document_id=lease['document_id'], kind='cancel', idempotency_key='synthetic-cancel',
        identity=owner, client_id='cancel-tab', lease_token=lease['lease']['token'], base_revision=lease['revision'])
    service.commit_operation(document_id=lease['document_id'], idempotency_key='synthetic-cancel',
        path=copy, response={'success':True}, release_lease=True)
    if retired:
        copy.unlink()
    monkeypatch.setattr(DocumentDataService, 'write', original_write)
    blocked = client.post(f'/api/file/{path.stem}/draft', json=payload, headers=headers)
    assert blocked.status_code == 409 and blocked.json['code'] == 'draft_copy_unavailable'
    receipt = client.get(f'/api/editing/operations/{metadata["document_id"]}/recover-draft', headers=headers).json['operation']
    assert receipt['status'] == 'failed' and receipt['result']['code'] == 'draft_copy_unavailable'
    metadata['idempotency_key'] = 'new-explicit-copy'
    saved = client.post(f'/api/file/{path.stem}/draft', json=payload, headers=headers)
    assert saved.status_code == 200, saved.json
    assert saved.json['file'] != copy.stem and path.read_bytes() == original_bytes
