"""Private SADI publication remains atomic during Windows-like reader denial."""
import os
from pathlib import Path
import pytest

from tests.test_maintenance_access import production_sadi,login,lease
from tests.test_maintenance import complete_document,sign_document


@pytest.mark.parametrize('permanent',[False,True])
def test_private_bundle_retry_is_bounded_and_never_commits_an_incomplete_archive(
        production_sadi,monkeypatch,permanent):
    app,client,draft,editing,root=production_sadi
    login(client,'outro@sensorpoint.pt')
    metadata=lease(client,draft,'private-io')
    document=complete_document()
    sign_document(document,app.secret_key)
    before=draft.read_bytes()
    original=os.rename
    calls=[]
    def contend(source,target):
        if Path(source).parent==root/'private/sadi/pdf' and Path(source).name.startswith('.t-'):
            calls.append((source,target))
            if permanent or len(calls)<3:
                problem=PermissionError('Synthetic private PDF reader')
                problem.winerror=5
                raise problem
        original(source,target)
    monkeypatch.setattr('src.services.atomic_files.os.rename',contend)
    monkeypatch.setattr('src.services.atomic_files.time.sleep',lambda _:None)
    payload={**document,'_edit':metadata}
    response=client.post(f'/api/file/{draft.stem}/send',json=payload)
    queue=app.extensions['test_graph_queue']
    if permanent:
        assert response.status_code==500 and draft.read_bytes()==before and len(calls)==8
        assert not list((root/'archive').rglob('*.xlsx'))
        with queue._connect() as db: assert db.execute('SELECT COUNT(*) FROM graph_sync_jobs').fetchone()[0]==0
    else:
        assert response.status_code==200 and response.json['maintenance_bundle_url'] and len(calls)==3
        assert len(list((root/'private/sadi/pdf').rglob('*.pdf')))==3
        assert len(list((root/'archive').rglob('*.xlsx')))==1
        assert client.post(f'/api/file/{draft.stem}/send',json=payload).json==response.json
        assert len(calls)==3
    assert queue.graph_service.calls==[]
