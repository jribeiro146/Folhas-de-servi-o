"""Same failed-write containment for Storage and shared token refusals."""
import io
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError

import pytest

from src.services.provider_waits import ProviderWaitStore,ProviderWaitError
from src.services.graph_sync_queue import GraphSyncQueue
from tests.test_provider_wait_integration import graph_with_clock
from tests.test_mail_provider_waits import clock,mail_at,enqueue,accepted
from tests.test_durable_mail import Graph


@pytest.mark.parametrize('stage',['token','GET','PUT'])
@pytest.mark.parametrize('status',[429,503])
@pytest.mark.parametrize('error_type',[OSError,sqlite3.OperationalError])
def test_storage_refusal_write_failure_stops_requests_and_shared_mail_token(
        tmp_path,clock,monkeypatch,stage,status,error_type):
    graph=graph_with_clock(tmp_path,clock)
    calls=[]
    rejecting=[True]
    def transport(req,**_):
        calls.append(req)
        if rejecting[0]:
            raise HTTPError(req.full_url,status,'synthetic',{'Retry-After':'900'},io.BytesIO(b''))
        return accepted()
    monkeypatch.setattr('src.services.graph_storage_service.request.urlopen',transport)
    if stage!='token':
        monkeypatch.setattr(graph,'_access_token_value',lambda:'synthetic')
    original_audit=ProviderWaitStore._audit
    def fail(_db,**_):
        raise error_type('Synthetic wait write failure')
    monkeypatch.setattr(ProviderWaitStore,'_audit',staticmethod(fail))
    url='https://graph.microsoft.com/v1.0/synthetic'
    with pytest.raises(ProviderWaitError) as failure:
        graph._request_bytes(url,method='GET' if stage=='token' else stage)
    assert failure.value.retry_after_seconds==900 and len(calls)==1
    mail=mail_at(tmp_path,transport,clock)
    queue=GraphSyncQueue(Graph(tmp_path),tmp_path/'queue.sqlite3',mail_service=mail,auto_start=False)
    job=enqueue(queue,tmp_path)
    if stage=='token':
        queue.run_until_idle()
        assert queue.status(job['id'])['attempts']==0 and len(calls)==1
    else:
        assert mail.provider_pause() is None  # a storage-only wait does not pause mail
    for _ in range(3):
        with pytest.raises(ProviderWaitError): graph._request_bytes(url,method='GET')
    assert len(calls)==1
    deadline=clock[0]+900
    monkeypatch.setattr(ProviderWaitStore,'_audit',staticmethod(original_audit))
    clock[0]+=10
    assert graph.provider_pause()['deadline']==deadline
    clock[0]=deadline-1
    with pytest.raises(ProviderWaitError): graph._request_bytes(url,method='GET')
    assert len(calls)==1
    clock[0]=deadline
    rejecting[0]=False
    graph._request_bytes(url,method='GET')
    assert len(calls)==(3 if stage=='token' else 2)


@pytest.mark.parametrize('second_reader',[False,True])
def test_newer_deferred_wait_survives_concurrent_recovery(tmp_path,clock,monkeypatch,second_reader):
    store=ProviderWaitStore(tmp_path/'waits.sqlite3',clock=lambda:clock[0])
    scope=mail_at(tmp_path,lambda *_:None,clock).provider_scopes[0]
    first=store.defer(scope,http_status=429,retry_after='900')
    entered=threading.Event()
    release=threading.Event()
    original=store._persist_deferred
    def blocked(value):
        if value['evidence_id']==first['evidence_id']:
            entered.set()
            assert release.wait(5)
        original(value)
    monkeypatch.setattr(store,'_persist_deferred',blocked)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending=pool.submit(store.blocking,[scope])
        try:
            assert entered.wait(5)
            newer=store.defer(scope,http_status=503,retry_after='90000')
            other=pool.submit(store.blocking,[scope]) if second_reader else None
            if other: assert other.result(timeout=5)['suspended']
        finally:
            release.set()
        assert pending.result(timeout=5)['deadline']==newer['deadline']
    result=store.blocking([scope])
    assert result['deadline']==newer['deadline'] and result['suspended']
    with store._connect() as db:
        assert db.execute('SELECT COUNT(*) FROM provider_waits').fetchone()[0]==1


def test_expired_unrecorded_wait_does_not_start_a_new_delay_when_storage_recovers(tmp_path,clock,monkeypatch):
    store=ProviderWaitStore(tmp_path/'waits.sqlite3',clock=lambda:clock[0])
    scope=mail_at(tmp_path,lambda *_:None,clock).provider_scopes[0]
    original=store._persist_deferred
    def fail(_): raise sqlite3.OperationalError('Synthetic write failure')
    monkeypatch.setattr(store,'_persist_deferred',fail)
    before=store.defer(scope,http_status=429,retry_after='900')
    clock[0]+=901
    assert store.blocking([scope]) is None
    monkeypatch.setattr(store,'_persist_deferred',original)
    clock[0]+=10
    assert store.blocking([scope]) is None
    assert store.overview()[0]['deadline']==before['deadline']


def test_overview_of_unrecorded_wait_cannot_resume_an_older_durable_suspension(tmp_path,clock,monkeypatch):
    store=ProviderWaitStore(tmp_path/'waits.sqlite3',clock=lambda:clock[0])
    scope=mail_at(tmp_path,lambda *_:None,clock).provider_scopes[0]
    store.record(scope,http_status=429,retry_after='90000')
    clock[0]+=90001
    def fail(_): raise sqlite3.OperationalError('Synthetic write failure')
    monkeypatch.setattr(store,'_persist_deferred',fail)
    store.defer(scope,http_status=429,retry_after='900')
    displayed=store.overview()[0]
    assert not displayed['can_resume']
    with pytest.raises(ValueError,match='mudou'):
        store.resume(scope,expected_version=displayed['version'],evidence_id=displayed['evidence_id'],
                     actor='synthetic',reason='Synthetic review')
    assert store.blocking([scope])['deadline']==displayed['deadline']
