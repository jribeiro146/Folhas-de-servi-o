"""Persist storage pause policy through a simulated memory loss and local recovery."""
import io
from contextlib import nullcontext
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from src.services import provider_waits
from src.services.provider_waits import ProviderWaitStore
from src.services.graph_sync_queue import GraphSyncQueue
from tests.test_mail_provider_waits import clock
from tests.test_provider_wait_integration import graph_with_clock
from tests.test_own_publication_state import publication
from tests.test_operational_admin import ADMIN,login,configured_synthetic_admin
import src.web.application as web


@pytest.mark.parametrize('header',['900','9'*65])
@pytest.mark.parametrize('local_pending',[False,True])
def test_storage_job_retains_its_wait_after_memory_loss_and_local_recovery(
        publication,tmp_path,clock,monkeypatch,header,local_pending):
    source,editing,actor,before,metadata,publisher,queue=publication
    storage=graph_with_clock(tmp_path,clock)
    monkeypatch.setattr(storage,'_access_token_value',lambda:'synthetic')
    requests=[]
    def transport(req,**_):
        requests.append(req)
        raise HTTPError(req.full_url,429,'synthetic',{'Retry-After':header},io.BytesIO(b''))
    monkeypatch.setattr('src.services.graph_storage_service.request.urlopen',transport)
    def fail(_db,**_): raise OSError('Synthetic guard write failure')
    monkeypatch.setattr(ProviderWaitStore,'_audit',staticmethod(fail))
    upload=publisher.upload_active_bundle
    def refused(*args,**kwargs):
        upload(*args,**kwargs)  # already-confirmed metadata must still recover locally
        storage._request_bytes('https://graph.microsoft.com/v1.0/synthetic',method='PUT',data=b'synthetic')
    publisher.upload_active_bundle=refused
    publisher.provider_pause=storage.provider_pause
    job=queue.enqueue('upload_active',{'draft_path':str(source)})
    deadline=clock[0]+900
    with editing.operation_guard(before['document_id'],actor) if local_pending else nullcontext():
        queue.run_until_idle()
    state=queue.status(job['id'])
    assert len(requests)==1
    if local_pending:
        assert state['result']['phase']=='publication_local_pending'
        assert state['result']['publication_remote_failure']['provider_deadline_unrecordable'] is (header!='900')
    # Only the memory is lost. Reopen the persisted job/store as another worker.
    monkeypatch.setattr(provider_waits,'_deferred_waits',{})
    reopened=GraphSyncQueue(publisher,queue.database_path,auto_start=False)
    reopened.editing_state_service=editing
    if local_pending:
        with reopened._connect() as db:
            db.execute('UPDATE graph_sync_jobs SET next_attempt_at=0 WHERE id=?',(job['id'],))
        reopened.run_until_idle()
    state=reopened.status(job['id'])
    assert len(requests)==1 and publisher.calls==1
    if header=='900':
        assert state['result']['retry_not_before']==deadline and state['will_retry']
        with pytest.raises(ValueError,match='prazo'): reopened.retry(job['id'])
        clock[0]=deadline-1
        reopened.run_until_idle()
        assert len(requests)==1
    else:
        assert state['result']['invalid_state'] and not state['will_retry']
        with pytest.raises(ValueError,match='inválidos'): reopened.retry(job['id'])
        monkeypatch.setattr(web,'AUTH_ENABLED',True)
        monkeypatch.setattr(web,'ACTIVE_AUTH_PROVIDER','microsoft')
        class Auth:
            def user_from_session(self,payload): return SimpleNamespace(**payload) if payload else None
        app=web.create_app(editing_state_service=editing,graph_sync_queue=reopened,microsoft_auth_service=Auth())
        admin=login(app,ADMIN.email)
        shown=admin.get('/api/admin/overview').json['jobs'][0]
        assert shown['invalid_state'] and not shown['can_retry']
        response=admin.post('/api/admin/jobs/'+job['id']+'/retry',json={'reason':'Synthetic review'},
                            headers={'X-Admin-CSRF':'synthetic-csrf'})
        assert response.status_code==409 and len(requests)==1


def test_response_is_closed_when_even_emergency_memory_cannot_record(tmp_path,clock,monkeypatch):
    storage=graph_with_clock(tmp_path,clock)
    monkeypatch.setattr(storage,'_access_token_value',lambda:'synthetic')
    body=io.BytesIO(b'synthetic')
    calls=[]
    def transport(req,**_):
        calls.append(req)
        raise HTTPError(req.full_url,429,'synthetic',{'Retry-After':'900'},body)
    monkeypatch.setattr('src.services.graph_storage_service.request.urlopen',transport)
    def fail(*_,**__): raise OSError('Synthetic emergency failure')
    monkeypatch.setattr(storage.provider_wait_store,'record',fail)
    monkeypatch.setattr(storage.provider_wait_store,'defer',fail)
    with pytest.raises(OSError): storage._request_bytes('https://graph.microsoft.com/v1.0/synthetic',method='GET')
    assert len(calls)==1 and body.closed
