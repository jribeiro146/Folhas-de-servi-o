"""Provider pauses are tested against local SQLite with invented scopes only."""
from concurrent.futures import ThreadPoolExecutor
from email.utils import formatdate
import json

import pytest

from src.services.provider_waits import (
    ProviderWaitStore, ProviderWaitError, provider_scope, parse_retry_after,
)


@pytest.mark.parametrize('header,delay,source', [('120',120,'seconds'),(None,2,'missing'),
    ('NaN',2,'invalid'),('-1',2,'invalid'),('1.5',2,'invalid'),('',2,'missing')])
def test_parse_normal_and_invalid_waits(header, delay, source):
    result = parse_retry_after(header, now=1700000000, fallback=2)
    assert result == {'deadline':1700000000+delay,'suspended':False,'source':source}


def test_http_dates_and_past_dates():
    now=1700000000
    assert parse_retry_after(formatdate(now+900,usegmt=True),now=now,fallback=2)['deadline']==now+900
    assert parse_retry_after(formatdate(now-10,usegmt=True),now=now,fallback=2)['deadline']==now
    assert parse_retry_after('Tue, 14 Nov 2023 22:13:20',now=now,fallback=2)['source']=='invalid'


@pytest.mark.parametrize('header', ['9'*64,'9'*1000,formatdate(1700000000+10*365*86400,usegmt=True)])
def test_very_large_waits_are_suspended_not_shortened(header):
    result=parse_retry_after(header,now=1700000000,fallback=2)
    assert result['suspended']
    assert result['deadline'] is None or result['deadline']>1700000000+86400


def test_ten_instances_keep_the_greatest_deadline_across_restart(tmp_path):
    clock=lambda:1700000000
    path=tmp_path/'waits.sqlite3'
    scope=provider_scope('storage','synthetic-tenant','synthetic-client','synthetic-library')
    stores=[ProviderWaitStore(path,clock=clock) for _ in range(10)]
    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(lambda item:item[1].record(scope,http_status=429,retry_after=str((item[0]+1)*120)),enumerate(stores)))
    reopened=ProviderWaitStore(path,clock=clock)
    with pytest.raises(ProviderWaitError) as error:
        reopened.require_ready([scope])
    assert error.value.retry_not_before==1700001200
    assert len(reopened.history())==10
    assert reopened.blocking([provider_scope('mail','synthetic-tenant','synthetic-client','sender@example.invalid')]) is None


def test_scope_wait_survives_new_request_and_expires_by_server_clock(tmp_path):
    now=[1700000000.0]
    store=ProviderWaitStore(tmp_path/'waits.sqlite3',clock=lambda:now[0])
    scope=provider_scope('storage','tenant','app','library')
    store.record(scope,http_status=503,retry_after='120')
    now[0]+=119
    with pytest.raises(ProviderWaitError): store.require_ready([scope])
    now[0]+=1
    store.require_ready([scope])
    assert store.overview()[0]['blocked'] is False


def test_admin_decision_is_versioned_audited_and_never_anticipates_deadline(tmp_path,monkeypatch):
    now=[1700000000.0]
    store=ProviderWaitStore(tmp_path/'waits.sqlite3',clock=lambda:now[0])
    scope=provider_scope('storage','tenant','app','library')
    before=store.record(scope,http_status=429,retry_after='86401')
    args=dict(expected_version=before['version'],evidence_id=before['evidence_id'],actor='synthetic-admin',reason='Resposta conferida')
    with pytest.raises(ValueError,match='antecipado'): store.resume(scope,**args)
    now[0]+=86402
    assert store.overview()[0]['can_resume']
    audit=store._audit
    def fail_audit(*args,**kwargs): raise OSError('synthetic audit disk failure')
    monkeypatch.setattr(store,'_audit',fail_audit)
    with pytest.raises(OSError): store.resume(scope,**args)
    assert store.blocking([scope])['suspended']
    assert len(store.history())==1
    monkeypatch.setattr(store,'_audit',audit)
    newer=store.record(scope,http_status=429,retry_after='1')
    with pytest.raises(ValueError,match='mudou'): store.resume(scope,**args)
    now[0]+=2
    store.resume(scope,**dict(args,expected_version=newer['version'],evidence_id=newer['evidence_id']))
    assert store.blocking([scope]) is None
    assert store.history()[0]['action']=='provider_wait_resumed'


def test_unknown_or_corrupt_guard_is_not_reset(tmp_path):
    store=ProviderWaitStore(tmp_path/'waits.sqlite3',clock=lambda:1700000000)
    scope=provider_scope('storage','tenant','app','library')
    store.record(scope,http_status=429,retry_after='120')
    with store._connect() as db:
        db.execute('UPDATE provider_waits SET state_json=?', (json.dumps({'bad':'state'}),))
    with pytest.raises(ValueError): store.require_ready([scope])


def test_failed_audit_cannot_leave_an_unaudited_wait(tmp_path,monkeypatch):
    store=ProviderWaitStore(tmp_path/'waits.sqlite3',clock=lambda:1700000000)
    scope=provider_scope('storage','tenant','app','library')
    original=store.record(scope,http_status=429,retry_after='120')
    def fail(*args,**kwargs): raise OSError('synthetic audit failure')
    monkeypatch.setattr(store,'_audit',fail)
    with pytest.raises(OSError): store.record(scope,http_status=429,retry_after='900')
    assert store.blocking([scope])==original
    assert len(store.history())==1


def test_unrelated_episodes_reset_backoff_but_consecutive_retries_still_back_off(tmp_path):
    now=[1700000000.0]
    store=ProviderWaitStore(tmp_path/'waits.sqlite3',clock=lambda:now[0])
    scope=provider_scope('storage','tenant','app','library')
    for _ in range(10):
        pause=store.record(scope,http_status=503,retry_after=None)
        assert pause['deadline']==now[0]+1 and pause['failures']==1
        now[0]+=86400
    first=store.record(scope,http_status=503,retry_after=None)
    now[0]=first['deadline']
    second=store.record(scope,http_status=503,retry_after=None)
    assert second['deadline']==now[0]+2 and second['failures']==2
    now[0]=second['deadline']+301
    fresh=store.record(scope,http_status=503,retry_after=None)
    assert fresh['deadline']==now[0]+1
