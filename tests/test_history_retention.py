from contextlib import closing
import hashlib
import json
import sqlite3

import pytest

from src.services.sqlite_storage_format import initialize_store, StorageFormatError
from tools.history_retention import simulate, _read


NOW = 2_000_000_000


def stores(tmp_path):
    queue, editing = tmp_path/'queue.sqlite3', tmp_path/'editing.sqlite3'
    for path, kind in ((queue,'queue'),(editing,'editing')):
        with closing(sqlite3.connect(path)) as db:
            initialize_store(db,kind)
    return queue,editing


def insert(db, identifier, *, age=400, kind='upload_active', status='complete', payload=None, result=None, stamp=None):
    db.execute('INSERT INTO graph_sync_jobs (id,kind,payload_json,status,next_attempt_at,result_json,created_at,updated_at) '
        'VALUES (?,?,?,?,0,?,?,?)',(identifier,kind,json.dumps({'draft_path':'synthetic.xlsx'} if payload is None else payload),
        status,json.dumps({'uploaded_files':['synthetic.xlsx']} if result is None else result),NOW-age*86400,
        NOW-age*86400 if stamp is None else stamp))


def test_age_simulation_keeps_receipts_ids_uncertainty_and_audit_bytes(tmp_path):
    queue,editing = stores(tmp_path)
    with closing(sqlite3.connect(queue)) as db:
        insert(db,'old');insert(db,'exact-90',age=90);insert(db,'young',age=1)
        for status in ('pending','running','held','failed','unknown'):
            insert(db,status,status=status)
        for flag in ('migration_uncertain','restore_uncertain','reconciliation_hold','invalid_state'):
            insert(db,flag,result={flag:True})
        for phase in ('sending_mail','mail_unknown','sending_teams','teams_unknown'):
            insert(db,phase,result={'phase':phase})
        insert(db,'receipt',kind='archive_and_remove',result={'mail':{'accepted':True}})
        insert(db,'teams',kind='notify_teams',result={'accepted':True})
        insert(db,'cancel',kind='remove_active',result={'cancellation_started':True})
        insert(db,'superseded',result={'superseded_by':'old'})
        insert(db,'malformed',result=[]);insert(db,'future',stamp=NOW+1)
        insert(db,'bad-date',stamp='invalid')
        db.execute('INSERT INTO graph_mail_messages VALUES (?,?,?,?,?,?,?,?,?)',
                   ('receipt',None,'synthetic','synthetic','','a'*64,12,'synthetic',NOW-400*86400))
        db.execute('INSERT INTO graph_mail_messages VALUES (?,?,?,?,?,?,?,?,?)',
                   ('orphan',b'uncertain bytes','synthetic','synthetic','','b'*64,15,'synthetic',None))
        db.commit()
    with closing(sqlite3.connect(editing)) as db:
        db.execute('INSERT INTO admin_actions (actor_email,action,target,reason,details_json,created_at) '
                   'VALUES (?,?,?,?,?,?)',('admin@example.invalid','retry','synthetic','synthetic','{}',NOW-400*86400))
        db.commit()
    before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (queue,editing)]
    report = simulate(queue,editing_path=editing,now=NOW)
    assert report['deletion_candidates'] == report['audit']['deletion_candidates'] == 0
    assert report['protected_job_ids'] == 23
    assert report['plain_uploads_older_than'] == {'90':1,'180':1,'365':1}
    assert report['protected_reasons']['plain_upload_idempotency'] == 3
    assert report['protected_reasons']['unfinished_or_failed'] == 5
    assert report['protected_reasons']['malformed_or_future'] == 3
    assert report['mail_snapshots'] == {'protected_rows':2,'protected_blob_bytes':15,'already_retired_rows':1}
    assert report['audit']['protected_rows'] == 1
    assert report['audit']['older_than'] == {'90':1,'180':1,'365':1}
    assert before == [hashlib.sha256(p.read_bytes()).hexdigest() for p in (queue,editing)]
    # Read-only is enforced by SQLite, not a promise made by the reporting code.
    with closing(_read(queue,'queue')) as db:
        with pytest.raises(sqlite3.OperationalError):
            db.execute('DELETE FROM graph_sync_jobs')


def test_missing_and_unknown_databases_are_not_created_or_modified(tmp_path):
    missing=tmp_path/'missing.sqlite3'
    with pytest.raises(sqlite3.OperationalError):
        simulate(missing,now=NOW)
    assert not missing.exists()
    unknown=tmp_path/'unknown.sqlite3'
    with closing(sqlite3.connect(unknown)) as db:
        db.execute('CREATE TABLE other (value TEXT)');db.commit()
    original=unknown.read_bytes()
    with pytest.raises(StorageFormatError):
        simulate(unknown,now=NOW)
    assert unknown.read_bytes()==original


@pytest.mark.parametrize('days,now',[((),NOW),((0,),NOW),((90.0,),NOW),((90,),float('inf')),((90,),True)])
def test_invalid_simulation_options_are_refused_before_opening(tmp_path,days,now):
    with pytest.raises(ValueError):
        simulate(tmp_path/'absent',days=days,now=now)
    assert not (tmp_path/'absent').exists()
