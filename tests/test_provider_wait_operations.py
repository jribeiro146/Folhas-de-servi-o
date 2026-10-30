"""Real HTTP cancellation boundaries; Microsoft is replaced by a local transport."""
import io
import time
from urllib.error import HTTPError

from src.services.graph_storage_service import GraphStorageService
from src.services.provider_waits import ProviderWaitStore
from tests.test_cancellation_recovery import cancellation_http


def test_cancellation_429_releases_operation_preserves_source_and_can_be_retried_after_deadline(cancellation_http,monkeypatch):
    client,editing,queue,source,body,root=cancellation_http
    graph=queue.graph_service
    now=[time.time()]
    graph._provider_wait_store=ProviderWaitStore(root/'waits.sqlite3',clock=lambda:now[0])
    monkeypatch.setattr(graph,'_get_item_by_path',GraphStorageService._get_item_by_path.__get__(graph))
    monkeypatch.setattr(graph,'_access_token_value',lambda:'synthetic')
    calls=[]
    def transport(req,timeout):
        calls.append(req.full_url)
        raise HTTPError(req.full_url,429 if len(calls)==1 else 404,'synthetic',
                        {'Retry-After':'120'} if len(calls)==1 else {},io.BytesIO(b'synthetic'))
    monkeypatch.setattr('src.services.graph_storage_service.request.urlopen',transport)
    original=source.read_bytes()
    url=f'/api/file/{source.stem}/cancel'
    for _ in range(2):
        response=client.post(url,json=body)
        assert response.status_code==503 and response.json['code']=='provider_wait'
        assert source.read_bytes()==original
        assert editing.operation(body['_edit']['document_id'],'cancel-1')['status']=='failed'
    assert len(calls)==1 and queue.summary()['pending']==0
    now[0]+=120
    response=client.post(url,json=body)
    assert response.status_code==200,response.json
    assert len(calls)==2
    queue.run_until_idle()
    assert queue.status(response.json['graph_job_id'])['status']=='complete'
    assert graph.writes==[]
    assert client.post(url,json=body).json==response.json
    assert len(list((root/'Canceladas').rglob('*.xlsx')))==1
