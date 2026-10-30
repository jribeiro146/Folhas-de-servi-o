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


def test_explicit_resolution_and_new_publication_release_old_local_failure(publication, monkeypatch):
    source, editing, actor, before, metadata, graph, queue = publication
    copy = queue._publish_locked_metadata
    def diverge(*args, **kwargs):
        copy(*args, **kwargs)
        source.write_bytes(b'different source during acknowledgement')
    monkeypatch.setattr(queue, '_publish_locked_metadata', diverge)
    old = queue.enqueue('upload_active', {'draft_path': str(source)})
    queue.run_until_idle()
    conflict = editing.snapshot(source)
    editing.save_autosave(**dict(metadata, base_revision=conflict['revision'], idempotency_key='resolve'),
        document=before['server_document'], path=source, source_resolution=conflict['source_conflict']['token'])
    assert queue.unfinished_upload(source)['publication_refused_by_owner']
    with pytest.raises(ValueError, match='guardar novamente'):
        queue.retry(old['id'])
    monkeypatch.setattr(queue, '_publish_locked_metadata', copy)
    mark_dirty(source.parent)
    new = queue.enqueue('upload_active', {'draft_path': str(source)})
    queue.run_until_idle()
    assert queue.status(new['id'])['status'] == 'complete'
    assert queue.status(old['id'])['result']['superseded_by'] == new['id']
    assert queue.unfinished_upload(source) is None


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


@pytest.mark.parametrize('retryable', [False, True])
@pytest.mark.parametrize('failure_at', ['proof', 'checkpoint'])
def test_failed_checkpoint_cannot_replace_a_remote_refusal_or_wait(publication, monkeypatch, retryable, failure_at):
    import sqlite3
    import time
    from src.services.graph_storage_service import GraphConflictError
    source, editing, actor, before, metadata, graph, queue = publication
    remote = GraphStorageError('synthetic wait') if retryable else GraphConflictError('synthetic definite 412')
    remote.retry_after_seconds = 120 if retryable else None
    upload = graph.upload_active_bundle
    def refused(*args, **kwargs):
        upload(*args, **kwargs)
        raise remote
    graph.upload_active_bundle = refused
    if failure_at == 'checkpoint':
        def unavailable(*args, **kwargs):
            raise sqlite3.OperationalError('synthetic checkpoint failure')
        monkeypatch.setattr(queue, '_checkpoint_result', unavailable)
    else:
        def bad_proof(*args, **kwargs):
            raise ValueError('synthetic metadata changed while reading')
        monkeypatch.setattr(queue, '_publication_checkpoint', bad_proof)
    job = queue.enqueue('upload_active', {'draft_path': str(source)})
    started = time.time()
    queue.run_until_idle()
    failed = queue.status(job['id'])
    assert failed['will_retry'] is retryable
    assert failed['last_error'] == str(remote)
    assert (failed['result'] or {}).get('phase') != 'publication_local_pending'
    if retryable:
        assert failed['next_attempt_at'] >= started + 120
    else:
        with queue._connect() as connection:
            connection.execute('UPDATE graph_sync_jobs SET next_attempt_at=0 WHERE id=?', (job['id'],))
    queue.run_until_idle()
    assert graph.calls == 1
    assert dirty_version(source.parent)


def test_reader_during_local_copy_cannot_invalidate_autosave(publication, monkeypatch):
    source, editing, actor, before, metadata, graph, queue = publication
    reader = EditingStateService(editing.root)
    reader.repository.busy_timeout_ms = 40
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


def test_first_conflict_detected_by_autosave_has_a_durable_resolution_token(publication):
    source, editing, actor, before, metadata, graph, queue = publication
    source.write_bytes(b'external source first seen by autosave')
    with pytest.raises(RevisionConflictError) as caught:
        editing.save_autosave(**metadata, path=source, document={'report': 'unconfirmed'})
    conflict = caught.value.snapshot
    resolved = editing.save_autosave(**dict(metadata, base_revision=conflict['revision'], idempotency_key='choice'),
        document=before['server_document'], path=source, source_resolution=conflict['source_conflict']['token'])
    assert resolved['source_conflict'] is None
    assert resolved['server_document'] == before['server_document']


def test_heartbeat_waits_for_a_concurrent_autosave_without_false_conflict(publication, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    source, editing, actor, before, metadata, graph, queue = publication
    second = EditingStateService(editing.root)
    entered, release = threading.Event(), threading.Event()
    check = editing._assert_session
    def delayed_check(*args, **kwargs):
        result = check(*args, **kwargs)
        entered.set()
        assert release.wait(3)
        return result
    monkeypatch.setattr(editing, '_assert_session', delayed_check)
    with ThreadPoolExecutor(max_workers=2) as executor:
        save = executor.submit(editing.save_autosave, **dict(metadata, idempotency_key='concurrent'),
            path=source, document={'report': 'concurrent autosave'})
        assert entered.wait(2)
        heartbeat = executor.submit(second.renew_lease, before['document_id'], actor,
                                    metadata['client_id'], metadata['lease_token'])
        release.set()
        saved = save.result(3)
        assert heartbeat.result(3)['revision'] == saved['revision']


@pytest.mark.parametrize('retryable', [False, True])
def test_partial_remote_error_keeps_its_retry_policy_when_local_mutex_is_busy(publication, retryable):
    import time
    from src.services.graph_storage_service import GraphConflictError
    source, editing, actor, before, metadata, graph, queue = publication
    remote = GraphStorageError('synthetic provider pause') if retryable else GraphConflictError('synthetic remote conflict')
    remote.retry_after_seconds = 120 if retryable else None
    upload = graph.upload_active_bundle
    def fail_after_metadata(*args, **kwargs):
        upload(*args, **kwargs)
        raise remote
    graph.upload_active_bundle = fail_after_metadata
    job = queue.enqueue('upload_active', {'draft_path': str(source)})
    started = time.time()
    with editing.operation_guard(before['document_id'], actor):
        queue.run_until_idle()
    failed = queue.status(job['id'])
    assert failed['will_retry'] is True  # Only the local acknowledgement resumes.
    assert failed['result']['publication_remote_failure']['retryable'] is retryable
    assert failed['result']['phase'] == 'publication_local_pending'
    with queue._connect() as connection:
        connection.execute('UPDATE graph_sync_jobs SET next_attempt_at=0 WHERE id=?', (job['id'],))
    queue.run_until_idle()
    failed = queue.status(job['id'])
    assert failed['result']['phase'] == 'publication_partial_confirmed'
    assert failed['last_error'] == str(remote)
    assert failed['will_retry'] is retryable
    assert graph.calls == 1
    if retryable:
        assert failed['next_attempt_at'] >= started + 120


def test_newer_complete_job_cannot_retire_local_phase_without_owner_refusal(publication, monkeypatch):
    source, editing, actor, before, metadata, graph, queue = publication
    copy = queue._publish_locked_metadata
    def interrupted(*args, **kwargs):
        raise OSError('interrupted before copying metadata')
    monkeypatch.setattr(queue, '_publish_locked_metadata', interrupted)
    old = queue.enqueue('upload_active', {'draft_path': str(source)})
    queue.run_until_idle()
    new = queue.enqueue('upload_active', {'draft_path': str(source)})
    with queue._connect() as connection:
        connection.execute("UPDATE graph_sync_jobs SET status='complete' WHERE id=?", (new['id'],))
    assert queue.unfinished_upload(source)['id'] == old['id']
    assert queue.status(old['id'])['status'] == 'failed'
    monkeypatch.setattr(queue, '_publish_locked_metadata', copy)
    with queue._connect() as connection:
        connection.execute('UPDATE graph_sync_jobs SET next_attempt_at=0 WHERE id=?', (old['id'],))
    queue.run_until_idle()
    assert queue.status(old['id'])['status'] == 'complete'
    assert graph.calls == 1


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
@pytest.mark.parametrize('action', ['autosave', 'send', 'bootstrap', 'source_choice'])
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
    if action == 'source_choice':
        import os
        copy = queue._publish_locked_metadata
        def diverge(*args, **kwargs):
            copy(*args, **kwargs)
            info = draft.stat()
            os.utime(draft, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))
        monkeypatch.setattr(queue, '_publish_locked_metadata', diverge)
        queue.run_until_idle()
        conflict = editing.snapshot(draft)
        response = client.post(f'/api/file/{draft.stem}/autosave', json={
            'document': document, 'source_resolution': conflict['source_conflict']['token'],
            '_edit': {**meta, 'base_revision': conflict['revision'], 'idempotency_key': 'choose-current'}})
        assert response.status_code == 200, response.get_json()
        chosen = response.get_json()['editing']
        response = client.post(f'/api/file/{draft.stem}/send', json={**document,
            '_edit': {**meta, 'base_revision': chosen['revision'], 'idempotency_key': 'send-chosen'}})
        assert response.status_code == 409, response.get_json()
        assert response.get_json()['code'] == 'publication_failed'
        assert 'Guarde novamente' in response.get_json()['error']
        assert 'administração' not in response.get_json()['error']
        return
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

@pytest.mark.parametrize('external', [False, True])
def test_refresh_after_own_publication_reuses_matching_etag_and_protects_changes(publication, external):
    from tests.test_graph_publication_recovery import InventoryGraph, item
    source, editing, actor, before, metadata, graph, queue = publication
    job = queue.enqueue('upload_active', {'draft_path': str(source)})
    queue.run_until_idle()
    assert queue.status(job['id'])['status'] == 'complete'
    refresh = InventoryGraph(source.parent.name)
    refresh.items = [{'id': 'folder', 'name': source.parent.name, 'eTag': 'folder-current',
                      'folder': {'childCount': 1}}]
    refresh.children = [item(source.name, 'synthetic-workbook', 'external-v2' if external else 'own-1')]
    refresh.version = b'changed external workbook'
    refresh.sync_active_files(source.parent.parent)
    after = editing.snapshot(source)
    assert after['server_document'] == before['server_document']
    assert bool(after['source_conflict']) is external
    assert after['revision'] == before['revision'] + int(external)
    assert refresh.downloads == (['synthetic-workbook'] if external else [])


def test_unrelated_corrupt_editing_record_cannot_hide_a_valid_publication_refusal(publication):
    source, editing, actor, before, metadata, graph, queue = publication
    with editing.repository._connect() as connection:
        row = connection.execute('SELECT * FROM editing_states WHERE document_id=?', (before['document_id'],)).fetchone()
        connection.execute('DELETE FROM editing_states WHERE document_id=?', (before['document_id'],))
        connection.execute('INSERT INTO editing_states VALUES(?,?,?)', ('corrupt-other', '{"publication_refusals":{}}', 1))
        state = json.loads(row['state_json'])
        state['publication_refusals'] = {'confirmed-choice': 100}
        connection.execute('INSERT INTO editing_states VALUES(?,?,?)',
                           (before['document_id'], json.dumps(state), row['updated_at']))
    assert queue._publication_refused('confirmed-choice') is True
    assert queue._publication_refused('unconfirmed') is False


@pytest.mark.parametrize('newer_complete', [False, True])
def test_partial_local_retry_never_republishes_after_a_newer_started_upload(publication, monkeypatch, newer_complete):
    from src.services.graph_storage_service import GraphConflictError
    source, editing, actor, before, metadata, graph, queue = publication
    upload = graph.upload_active_bundle
    def partial_first(*args, **kwargs):
        result = upload(*args, **kwargs)
        if graph.calls == 1:
            raise GraphStorageError('synthetic retryable failure')
        if not newer_complete:
            raise GraphConflictError('synthetic newer refusal')
        return result
    graph.upload_active_bundle = partial_first
    publish = queue._publish_staging_metadata
    monkeypatch.setattr(queue, '_publish_staging_metadata',
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError('busy before local journal')))
    old = queue.enqueue('upload_active', {'draft_path': str(source)})
    queue.run_until_idle()
    monkeypatch.setattr(queue, '_publish_staging_metadata', publish)
    new = queue.enqueue('upload_active', {'draft_path': str(source)})
    queue.run_until_idle()
    assert graph.calls == 2
    with queue._connect() as connection:
        connection.execute('UPDATE graph_sync_jobs SET next_attempt_at=0 WHERE id=?', (old['id'],))
    queue.run_until_idle()
    assert graph.calls == 2  # Never a third PUT of the older immutable snapshot.
    result = queue.status(old['id'])
    assert result['result']['blocked_by_newer_upload'] == new['id']
    assert result['status'] == ('complete' if newer_complete else 'failed')
    assert result['will_retry'] is False
    assert editing.snapshot(source)['server_document'] == before['server_document']


def test_partial_412_and_half_local_copy_recovers_locally_while_autosave_continues(publication, monkeypatch):
    import shutil
    from src.services.graph_storage_service import GraphConflictError
    source, editing, actor, before, metadata, graph, queue = publication
    upload = graph.upload_active_bundle
    def refused(path, **kwargs):
        upload(path, **kwargs)
        (path.parent / '.graph_bundle.json').write_text('{"synthetic":true}')
        raise GraphConflictError('synthetic definitive 412')
    graph.upload_active_bundle = refused
    copy = queue._publish_locked_metadata
    def interrupted(staged, original, **kwargs):
        shutil.copy2(staged.with_name(staged.name + '.graph.json'),
                     original.with_name(original.name + '.graph.json'))
        raise OSError('interrupted before second metadata file')
    monkeypatch.setattr(queue, '_publish_locked_metadata', interrupted)
    job = queue.enqueue('upload_active', {'draft_path': str(source)})
    queue.run_until_idle()
    assert queue.status(job['id'])['will_retry']
    pending = editing.snapshot(source)
    assert pending['publication_pending'] and not pending['source_conflict']
    saved = editing.save_autosave(**dict(metadata, idempotency_key='while-pending'),
        document={'report': 'More text while local acknowledgement waits'}, path=source)
    closed = editing.close_editing_session(**dict(metadata, base_revision=saved['revision'], idempotency_key='close-pending'),
        document={'report': 'Last text before closing'}, path=source)
    assert closed['autosaved']
    monkeypatch.setattr(queue, '_publish_locked_metadata', copy)
    with queue._connect() as connection:
        connection.execute('UPDATE graph_sync_jobs SET next_attempt_at=0 WHERE id=?', (job['id'],))
    queue.run_until_idle()
    failed = queue.status(job['id'])
    assert failed['will_retry'] is False
    assert failed['result']['phase'] == 'publication_partial_confirmed'
    assert graph.calls == 1  # The definite 412 never becomes a remote retry.
    assert not editing.snapshot(source)['publication_pending']
    assert editing.snapshot(source)['server_document'] == {'report': 'Last text before closing'}


@pytest.mark.parametrize('newer_save', [False, True])
def test_missing_origin_recovers_automatically_and_unblocks_finalization(publication, monkeypatch, newer_save):
    source, editing, actor, before, metadata, graph, queue = publication
    absent = source.parent.with_name('synthetic-temporarily-absent')
    upload = graph.upload_active_bundle
    def missing_after_put(*args, **kwargs):
        result = upload(*args, **kwargs)
        source.parent.rename(absent)
        return result
    graph.upload_active_bundle = missing_after_put
    old = queue.enqueue('upload_active', {'draft_path': str(source)})
    queue.run_until_idle()
    assert queue.status(old['id'])['will_retry']
    absent.rename(source.parent)
    graph.upload_active_bundle = upload
    if newer_save:
        new = queue.enqueue('upload_active', {'draft_path': str(source)})
        queue.run_until_idle()
        assert queue.status(new['id'])['status'] == 'complete'
    with queue._connect() as connection:
        connection.execute('UPDATE graph_sync_jobs SET next_attempt_at=0 WHERE id=?', (old['id'],))
    queue.run_until_idle()  # No admin retry() needed after the folder returns.
    assert graph.calls == (2 if newer_save else 1)
    assert queue.status(old['id'])['status'] == 'complete'
    assert queue.unfinished_upload(source) is None
    assert editing.snapshot(source)['server_document'] == before['server_document']


