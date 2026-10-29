"""Synthetic provider refusals through real transport, queue and admin boundaries."""
from contextlib import closing
from email.utils import formatdate
import io
import json
import sqlite3
import time
from urllib.error import HTTPError

import pytest

from src.services.provider_waits import ProviderWaitStore, ProviderWaitError, provider_scope
from src.services.graph_storage_service import GraphConfig, GraphStorageService
from src.services.graph_sync_queue import GraphSyncQueue
from src.services.graph_sync_coordinator import GraphRefreshCoordinator
from src.services.active_file_index import ACTIVE_INDEX_NAME
from src.services.diagnostic_views import safe_refresh_status
from tests.test_graph_refresh_coordinator import wait_finished
from tests.test_operational_admin import admin_app, configured_synthetic_admin, login, ADMIN


def graph_with_clock(tmp_path, now):
    store = ProviderWaitStore(tmp_path / 'waits.sqlite3', clock=lambda: now[0])
    return GraphStorageService(GraphConfig('synthetic', 'synthetic', 'synthetic', 'library'), provider_wait_store=store)


def refusal(header='120', status=429):
    return HTTPError('https://graph.microsoft.com/v1.0/synthetic', status, 'synthetic',
                     {'Retry-After': header}, io.BytesIO(b'synthetic provider body'))


@pytest.mark.parametrize('dated', [False, True])
def test_http_pause_stops_new_instances_before_token_or_network_and_resumes_at_deadline(tmp_path, monkeypatch, dated):
    now = [float(int(time.time()))]
    graph = graph_with_clock(tmp_path, now)
    requests, tokens = [], []
    monkeypatch.setattr(GraphStorageService, '_access_token_value', lambda self: tokens.append(1) or 'synthetic')
    def transport(req, timeout):
        requests.append(req.full_url)
        if len(requests) == 1:
            raise refusal(formatdate(now[0]+120, usegmt=True) if dated else '120')
        return io.BytesIO(b'{}')
    monkeypatch.setattr('src.services.graph_storage_service.request.urlopen', transport)
    with pytest.raises(ProviderWaitError): graph._graph_json('GET', '/synthetic')
    assert len(requests) == len(tokens) == 1
    reopened = GraphStorageService(graph.config, provider_wait_store=ProviderWaitStore(
        graph.provider_wait_store.database_path, clock=lambda: now[0]))
    now[0] += 119
    with pytest.raises(ProviderWaitError): reopened._graph_json('GET', '/synthetic')
    assert len(requests) == len(tokens) == 1
    now[0] += 1
    assert reopened._graph_json('GET', '/synthetic') == {}
    assert len(requests) == len(tokens) == 2


def test_second_page_throttle_preserves_confirmed_inventory_and_force_refresh_does_not_start(tmp_path, monkeypatch):
    now = [time.time()]
    graph = graph_with_clock(tmp_path, now)
    active = tmp_path / 'active'
    active.mkdir()
    inventory = {'version':1,'files':['previous.xlsx'],'unavailable_files':[], 'updated_at':now[0]-20,'refresh_id':'previous'}
    index = active / ACTIVE_INDEX_NAME
    index.write_text(json.dumps(inventory))
    original = index.read_bytes()
    calls = []
    monkeypatch.setattr(graph, '_access_token_value', lambda: 'synthetic')
    def transport(req, timeout):
        calls.append(req.full_url)
        if len(calls) == 1:
            return io.BytesIO(json.dumps({'value':[], '@odata.nextLink':'https://graph.microsoft.com/v1.0/next'}).encode())
        raise refusal()
    monkeypatch.setattr('src.services.graph_storage_service.request.urlopen', transport)
    coordinator = GraphRefreshCoordinator(graph, active)
    first = coordinator.request_refresh(force=True)
    result = wait_finished(coordinator)
    assert result['outcome'] == 'provider_wait' and result['last_success_at'] is None
    assert result['last_error'] is None and result['inventory']['stale']
    assert index.read_bytes() == original
    for _ in range(10):
        forced = GraphRefreshCoordinator(graph, active).request_refresh(force=True)
        assert forced['refresh_id'] == first['refresh_id'] and not forced['in_progress']
        safe = safe_refresh_status(forced)
        assert safe['outcome'] == 'provider_wait' and safe['provider_retry_at'] >= now[0]+120
    assert len(calls) == 2


def test_third_download_stops_the_pass_without_later_download_or_cleanup(tmp_path, monkeypatch):
    graph = graph_with_clock(tmp_path, [time.time()])
    items = [{'id':str(i),'name':f'synthetic{i}.xlsx','file':{'mimeType':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'},'size':1,'eTag':str(i)} for i in range(5)]
    monkeypatch.setattr(graph, 'list_active_items', lambda: items)
    monkeypatch.setattr(graph, '_access_token_value', lambda: 'synthetic')
    calls, cleanup = [], []
    def transport(req, timeout):
        calls.append(req.full_url)
        if len(calls) == 3: raise refusal()
        return io.BytesIO(b'synthetic workbook')
    monkeypatch.setattr('src.services.graph_storage_service.request.urlopen', transport)
    monkeypatch.setattr(graph, '_remove_stale_active_cache', lambda *a: cleanup.append(1))
    with pytest.raises(ProviderWaitError): graph.sync_active_files(tmp_path / 'active')
    assert len(calls) == 3 and not cleanup
    inventory = json.loads((tmp_path/'active'/ACTIVE_INDEX_NAME).read_text())
    assert len(inventory['files']) == 5 and 'synthetic2.xlsx' in inventory['unavailable_files']
    assert not (tmp_path/'active'/'synthetic3.xlsx').exists()


def queued(queue, identifier, *, kind='remove_active', status='pending', result=None, retryable=0, due=0):
    now = time.time()
    with closing(queue._connect()) as db:
        db.execute('INSERT INTO graph_sync_jobs(id,kind,payload_json,status,next_attempt_at,created_at,updated_at,result_json,retryable) VALUES(?,?,?,?,?,?,?,?,?)',
                   (identifier,kind,'{}',status,due,now,now,json.dumps(result or {}),retryable))
        db.commit()


@pytest.mark.parametrize('status,retryable', [('pending',0),('failed',1)])
def test_shared_pause_preserves_attempts_and_schedules_deadline_without_busy_loop(tmp_path,status,retryable):
    graph = graph_with_clock(tmp_path, [time.time()])
    queue = GraphSyncQueue(graph, tmp_path/'queue.sqlite3', auto_start=False)
    graph.provider_wait_store.record(graph.provider_scopes[0],http_status=429,retry_after='120')
    queued(queue,'waiting',status=status,retryable=retryable)
    assert queue._claim_next() is None
    assert queue.status('waiting')['attempts']==0
    assert 110 < queue._next_retry_delay() <= 121 and not queue._has_due_jobs()
    queued(queue,'unrelated',kind='notify_teams')
    assert queue._claim_next()['id']=='unrelated'


def test_manual_retry_and_new_pending_jobs_cannot_shorten_pause(tmp_path):
    now=[time.time()]
    graph=graph_with_clock(tmp_path,now)
    queue=GraphSyncQueue(graph,tmp_path/'queue.sqlite3',auto_start=False)
    graph.provider_wait_store.record(graph.provider_scopes[0],http_status=429,retry_after='120')
    queued(queue,'terminal',status='failed')
    with pytest.raises(ProviderWaitError): queue.retry('terminal')
    queued(queue,'new')
    assert queue._claim_next() is None
    now[0]+=120
    assert queue._claim_next()['id']=='new'


def test_pending_job_deadline_is_binding_even_without_shared_guard(tmp_path):
    queue=GraphSyncQueue(None,tmp_path/'queue.sqlite3',auto_start=False)
    queued(queue,'pending',kind='notify_teams',result={'phase':'teams_rejected','retry_not_before':time.time()+900})
    assert queue._claim_next() is None
    assert 890 < queue._next_retry_delay() <= 901


def test_anomalous_pause_does_not_start_a_timer_or_consume_attempts(tmp_path):
    graph=graph_with_clock(tmp_path,[time.time()])
    queue=GraphSyncQueue(graph,tmp_path/'queue.sqlite3',auto_start=False)
    graph.provider_wait_store.record(graph.provider_scopes[0],http_status=429,retry_after='9'*64)
    queued(queue,'waiting')
    assert queue._claim_next() is None and queue._next_retry_delay() is None
    assert queue.status('waiting')['attempts']==0


def test_admin_wait_review_is_protected_versioned_and_audit_is_read_from_same_database(admin_app):
    app, editing, _=admin_app
    store=ProviderWaitStore(clock=lambda:time.time()-90000)
    scope=provider_scope('storage','synthetic','app','library')
    before=store.record(scope,http_status=429,retry_after='86401')
    url='/api/admin/provider-waits/resume'
    args={'scope':scope,'version':before['version'],'evidence_id':before['evidence_id'],'reason':'Conferida resposta sintética'}
    headers={'X-Admin-CSRF':'synthetic-csrf'}
    assert login(app,'other@sensorpoint.pt').post(url,json=args,headers=headers).status_code==403
    admin=login(app,ADMIN.email)
    assert admin.post(url,json=args).status_code==403
    overview=admin.get('/api/admin/overview').json
    assert overview['provider_waits'][0]['can_resume']
    assert overview['provider_events'][0]['action']=='provider_wait_recorded'
    assert admin.post(url,json={**args,'version':99},headers=headers).status_code==409
    assert admin.post(url,json=args,headers=headers).status_code==200
    assert admin.post(url,json=args,headers=headers).status_code==409
    overview=admin.get('/api/admin/overview').json
    assert overview['audit'][0]['action']=='provider_wait_resumed'
    assert not overview['provider_waits'][0]['blocked']
    assert not editing.repository.admin_history()  # no non-atomic second audit write


def test_migration_adds_guard_to_previous_contract_and_preserves_existing_waits(tmp_path):
    from src.services.runtime_contract import RuntimeContract, SUPPORTED_FORMATS
    from src.services.runtime_migration import prepare, apply
    data=tmp_path/'data'
    RuntimeContract(data,'a'*64).initialize_empty()
    old={key:value for key,value in SUPPORTED_FORMATS.items() if key!='provider_waits'}
    with closing(sqlite3.connect(data/'runtime-contract.sqlite3')) as db:
        # This is the real immediately previous contract, not a destructive reset.
        db.execute('UPDATE state_contract SET formats_json=?',(json.dumps(old),))
        db.commit()
    prepare(data,[],tmp_path/'first',source_sha256='b'*64)
    apply(tmp_path/'first',source_sha256='b'*64)
    store=ProviderWaitStore(data/'provider-waits.sqlite3')
    scope=provider_scope('storage','tenant','app','library')
    pause=store.record(scope,http_status=429,retry_after='900')
    history=store.history()
    report=prepare(data,[],tmp_path/'second',source_sha256='c'*64)
    assert report['provider_waits_preserved']==1
    apply(tmp_path/'second',source_sha256='c'*64)
    restored=ProviderWaitStore(data/'provider-waits.sqlite3')
    assert restored.blocking([scope])==pause and restored.history()==history


def test_backup_restore_retains_wait_and_audit_under_global_hold(tmp_path):
    from tests.test_runtime_backup import storage, retire_roots, SOURCE, KEY
    from src.services.runtime_backup import backup, restore
    data,excel,destination,workspace=storage(tmp_path)
    store=ProviderWaitStore(data/'provider-waits.sqlite3')
    scope=provider_scope('storage','tenant','app','library')
    pause=store.record(scope,http_status=429,retry_after='900')
    history=store.history()
    backup(data,[excel],destination,source_sha256=SOURCE,key=KEY)
    retire_roots(tmp_path,data,excel)
    assert restore(destination,workspace,source_sha256=SOURCE,key=KEY)['state']=='held'
    recovered=ProviderWaitStore(data/'provider-waits.sqlite3')
    assert recovered.blocking([scope])==pause and recovered.history()==history


def test_expired_wait_is_neutral_and_new_refresh_does_not_wait_for_normal_interval(tmp_path,monkeypatch):
    now=[time.time()]
    graph=graph_with_clock(tmp_path,now)
    calls=[]
    def list_items():
        calls.append(1)
        if len(calls)==1:
            pause=graph.provider_wait_store.record(graph.provider_scopes[0],http_status=429,retry_after='5')
            raise ProviderWaitError(pause)
        return []
    monkeypatch.setattr(graph,'list_active_items',list_items)
    coordinator=GraphRefreshCoordinator(graph,tmp_path/'active',refresh_seconds=3600)
    coordinator.request_refresh(force=True)
    assert wait_finished(coordinator)['outcome']=='provider_wait'
    now[0]+=10
    status=coordinator.status()
    assert status['outcome']=='idle' and status['provider_retry_at'] is None and status['inventory']['stale']
    coordinator.request_refresh()
    assert wait_finished(coordinator)['outcome']=='success' and len(calls)==2


def test_automatic_wait_events_cannot_hide_human_admin_history(admin_app):
    app,editing,_=admin_app
    editing.repository.record_admin_action({'actor_email':ADMIN.email,'action':'release_sheet',
        'target':'synthetic-document','reason':'Synthetic human action','details':{}})
    store=ProviderWaitStore()
    scope=provider_scope('storage','tenant','app','library')
    for _ in range(150): store.record(scope,http_status=429,retry_after='1')
    overview=login(app,ADMIN.email).get('/api/admin/overview').json
    assert len(overview['provider_events'])==100
    assert any(event['action']=='release_sheet' for event in overview['audit'])
    assert not any(event['action']=='provider_wait_recorded' for event in overview['audit'])


def test_held_confirmation_is_checked_while_another_scope_waits_an_hour(tmp_path):
    graph=graph_with_clock(tmp_path,[time.time()])
    queue=GraphSyncQueue(graph,tmp_path/'queue.sqlite3',auto_start=False)
    graph.provider_wait_store.record(graph.provider_scopes[0],http_status=429,retry_after='3600')
    queued(queue,'waiting')
    queued(queue,'local-confirmation',kind='archive_and_mail_local',status='held')
    assert queue._claim_next() is None
    assert queue._next_retry_delay()==5.0
    assert queue.status('waiting')['attempts']==queue.status('local-confirmation')['attempts']==0
