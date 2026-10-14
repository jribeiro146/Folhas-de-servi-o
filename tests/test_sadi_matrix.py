"""Cross-check production SADI policy and one bounded Windows failure model."""
from copy import deepcopy
import errno
import os

import pytest

from tests.test_maintenance_access import production_sadi, login, lease
from tests.test_maintenance import complete_document, sign_document
from src.services.maintenance_private_service import MaintenancePrivateService
from src.services.document_data_service import DocumentDataService


@pytest.mark.parametrize('email', ['jribeiro@sensorpoint.pt', 'acarvalho@sensorpoint.pt'])
def test_absent_customer_still_needs_technician_and_site_changes_invalidate_only_that_site(production_sadi, email):
    app, client, draft, edits, root = production_sadi
    login(client, email)
    metadata = lease(client, draft, 'sadi-matrix')
    document = complete_document()
    site = document['maintenance_checklists'][0]
    site['customer_not_present'] = True
    sign_document(document, app.secret_key)
    checked = client.post(f'/api/file/{draft.stem}/maintenance/validate', json={'document':document})
    assert checked.status_code == 200 and checked.json['errors'] == []
    assert checked.json['sites'][0]['waived'] == {'customer':True, 'technician':False}
    saved = client.post(f'/api/file/{draft.stem}/draft', json={**document, '_edit':metadata})
    assert saved.status_code == 200
    reopened = client.get(f'/api/file/{draft.stem}')
    assert reopened.cache_control.no_store
    assert reopened.json['document']['maintenance_checklists'][0] == site
    stored = MaintenancePrivateService(root / 'private/sadi').read(edits.resolve_document_id(draft))
    assert stored == document['maintenance_checklists']
    assert 'maintenance_checklists' not in DocumentDataService(draft).read()

    changed = deepcopy(document)
    changed['maintenance_checklists'][0]['final_observations'] = 'Alteração fictícia posterior à assinatura'
    checked = client.post(f'/api/file/{draft.stem}/maintenance/validate', json={'document':changed})
    assert checked.json['errors']
    assert checked.json['sites'][0]['signed']['technician'] is False
    assert all(checked.json['sites'][1]['signed'].values())
    assert MaintenancePrivateService(root / 'private/sadi').read(edits.resolve_document_id(draft)) == stored

    missing = deepcopy(document)
    missing['maintenance_checklists'][0]['signatures'].pop('technician')
    missing['maintenance_checklists'][0]['technician_signature_not_collected'] = True
    app.extensions['test_graph_queue'].run_until_idle()
    metadata = lease(client, draft, 'sadi-matrix')
    failed = client.post(f'/api/file/{draft.stem}/send', json={**missing, '_edit':metadata})
    assert failed.status_code == 400 and draft.is_file()
    assert any('assinatura' in error.casefold() for error in failed.json['missing_fields'])
    with app.extensions['test_graph_queue']._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM graph_sync_jobs WHERE kind='archive_and_remove'").fetchone()[0] == 0


def test_private_bundle_rename_denied_preserves_draft_and_explicit_retry(production_sadi, monkeypatch):
    # Deterministic PermissionError models the observed failure boundary. It does
    # not identify the cause of the historical Windows WinError 5 occurrence.
    app, client, draft, _edits, root = production_sadi
    login(client, 'jribeiro@sensorpoint.pt')
    metadata = lease(client, draft, 'rename-denied')
    document = complete_document()
    sign_document(document, app.secret_key)
    original = os.rename
    denied = []
    def rename(source, destination, *args, **kwargs):
        if source.parent == root / 'private/sadi/pdf' and source.name.startswith('.t-'):
            denied.append(source)
            raise PermissionError(errno.EACCES, 'Synthetic private publication denied')
        return original(source, destination, *args, **kwargs)
    monkeypatch.setattr(os, 'rename', rename)
    failed = client.post(f'/api/file/{draft.stem}/send', json={**document, '_edit':metadata})
    assert failed.status_code == 500 and len(denied) == 1 and draft.is_file()
    assert not list((root / 'private/sadi/pdf').iterdir())
    queue = app.extensions['test_graph_queue']
    with queue._connect() as db:
        assert db.execute('SELECT COUNT(*) FROM graph_sync_jobs').fetchone()[0] == 0
    monkeypatch.setattr(os, 'rename', original)
    retry = client.post(f'/api/file/{draft.stem}/send', json={**document, '_edit':metadata})
    assert retry.status_code == 200, retry.json
    assert len(list((root / 'private/sadi/pdf').iterdir())) == 1
    assert client.get(retry.json['maintenance_bundle_url']).status_code == 200
