"""Full finalization and pre-POST failures, with fictitious identity and I/O."""
import base64
import io
import json
import sqlite3
from urllib.error import HTTPError

import pytest

from src.services.graph_sync_queue import GraphSyncQueue
from src.services.provider_waits import ProviderWaitError
from tests.test_mail_provider_waits import clock,mail_at,enqueue,accepted
from tests.test_durable_mail import Graph
from tests.test_maintenance_access import synthetic_production_sadi,login,lease
from tests.test_maintenance import complete_document


@pytest.mark.parametrize('scope,seconds,cached,status',[
    ('mail','900',False,200),('mail','90000',False,200),
    ('application','900',False,503),('application','900',True,200),
    ('application','90000',True,200),
])
def test_finalization_archives_while_mail_waits_but_respects_required_token_preflight(
        tmp_path,clock,monkeypatch,verified_test_release,scope,seconds,cached,status):
    token='synthetic.'+base64.urlsafe_b64encode(json.dumps({'roles':['Mail.Send']}).encode()).decode()+'.synthetic'
    calls=[]
    def transport(req,**_):
        calls.append(req)
        assert '/oauth2/v2.0/token' in req.full_url, 'No sendMail before the deadline'
        return io.BytesIO(json.dumps({'access_token':token,'expires_in':3600}).encode())
    mail=mail_at(tmp_path,transport,clock)
    if cached:
        mail._access_token=token
        mail._access_token_expires_at=clock[0]+3600
    mail.provider_wait_store.record(mail.provider_scopes[0 if scope=='mail' else 1],
        http_status=429,retry_after=seconds)
    app,client,draft,editing,root=synthetic_production_sadi(tmp_path,monkeypatch,
        {'mail_service':mail},verified_test_release)
    login(client,'outro@sensorpoint.pt')
    metadata=lease(client,draft,'mail-wait-finalization')
    document=complete_document()
    document['maintenance_checklists']=[]
    document['customer_email']='customer@example.invalid'
    assert client.get('/api/files?refresh=0').status_code==200
    saved=client.post(f'/api/file/{draft.stem}/autosave',json={'document':document,'_edit':metadata})
    assert saved.status_code==200,saved.json
    metadata['base_revision']=saved.json['editing']['revision']
    metadata['idempotency_key']='mail-wait-send'
    payload={**document,'_edit':metadata}
    response=client.post(f'/api/file/{draft.stem}/send',json=payload)
    assert response.status_code==status,response.json
    queue=app.extensions['test_graph_queue']
    if status==200:
        assert response.json['email_status']=='pending'
        assert len(list((root/'archive').rglob('*.xlsx')))==1
        job=queue.status(response.json['graph_job_id'])
        assert job['kind']=='archive_and_remove' and job['status'] in {'held','pending'}
        queue.run_until_idle()
        assert queue.status(job['id'])['attempts']==0 and queue.status(job['id'])['status']=='pending'
        repeated=client.post(f'/api/file/{draft.stem}/send',json=payload)
        assert repeated.status_code==200 and repeated.json==response.json
    else:
        assert response.json['code']=='provider_wait' and draft.exists()
        assert not list((root/'archive').rglob('*.xlsx'))
        assert queue.summary()['pending']==0
        state=editing.snapshot(draft)
        assert state['server_document']['customer_email']=='customer@example.invalid'
    assert len(calls)==(1 if scope=='mail' and not cached else 0)


@pytest.mark.parametrize('failure', ['guard','lazy_store','token_record','send_refusal_record'])
@pytest.mark.parametrize('error_type', [OSError,sqlite3.OperationalError])
def test_failures_known_before_send_do_not_create_unknown_delivery(tmp_path,clock,monkeypatch,failure,error_type):
    calls=[]
    def transport(req,**_):
        calls.append(req)
        if failure=='token_record' or req.full_url.endswith('/sendMail'):
            raise HTTPError(req.full_url,429,'synthetic',{'Retry-After':'900'},io.BytesIO(b''))
        return accepted()
    mail=mail_at(tmp_path,transport,clock)
    def fail(*_,**__):
        raise error_type('Synthetic storage failure')
    if failure=='guard':
        monkeypatch.setattr(mail.provider_wait_store,'require_ready',fail)
    elif failure=='lazy_store':
        # Claim saw a healthy store; construction fails only in the send phase.
        monkeypatch.setattr(mail,'provider_pause',lambda:None)
        mail._provider_wait_store=None
        monkeypatch.setattr('src.services.graph_mail_service.ProviderWaitStore',fail)
    else:
        monkeypatch.setattr(mail.provider_wait_store,'record',fail)
    queue=GraphSyncQueue(Graph(tmp_path),tmp_path/'queue.sqlite3',mail_service=mail,auto_start=False)
    job=enqueue(queue,tmp_path)
    queue.run_until_idle()
    state=queue.status(job['id'])
    assert state['result']['phase']=='mail_rejected' and not state['reconciliation_required']
    assert state['result']['mail_snapshot']
    sends=[req for req in calls if req.full_url.endswith('/sendMail')]
    assert len(sends)==(1 if failure=='send_refusal_record' else 0)
    if failure.endswith('record'):
        assert not state['will_retry']  # storage repair and review are required
        assert state['result']['retry_not_before']==clock[0]+900
        with pytest.raises((ValueError,ProviderWaitError),match='prazo'):
            queue.retry(job['id'])
        clock[0]+=900
        assert queue.retry(job['id'])['status']=='pending'
    else:
        assert state['will_retry']


@pytest.mark.parametrize('failure', ['token','send'])
def test_unrecordable_unbounded_refusal_preserves_message_and_blocks_manual_retry(tmp_path,clock,monkeypatch,failure):
    calls=[]
    def transport(req,**_):
        calls.append(req)
        if failure=='send' and '/oauth2/' in req.full_url:
            return accepted()
        raise HTTPError(req.full_url,429,'synthetic',{'Retry-After':'9'*65},io.BytesIO(b''))
    mail=mail_at(tmp_path,transport,clock)
    def fail(*_,**__):
        raise sqlite3.OperationalError('Synthetic write failure')
    monkeypatch.setattr(mail.provider_wait_store,'record',fail)
    queue=GraphSyncQueue(Graph(tmp_path),tmp_path/'queue.sqlite3',mail_service=mail,auto_start=False)
    job=enqueue(queue,tmp_path)
    queue.run_until_idle()
    state=queue.status(job['id'])
    assert state['result']['mail_snapshot'] and state['result']['invalid_state']
    assert not state['will_retry'] and not state['reconciliation_required']
    assert state['result']['phase']=='mail_rejected'
    with pytest.raises(ValueError,match='inválidos'):
        queue.retry(job['id'])
    assert len(calls)==(2 if failure=='send' else 1)
    assert sum(req.full_url.endswith('/sendMail') for req in calls)==(1 if failure=='send' else 0)
