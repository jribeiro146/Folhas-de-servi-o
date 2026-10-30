"""Durable mail deadlines with synthetic Microsoft responses, never delivery."""
import io
import json
import time
from email.utils import formatdate
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError

import pytest

from src.services.graph_mail_service import GraphMailService, GraphMailConfig, GraphMailTransientError
from src.services.graph_storage_service import GraphStorageService, GraphConfig, TOKEN_URL_TEMPLATE
from src.services.graph_sync_queue import GraphSyncQueue
from src.services.provider_waits import ProviderWaitStore, ProviderWaitError
from tests.test_durable_mail import Graph


@pytest.fixture
def clock(monkeypatch):
    now = [float(int(time.time()))]
    monkeypatch.setattr(time, 'time', lambda: now[0])
    return now


def mail_at(root, transport, clock):
    return GraphMailService(GraphMailConfig('synthetic', 'synthetic', 'synthetic', 'sender@example.invalid'),
        transport=transport, provider_wait_store=ProviderWaitStore(root/'waits.sqlite3', clock=lambda:clock[0]))


def enqueue(queue, root, identifier='mail:one'):
    return queue.enqueue('archive_and_remove', {'archived_path':str(root/'synthetic.xlsx'),
        'source_name':'synthetic.xlsx', 'mail':{'to':'customer@example.invalid'}}, job_id=identifier)


def accepted():
    return io.BytesIO(b'{"access_token":"synthetic","expires_in":3600}')


@pytest.mark.parametrize('header_kind', ['seconds', 'http_date'])
def test_900_seconds_survive_restart_ten_sessions_new_jobs_and_manual_retry(tmp_path, clock, header_kind):
    calls=[]; reject=[True]
    deadline=clock[0]+900
    header='900' if header_kind=='seconds' else formatdate(deadline,usegmt=True)
    def transport(req, **_):
        calls.append(req)
        if req.full_url.endswith('/sendMail') and reject[0]:
            raise HTTPError(req.full_url,429,'synthetic',{'Retry-After':header},io.BytesIO(b''))
        return accepted()
    mail=mail_at(tmp_path,transport,clock)
    queue=GraphSyncQueue(Graph(tmp_path),tmp_path/'queue.sqlite3',mail_service=mail,auto_start=False)
    job=enqueue(queue,tmp_path)
    queue.run_until_idle()
    first=queue.status(job['id'])
    assert first['result']['phase']=='mail_rejected' and first['will_retry']
    assert not first['reconciliation_required'] and first['next_attempt_at']>=deadline
    assert first['result']['retry_not_before']==deadline
    original=next(req.data for req in calls if req.full_url.endswith('/sendMail'))
    second=enqueue(queue,tmp_path,'mail:two')
    before=len(calls)
    def parallel_session(_):
        other=mail_at(tmp_path,transport,clock)
        with pytest.raises(ProviderWaitError):
            other._request_bytes('https://graph.microsoft.com/v1.0/users/sender@example.invalid/sendMail',
                                 method='POST',data=original)
    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(parallel_session, range(10)))
    restarted=GraphSyncQueue(Graph(tmp_path),queue.database_path,
        mail_service=mail_at(tmp_path,transport,clock),auto_start=False)
    with pytest.raises(ProviderWaitError):
        restarted.retry(job['id'])
    clock[0]=deadline-1
    restarted.run_until_idle()
    assert len(calls)==before
    assert restarted.status(job['id'])['attempts']==1
    assert restarted.status(second['id'])['attempts']==0
    assert restarted._next_retry_delay()==pytest.approx(1)
    clock[0]=deadline
    reject[0]=False
    restarted.run_until_idle()
    assert restarted.status(job['id'])['status']=='complete'
    assert restarted.status(second['id'])['status']=='complete'
    sends=[req.data for req in calls if req.full_url.endswith('/sendMail')]
    assert sends.count(original)==2 and len(sends)==3


@pytest.mark.parametrize('failure', ['503', '503_store_failure', '500', 'timeout'])
def test_ambiguous_send_never_becomes_retryable_even_with_provider_pause(tmp_path,clock,monkeypatch,failure):
    calls=[]
    def transport(req, **_):
        if req.full_url.endswith('/sendMail'):
            calls.append(req)
            if failure=='timeout':
                raise TimeoutError('synthetic')
            raise HTTPError(req.full_url,500 if failure=='500' else 503,'synthetic',
                            {'Retry-After':'900'},io.BytesIO(b''))
        return accepted()
    mail=mail_at(tmp_path,transport,clock)
    if failure=='503_store_failure':
        def fail(*_,**__):
            raise OSError('Synthetic store failure')
        monkeypatch.setattr(mail.provider_wait_store,'record',fail)
    queue=GraphSyncQueue(Graph(tmp_path),tmp_path/'queue.sqlite3',mail_service=mail,auto_start=False)
    job=enqueue(queue,tmp_path)
    queue.run_until_idle()
    state=queue.status(job['id'])
    assert state['result']['phase']=='mail_unknown'
    assert state['reconciliation_required'] and not state['will_retry']
    if failure=='503':
        assert mail.provider_pause()['deadline']==clock[0]+900
    clock[0]+=901
    restarted=GraphSyncQueue(Graph(tmp_path),queue.database_path,
        mail_service=mail_at(tmp_path,transport,clock),auto_start=False)
    restarted.run_until_idle()
    with pytest.raises(ValueError,match='reconciliação'):
        restarted.retry(job['id'])
    assert len(calls)==1


@pytest.mark.parametrize('status', [429,503])
def test_token_pause_is_shared_with_storage_and_no_send_is_attempted(tmp_path,clock,status):
    calls=[]
    def transport(req, **_):
        calls.append(req)
        assert not req.full_url.endswith('/sendMail')
        raise HTTPError(req.full_url,status,'synthetic',{'Retry-After':'900'},io.BytesIO(b''))
    mail=mail_at(tmp_path,transport,clock)
    queue=GraphSyncQueue(Graph(tmp_path),tmp_path/'queue.sqlite3',mail_service=mail,auto_start=False)
    job=enqueue(queue,tmp_path)
    queue.run_until_idle()
    state=queue.status(job['id'])
    assert state['result']['phase']=='mail_rejected' and not state['reconciliation_required']
    storage=GraphStorageService(GraphConfig('synthetic','synthetic','synthetic','library'),
        provider_wait_store=mail.provider_wait_store)
    assert storage.provider_pause()['provider']=='application'
    with pytest.raises(ProviderWaitError):
        storage._request_bytes('https://graph.microsoft.com/v1.0/drives/library',method='GET')
    assert len(calls)==1


def test_storage_token_pause_blocks_mail_but_mail_pause_does_not_block_storage(tmp_path,clock,monkeypatch):
    calls=[]
    mail=mail_at(tmp_path,lambda *a,**k:pytest.fail('No mail request before deadline'),clock)
    storage=GraphStorageService(GraphConfig('synthetic','synthetic','synthetic','library'),
        provider_wait_store=mail.provider_wait_store)
    mail.provider_wait_store.record(mail.provider_scopes[0],http_status=429,retry_after='120')
    def transport(req, **_):
        calls.append(req)
        if 'login.microsoftonline.com' in req.full_url:
            raise HTTPError(req.full_url,503,'synthetic',{'Retry-After':'900'},io.BytesIO(b''))
        return io.BytesIO(b'ok')
    monkeypatch.setattr('src.services.graph_storage_service.request.urlopen',transport)
    assert storage._request_bytes('https://graph.microsoft.com/v1.0/drives/library',method='GET',authenticated=False)==b'ok'
    with pytest.raises(ProviderWaitError):
        storage._request_bytes(TOKEN_URL_TEMPLATE.format(tenant_id='synthetic'),method='POST',authenticated=False)
    with pytest.raises(ProviderWaitError) as failure:
        mail.test_connection()
    assert failure.value.pause['provider']=='application' and len(calls)==2


def test_pause_arriving_during_token_acquisition_prevents_the_send_post(tmp_path,clock,monkeypatch):
    calls=[]
    mail=mail_at(tmp_path,lambda *a,**k:calls.append(a) or accepted(),clock)
    def token():
        mail.provider_wait_store.record(mail.provider_scopes[0],http_status=429,retry_after='900')
        return 'synthetic'
    monkeypatch.setattr(mail,'_access_token_value',token)
    queue=GraphSyncQueue(Graph(tmp_path),tmp_path/'queue.sqlite3',mail_service=mail,auto_start=False)
    job=enqueue(queue,tmp_path)
    queue.run_until_idle()
    state=queue.status(job['id'])
    assert calls==[] and state['will_retry'] and not state['reconciliation_required']
    assert state['result']['phase']=='mail_rejected' and state['result']['mail_snapshot']


@pytest.mark.parametrize('header', ['9'*64, '9'*65, '315360000'])
def test_anomalous_mail_wait_suspends_other_pending_jobs_without_consuming_attempts(tmp_path,clock,header):
    calls=[]
    def transport(req, **_):
        if req.full_url.endswith('/sendMail'):
            calls.append(req)
            raise HTTPError(req.full_url,429,'synthetic',{'Retry-After':header},io.BytesIO(b''))
        return accepted()
    mail=mail_at(tmp_path,transport,clock)
    queue=GraphSyncQueue(Graph(tmp_path),tmp_path/'queue.sqlite3',mail_service=mail,auto_start=False)
    job=enqueue(queue,tmp_path)
    queue.run_until_idle()
    another=enqueue(queue,tmp_path,'mail:second')
    queue.run_until_idle()
    state=queue.status(job['id'])
    assert not state['will_retry'] and not state['reconciliation_required']
    assert mail.provider_pause()['suspended'] and queue._next_retry_delay() is None
    assert queue.status(another['id'])['attempts']==0 and len(calls)==1
    with pytest.raises(ProviderWaitError):
        queue.retry(job['id'])


def test_legacy_typed_900_second_delay_is_not_capped_and_manual_retry_cannot_bypass_it(tmp_path,clock,monkeypatch):
    mail=mail_at(tmp_path,lambda *a,**k:pytest.fail('No transport'),clock)
    def refuse(*_,**__):
        raise GraphMailTransientError('Synthetic refusal',retry_after_seconds=900)
    monkeypatch.setattr(mail,'send_delivery',refuse)
    queue=GraphSyncQueue(Graph(tmp_path),tmp_path/'queue.sqlite3',mail_service=mail,auto_start=False)
    job=enqueue(queue,tmp_path)
    queue.run_until_idle()
    state=queue.status(job['id'])
    assert state['next_attempt_at']==clock[0]+900
    assert state['result']['retry_not_before']==clock[0]+900
    with pytest.raises(ValueError):
        queue.retry(job['id'])
