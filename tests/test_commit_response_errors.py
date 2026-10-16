"""Unexpected guard failures keep the JSON/diagnostic contract and preserve writes."""
import json
import logging
from contextlib import contextmanager
import pytest

from tests.test_editing_quarantine import quarantine_app, replace_stored_state, stored_evidence
from tests.test_diagnostic_privacy import DETAIL, assert_private
from src.logging_config import JsonLogFormatter


@pytest.mark.parametrize('endpoint', ['draft', 'send'])
def test_exception_outside_commit_view_returns_json_and_can_be_retried(quarantine_app, monkeypatch, caplog, endpoint):
    app, service, document_id, path = quarantine_app
    replace_stored_state(service, document_id, app.config['SYNTHETIC_INITIAL_STATE'])
    state = json.loads(app.config['SYNTHETIC_INITIAL_STATE'])
    metadata = dict(document_id=document_id, client_id='tab-1', lease_token=next(iter(state['sessions'])),
                    base_revision=state['revision'], idempotency_key='synthetic-guard-failure')
    client = app.test_client()
    headers = {'X-Mail-Monitor-Scope':client.get('/api/graph/monitor-context').json['scope']}
    original = service.operation_guard
    @contextmanager
    def broken_guard(*args, **kwargs):
        raise OSError(DETAIL)
        yield
    monkeypatch.setattr(service, 'operation_guard', broken_guard)
    before_state, before_file = stored_evidence(service, document_id), path.read_bytes()
    caplog.set_level(logging.ERROR)
    response = client.post(f'/api/file/{path.stem}/{endpoint}', json={'_edit':metadata}, headers=headers)
    assert response.status_code == 500 and response.is_json
    assert response.json['success'] is False and response.json['code'] == 'operation_failed'
    assert response.json['reference'] == response.headers['X-Request-ID']
    assert response.json['reference'] in response.json['error']
    assert_private(response.text)
    assert_private('\n'.join(JsonLogFormatter().format(record) for record in caplog.records))
    assert stored_evidence(service, document_id) == before_state and path.read_bytes() == before_file
    receipt = client.get(f'/api/editing/operations/{document_id}/{metadata["idempotency_key"]}', headers=headers)
    assert receipt.status_code == 404  # Nothing was claimed; an explicit retry is allowed.
    monkeypatch.setattr(service, 'operation_guard', original)
    saved = client.post(f'/api/file/{path.stem}/draft', json={'_edit':metadata,
        'intervention_report':'Texto preservado após falha do guard'}, headers=headers)
    assert saved.status_code == 200 and saved.json['success'] is True


def test_unmatched_api_route_keeps_404_instead_of_becoming_internal_failure(quarantine_app):
    response = quarantine_app[0].test_client().get('/api/synthetic-missing-route')
    assert response.status_code == 404
