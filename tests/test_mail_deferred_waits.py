"""Storage-write failure containment; no real Microsoft requests."""
import io
import sqlite3
from urllib.error import HTTPError

import pytest

from src.services.graph_sync_queue import GraphSyncQueue
from src.services.provider_waits import ProviderWaitStore,ProviderWaitError
from tests.test_mail_provider_waits import clock,mail_at,enqueue,accepted
from tests.test_durable_mail import Graph
from tests.test_maintenance_access import synthetic_production_sadi,login,lease
from tests.test_maintenance import complete_document


@pytest.mark.parametrize('stage', ['token','send','send503'])
@pytest.mark.parametrize('error_type', [OSError,sqlite3.OperationalError])
def test_unrecorded_wait_stops_other_jobs_and_instances_then_recovers_exact_deadline(
        tmp_path,clock,monkeypatch,stage,error_type):
    calls=[]
    def transport(req,**_):
        calls.append(req)
        if stage=='token' or req.full_url.endswith('/sendMail'):
            raise HTTPError(req.full_url,503 if stage=='send503' else 429,'synthetic',
                            {'Retry-After':'900'},io.BytesIO(b''))
        return accepted()
    mail=mail_at(tmp_path,transport,clock)
    original_audit=ProviderWaitStore._audit
    def fail(_db,**_):
        raise error_type('Synthetic wait storage failure')
    monkeypatch.setattr(ProviderWaitStore,'_audit',staticmethod(fail))
    queue=GraphSyncQueue(Graph(tmp_path),tmp_path/'queue.sqlite3',mail_service=mail,auto_start=False)
    first=enqueue(queue,tmp_path,'mail:a')
    second=enqueue(queue,tmp_path,'mail:b')
    queue.run_until_idle()
    count=len(calls)
    assert count==(1 if stage=='token' else 2)
    assert queue.status(second['id'])['attempts']==0
    original=queue.status(first['id'])
    assert original['result']['phase']==('mail_unknown' if stage=='send503' else 'mail_rejected')
    third=enqueue(queue,tmp_path,'mail:c')
    other=mail_at(tmp_path,transport,clock)
    second_queue=GraphSyncQueue(Graph(tmp_path),queue.database_path,mail_service=other,auto_start=False)
    second_queue.run_until_idle()
    assert len(calls)==count and second_queue.status(third['id'])['attempts']==0
    deadline=clock[0]+900
    assert other.provider_pause()['deadline']==deadline
    with pytest.raises(ProviderWaitError):
        other.provider_wait_store.require_ready(other.provider_scopes)
    # Repair writes and recover the original absolute deadline, without
    # extending it by a new 900 seconds at the time of recovery.
    monkeypatch.setattr(ProviderWaitStore,'_audit',staticmethod(original_audit))
    clock[0]+=10
    assert other.provider_pause()['deadline']==deadline
    with other.provider_wait_store._connect() as db:
        assert db.execute('SELECT COUNT(*) FROM provider_waits').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM provider_wait_actions').fetchone()[0]==1
    clock[0]=deadline-1
    second_queue.run_until_idle()
    assert len(calls)==count and second_queue.status(second['id'])['attempts']==0


def test_token_write_failure_blocks_later_http_finalization_in_another_instance(
        tmp_path,clock,monkeypatch,verified_test_release):
    calls=[]
    def transport(req,**_):
        calls.append(req)
        assert '/oauth2/' in req.full_url
        raise HTTPError(req.full_url,429,'synthetic',{'Retry-After':'900'},io.BytesIO(b''))
    def fail(_db,**_):
        raise sqlite3.OperationalError('Synthetic wait write failure')
    monkeypatch.setattr(ProviderWaitStore,'_audit',staticmethod(fail))
    mail=mail_at(tmp_path,transport,clock)
    queue=GraphSyncQueue(Graph(tmp_path),tmp_path/'first-queue.sqlite3',mail_service=mail,auto_start=False)
    enqueue(queue,tmp_path)
    queue.run_until_idle()
    assert len(calls)==1
    app,client,draft,editing,root=synthetic_production_sadi(tmp_path,monkeypatch,
        {'mail_service':mail_at(tmp_path,transport,clock)},verified_test_release)
    login(client,'outro@sensorpoint.pt')
    metadata=lease(client,draft,'after-write-failure')
    document=complete_document()
    document['maintenance_checklists']=[]
    document['customer_email']='customer@example.invalid'
    saved=client.post(f'/api/file/{draft.stem}/autosave',json={'document':document,'_edit':metadata})
    assert saved.status_code==200,saved.json
    metadata.update(base_revision=saved.json['editing']['revision'],idempotency_key='after-wait-send')
    response=client.post(f'/api/file/{draft.stem}/send',json={**document,'_edit':metadata})
    assert response.status_code==503 and response.json['code']=='provider_wait'
    assert len(calls)==1 and draft.exists() and not list((root/'archive').rglob('*.xlsx'))
    assert editing.snapshot(draft)['server_document']
    assert app.extensions['test_graph_queue'].summary()['pending']==0


@pytest.mark.parametrize('response', [b'not JSON', b'{}'])
def test_malformed_token_is_known_failure_without_send(tmp_path,clock,response):
    calls=[]
    def transport(req,**_):
        calls.append(req)
        return io.BytesIO(response)
    mail=mail_at(tmp_path,transport,clock)
    queue=GraphSyncQueue(Graph(tmp_path),tmp_path/'queue.sqlite3',mail_service=mail,auto_start=False)
    job=enqueue(queue,tmp_path)
    queue.run_until_idle()
    state=queue.status(job['id'])
    assert state['result']['phase']=='mail_rejected' and state['will_retry']
    assert not state['reconciliation_required']
    assert len(calls)==1 and '/oauth2/' in calls[0].full_url


@pytest.mark.parametrize('header', ['90000','9'*65])
def test_deferred_suspension_is_not_lost_or_resumable_before_it_is_durable(tmp_path,clock,monkeypatch,header):
    store=ProviderWaitStore(tmp_path/'waits.sqlite3',clock=lambda:clock[0])
    scope=mail_at(tmp_path,lambda *_:None,clock).provider_scopes[0]
    original_audit=ProviderWaitStore._audit
    def fail(_db,**_):
        raise sqlite3.OperationalError('Synthetic wait write failure')
    monkeypatch.setattr(ProviderWaitStore,'_audit',staticmethod(fail))
    value=store.defer(scope,http_status=429,retry_after=header)
    clock[0]+=90001
    assert store.blocking([scope])['suspended']
    assert not store.overview()[0]['can_resume']
    monkeypatch.setattr(ProviderWaitStore,'_audit',staticmethod(original_audit))
    clock[0]+=10
    recovered=store.blocking([scope])
    assert recovered['suspended'] and recovered['deadline']==value['deadline']
    assert store.overview()[0]['can_resume'] is (header=='90000')
