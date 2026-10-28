"""Real queue/editing persistence with synthetic Graph responses only."""
import hashlib
import json
from pathlib import Path

import pytest

from src.services.editing_state_service import EditingStateService, EditorIdentity, RevisionConflictError
from src.services.editing_state_service import OperationInProgressError
from src.services.graph_storage_service import GraphStorageService, GraphStorageError
from src.services.graph_sync_queue import GraphSyncQueue
from src.services.local_changes import mark_dirty, dirty_version


class PublicationGraph:
    def __init__(self):
        self.calls = 0
        self.partial = False

    def upload_active_bundle(self, path, **kwargs):
        self.calls += 1
        GraphStorageService._write_item_meta(path.with_name(path.name + '.graph.json'),
            {'id': 'synthetic-workbook', 'name': path.name, 'eTag': f'own-{self.calls}'})
        if self.partial:
            raise GraphStorageError('Synthetic refusal after a confirmed workbook upload')
        return [path.name]


@pytest.fixture
def publication(tmp_path):
    folder = tmp_path / '2026_9900_2026-10-28_TF'
    folder.mkdir()
    source = folder / (folder.name + '.xlsx')
    source.write_bytes(b'synthetic workbook')
    GraphStorageService._write_item_meta(source.with_name(source.name + '.graph.json'),
        {'id': 'synthetic-workbook', 'name': source.name, 'eTag': 'original'})
    editing = EditingStateService(tmp_path / 'editing-state')
    actor = EditorIdentity('synthetic-tenant:synthetic-user', 'Tecnico ficticio')
    editing.associate_path(source, 'synthetic-document', original_name=source.stem, owner=actor)
    state = editing.acquire_lease(source, actor, 'synthetic-device')
    metadata = dict(document_id=state['document_id'], identity=actor, client_id='synthetic-device',
        lease_token=state['lease']['token'], base_revision=state['revision'], idempotency_key='initial')
    state = editing.save_autosave(**metadata, document={'report': 'Recoverable technician text'})
    metadata['base_revision'] = state['revision']
    graph = PublicationGraph()
    queue = GraphSyncQueue(graph, tmp_path / 'graph-sync.sqlite3', auto_start=False)
    queue.editing_state_service = editing
    mark_dirty(folder)
    return source, editing, actor, state, metadata, graph, queue


@pytest.mark.parametrize('partial', [False, True])
def test_own_metadata_keeps_autosave_revision_and_editing_etag(publication, partial):
    source, editing, actor, before, metadata, graph, queue = publication
    graph.partial = partial
    fingerprint = editing._fingerprint(source)
    job = queue.enqueue('upload_active', {'draft_path': str(source)})
    queue.run_until_idle()
    after = editing.snapshot(source)
    assert editing._fingerprint(source) != fingerprint
    assert after['server_document'] == before['server_document']
    assert after['revision'] == before['revision']
    assert after['etag'] == before['etag']
    assert after['owner'] == before['owner']
    assert queue.status(job['id'])['status'] == ('failed' if partial else 'complete')
    assert bool(dirty_version(source.parent)) is partial
    reopened = EditingStateService(editing.root).acquire_lease(source, actor, 'second-device')
    assert reopened['server_document'] == before['server_document']
    assert reopened['revision'] == before['revision']


def test_real_external_change_preserves_recovery_and_requires_explicit_choice(publication):
    source, editing, actor, before, metadata, graph, queue = publication
    source.write_bytes(b'external actual content change')
    after = editing.snapshot(source)
    assert after['server_document'] == before['server_document']
    assert after['revision'] == before['revision'] + 1
    assert after['source_conflict']['token']
    metadata.update(base_revision=after['revision'], idempotency_key='unconfirmed-change')
    with pytest.raises(RevisionConflictError):
        editing.save_autosave(**metadata, document={'report': 'must wait for choice'})
    reopened = EditingStateService(editing.root).acquire_lease(source, actor, 'second-device')
    assert reopened['source_conflict'] == after['source_conflict']
    assert reopened['server_document'] == before['server_document']


def test_legacy_fingerprint_formula_remains_unchanged(publication):
    source, editing, *_ = publication
    parts = []
    for path in (source, source.with_name(source.stem + '__documento.json'),
                 source.with_name(source.stem + '__assinatura_cliente.png'),
                 source.with_name(source.name + '.graph.json')):
        if path.exists():
            stat = path.stat()
            parts.append(f'{path.name}:{stat.st_mtime_ns}:{stat.st_size}')
        else:
            parts.append(f'{path.name}:missing')
    assert editing._fingerprint(source) == hashlib.sha256('|'.join(parts).encode()).hexdigest()


@pytest.mark.parametrize('choice', ['recover', 'source'])
def test_explicit_source_choice_is_versioned_and_keeps_prior_recovery(publication, choice):
    source, editing, actor, before, metadata, graph, queue = publication
    source.write_bytes(b'external source')
    conflict = editing.snapshot(source)
    metadata.update(base_revision=conflict['revision'], idempotency_key='explicit-choice')
    document = before['server_document'] if choice == 'recover' else {'report': 'Source version selected'}
    after = editing.save_autosave(**metadata, document=document, path=source,
        source_resolution=conflict['source_conflict']['token'])
    assert after['source_conflict'] is None
    assert after['server_document'] == document
    with editing._state_transaction(after['document_id']) as state:
        assert state['previous_source_recovery']['document'] == before['server_document']
    repeated = editing.save_autosave(**metadata, document=document, path=source,
        source_resolution=conflict['source_conflict']['token'])
    assert repeated['revision'] == after['revision']


def test_choice_against_another_source_change_is_refused(publication):
    source, editing, actor, before, metadata, graph, queue = publication
    source.write_bytes(b'first external source')
    conflict = editing.snapshot(source)
    metadata.update(base_revision=conflict['revision'], idempotency_key='old-choice')
    source.write_bytes(b'second external source')
    with pytest.raises(RevisionConflictError):
        editing.save_autosave(**metadata, document={'report': 'stale choice'}, path=source,
            source_resolution=conflict['source_conflict']['token'])
    assert editing.snapshot(source)['server_document'] == before['server_document']


@pytest.mark.parametrize('boundary', ['before_copy', 'after_copy'])
def test_restart_finishes_only_local_phase_after_remote_success(publication, monkeypatch, boundary):
    source, editing, actor, before, metadata, graph, queue = publication
    copy = queue._publish_locked_metadata
    def interrupted(*args, **kwargs):
        if boundary == 'after_copy':
            copy(*args, **kwargs)
        raise OSError('Synthetic process interruption before editing acknowledgement')
    monkeypatch.setattr(queue, '_publish_locked_metadata', interrupted)
    job = queue.enqueue('upload_active', {'draft_path': str(source)})
    queue.run_until_idle()
    failed = queue.status(job['id'])
    assert failed['status'] == 'failed'
    assert failed['result']['phase'] == 'publication_local_pending'
    assert graph.calls == 1
    after = EditingStateService(editing.root).snapshot(source)
    assert after['server_document'] == before['server_document']
    assert after['revision'] == before['revision']
    restarted = GraphSyncQueue(graph, queue.database_path, auto_start=False)
    restarted.editing_state_service = EditingStateService(editing.root)
    with restarted._connect() as connection:
        connection.execute('UPDATE graph_sync_jobs SET next_attempt_at=0 WHERE id=?', (job['id'],))
    restarted.run_until_idle()
    assert restarted.status(job['id'])['status'] == 'complete'
    assert graph.calls == 1
    assert editing.snapshot(source)['server_document'] == before['server_document']
    assert not dirty_version(source.parent)


def test_reader_during_local_copy_cannot_invalidate_autosave(publication, monkeypatch):
    source, editing, actor, before, metadata, graph, queue = publication
    reader = EditingStateService(editing.root)
    copy = queue._publish_locked_metadata
    attempts = []
    def concurrent_reader(*args, **kwargs):
        with pytest.raises(OperationInProgressError):
            reader.snapshot(source)
        attempts.append(True)
        copy(*args, **kwargs)
    monkeypatch.setattr(queue, '_publish_locked_metadata', concurrent_reader)
    job = queue.enqueue('upload_active', {'draft_path': str(source)})
    queue.run_until_idle()
    assert attempts == [True]
    assert queue.status(job['id'])['status'] == 'complete'
    assert reader.snapshot(source)['server_document'] == before['server_document']


def test_divergent_partial_phase_requires_choice_and_cannot_be_replayed(publication, monkeypatch):
    source, editing, actor, before, metadata, graph, queue = publication
    copy = queue._publish_locked_metadata
    def diverged(*args, **kwargs):
        copy(*args, **kwargs)
        source.write_bytes(b'external change while local confirmation was interrupted')
    monkeypatch.setattr(queue, '_publish_locked_metadata', diverged)
    job = queue.enqueue('upload_active', {'draft_path': str(source)})
    queue.run_until_idle()
    assert queue.status(job['id'])['status'] == 'failed'
    conflict = editing.snapshot(source)
    assert conflict['source_conflict']
    assert conflict['server_document'] == before['server_document']
    metadata.update(base_revision=conflict['revision'], idempotency_key='choose-after-interruption')
    resolved = editing.save_autosave(**metadata, document=before['server_document'], path=source,
        source_resolution=conflict['source_conflict']['token'])
    assert not resolved['publication_pending']
    monkeypatch.setattr(queue, '_publish_locked_metadata', copy)
    with queue._connect() as connection:
        connection.execute("UPDATE graph_sync_jobs SET retryable=1,next_attempt_at=0 WHERE id=?", (job['id'],))
    queue.run_until_idle()
    assert queue.status(job['id'])['status'] == 'failed'
    assert graph.calls == 1
    assert editing.snapshot(source)['server_document'] == before['server_document']


def test_close_during_source_conflict_preserves_recovery(publication):
    source, editing, actor, before, metadata, graph, queue = publication
    source.write_bytes(b'external change')
    result = editing.close_editing_session(**metadata, document={'report': 'unconfirmed close'}, path=source)
    assert not result['autosaved']
    assert result['editing']['source_conflict']
    assert editing.snapshot(source)['server_document'] == before['server_document']


def test_inspect_backup_counts_conflicts_without_mutating_or_exposing_content(publication, tmp_path):
    import sqlite3
    from tools.inspect_publication_recovery import inspect
    source, editing, actor, before, metadata, graph, queue = publication
    copy = tmp_path / 'consistent-backup.sqlite3'
    with editing.repository._connect() as original, sqlite3.connect(copy) as destination:
        original.backup(destination)
    digest = hashlib.sha256(copy.read_bytes()).hexdigest()
    assert inspect(copy, source.parent)['counts'] == {'unchanged': 1}
    source.write_bytes(b'external source')
    report = inspect(copy, source.parent)
    assert report['counts'] == {'source_changed_requires_choice': 1}
    assert report['records'][0]['server_recovery_available'] is True
    assert 'Recoverable technician text' not in json.dumps(report)
    assert source.name not in json.dumps(report)
    assert hashlib.sha256(copy.read_bytes()).hexdigest() == digest


def test_busy_document_mutex_retries_only_local_acknowledgement(publication):
    source, editing, actor, before, metadata, graph, queue = publication
    job = queue.enqueue('upload_active', {'draft_path': str(source)})
    with editing.operation_guard(before['document_id'], actor):
        queue.run_until_idle()
    assert queue.status(job['id'])['result']['phase'] == 'publication_local_pending'
    with queue._connect() as connection:
        connection.execute('UPDATE graph_sync_jobs SET next_attempt_at=0 WHERE id=?', (job['id'],))
    queue.run_until_idle()
    assert queue.status(job['id'])['status'] == 'complete'
    assert graph.calls == 1
    assert editing.snapshot(source)['revision'] == before['revision']


def test_new_autosave_during_remote_put_survives_metadata_acknowledgement(publication):
    source, editing, actor, before, metadata, graph, queue = publication
    upload = graph.upload_active_bundle
    saved = []
    def concurrent_save(path, **kwargs):
        saved.append(editing.save_autosave(**dict(metadata, idempotency_key='during-put'),
            document={'report': 'New text while the remote service responds'}))
        return upload(path, **kwargs)
    graph.upload_active_bundle = concurrent_save
    job = queue.enqueue('upload_active', {'draft_path': str(source)})
    queue.run_until_idle()
    assert queue.status(job['id'])['status'] == 'complete'
    assert editing.snapshot(source)['server_document'] == saved[0]['server_document']
    assert editing.snapshot(source)['revision'] == saved[0]['revision']

@pytest.mark.parametrize('field', ['empty', 'omitted'])
@pytest.mark.parametrize('action', ['autosave', 'send', 'bootstrap'])
def test_production_routes_continue_after_own_publication(tmp_path, monkeypatch, verified_test_release, field, action):
    from tests.test_maintenance_access import synthetic_production_sadi
    from tests.test_maintenance import complete_document
    app, client, draft, editing, _ = synthetic_production_sadi(tmp_path, monkeypatch, {}, verified_test_release)
    queue = app.extensions['test_graph_queue']
    assert not queue.auto_start and queue.mail_service is None and queue.teams_notification_service is None
    graph = PublicationGraph()
    monkeypatch.setattr(queue.graph_service, 'upload_active_bundle', graph.upload_active_bundle)
    with client.session_transaction() as session:
        session['microsoft_user'] = {'id': '22222222-2222-4222-8222-222222222222',
            'tenant_id': '11111111-1111-4111-8111-111111111111',
            'display_name': 'Pessoa fictícia', 'email': 'technician@example.invalid'}
    GraphStorageService._write_item_meta(GraphStorageService._item_meta_path(draft),
        {'id': 'synthetic-workbook', 'name': draft.name, 'eTag': 'original'})
    boot = client.get(f'/api/file/{draft.stem}/bootstrap?client_id=synthetic-metadata').get_json()
    state = boot['editing']
    meta = dict(document_id=state['document_id'], client_id='synthetic-metadata',
        lease_token=state['lease']['token'], base_revision=state['revision'], idempotency_key='save-draft')
    document = complete_document()
    document.update(service_number=draft.stem, customer_email='', maintenance_checklists=[],
                    intervention_report='Texto fictício guardado')
    document['service_types'].update(manutencao=False, assistencia=True)
    if field == 'omitted':
        document.pop('maintenance_checklists')
    saved = client.post(f'/api/file/{draft.stem}/draft', json={**document, '_edit': meta})
    assert saved.status_code == 200, saved.get_json()
    meta['base_revision'] = saved.get_json()['revision']
    document['intervention_report'] = 'Edição posterior apenas no autosave'
    meta['idempotency_key'] = 'autosave-before-publication'
    saved_auto = client.post(f'/api/file/{draft.stem}/autosave', json={'document': document, '_edit': meta})
    assert saved_auto.status_code == 200, saved_auto.get_json()
    meta['base_revision'] = saved_auto.get_json()['editing']['revision']
    waiting = client.post(f'/api/file/{draft.stem}/send',
        json={**document, '_edit': {**meta, 'idempotency_key': 'send-after-publication'}})
    assert waiting.status_code == 409 and waiting.get_json()['code'] == 'publication_pending'
    queue.run_until_idle()
    assert queue.status(saved.get_json()['graph_job_id'])['status'] == 'complete'
    assert editing.snapshot(draft)['revision'] == meta['base_revision']
    assert editing.snapshot(draft)['server_document']['intervention_report'] == document['intervention_report']
    if action == 'autosave':
        response = client.post(f'/api/file/{draft.stem}/autosave',
            json={'document': document, '_edit': {**meta, 'idempotency_key': 'after-publication'}})
    elif action == 'send':
        response = client.post(f'/api/file/{draft.stem}/send',
            json={**document, '_edit': {**meta, 'idempotency_key': 'send-after-publication'}})
    else:
        response = client.get(f'/api/file/{draft.stem}/bootstrap?client_id=synthetic-metadata')
    assert response.status_code == 200, response.get_json()
    if action == 'send':
        assert editing.operation(meta['document_id'], 'send-after-publication')['status'] == 'complete'
        assert queue.status(response.get_json()['graph_job_id'])['status'] in {'held', 'pending'}
        # Final archive/mail phase is deliberately not run.
    else:
        assert editing.snapshot(draft)['server_document']['intervention_report'] == document['intervention_report']

