"""Durable provider deadlines, shared by web, workers and administrative actions.

No network/configuration import at module load: offline migration/backup tools
use the same validation without instantiating the application.
"""
from contextlib import closing
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time
import uuid

from src.services.sqlite_storage_format import initialize_store, validate_store

STORE_NAME = 'provider-waits.sqlite3'
AUTOMATIC_WAIT_LIMIT = 86400
_SCOPE = re.compile(r'(storage|mail|teams|application):[0-9a-f]{64}\Z')


def default_store_path():
    from src.config import APP_DATA_DIR
    return APP_DATA_DIR / STORE_NAME


def provider_scope(provider, *identifiers):
    if provider not in {'storage', 'mail', 'teams', 'application'}:
        raise ValueError('Âmbito de fornecedor inválido.')
    value = json.dumps([str(item).strip().casefold() for item in identifiers], separators=(',', ':'))
    return provider + ':' + hashlib.sha256(value.encode('utf-8')).hexdigest()


def parse_retry_after(value, *, now, fallback):
    """Never shorten a valid delay. Huge valid integers suspend, not fallback."""
    raw = str(value).strip() if value is not None else ''
    deadline = None
    kind = 'missing' if not raw else 'invalid'
    if re.fullmatch(r'[0-9]+', raw):
        kind = 'seconds'
        # Avoid Python integer/string conversion limits and floating overflow.
        # No representable deadline means explicit indefinite intervention.
        if len(raw.lstrip('0') or '0') <= 64:
            deadline = now + float(raw.lstrip('0') or '0')
        else:
            return {'deadline': None, 'suspended': True, 'source': 'anomalous'}
    elif raw:
        try:
            parsed = parsedate_to_datetime(raw)
            if parsed.tzinfo is not None:
                deadline = max(now, parsed.timestamp())
                kind = 'http_date'
        except (TypeError, ValueError, OverflowError):
            pass
    if deadline is None:
        deadline = now + fallback
    if not math.isfinite(deadline):
        return {'deadline': None, 'suspended': True, 'source': 'anomalous'}
    return {'deadline': deadline, 'suspended': deadline - now > AUTOMATIC_WAIT_LIMIT, 'source': kind}


def _invalid_constant(_):
    raise ValueError('Constante inválida no prazo do fornecedor.')


def validate_pause(value, scope):
    required = {'format', 'scope', 'provider', 'version', 'deadline', 'suspended', 'source',
                'observed_at', 'http_status', 'evidence_id', 'failures'}
    if (not isinstance(value, dict) or set(value) != required or value['format'] != 1
            or type(value['format']) is not int or not _SCOPE.fullmatch(scope)
            or value['scope'] != scope or value['provider'] != scope.split(':')[0]
            or type(value['version']) is not int or value['version'] < 1
            or type(value['failures']) is not int or not 1 <= value['failures'] <= 16
            or type(value['suspended']) is not bool
            or value['source'] not in {'seconds', 'http_date', 'missing', 'invalid', 'anomalous'}
            or type(value['http_status']) is not int or not 400 <= value['http_status'] <= 599
            or not isinstance(value['evidence_id'], str) or not re.fullmatch(r'[a-f0-9]{32}', value['evidence_id'])):
        raise ValueError('Estado de espera inválido; requer diagnóstico.')
    for key in ('deadline', 'observed_at'):
        item = value[key]
        if key == 'deadline' and item is None and value['suspended']:
            continue
        if type(item) not in (int, float) or not math.isfinite(item) or item <= 0:
            raise ValueError('Prazo de fornecedor inválido; requer diagnóstico.')
    return value


def decode_pause(raw, scope):
    return validate_pause(json.loads(raw, parse_constant=_invalid_constant), scope)


class ProviderWaitError(RuntimeError):
    """A request was refused or prevented; no new remote effect is implied."""
    def __init__(self, pause, *, now=None):
        now = time.time() if now is None else now
        self.pause = validate_pause(dict(pause), pause['scope'])
        self.scope = pause['scope']
        self.retry_not_before = pause['deadline']
        self.retry_after_seconds = max(0.0, pause['deadline'] - now) if pause['deadline'] is not None else None
        self.retryable = not pause['suspended']
        self.http_status = pause['http_status']
        super().__init__('A Microsoft indicou uma espera que requer análise administrativa.' if pause['suspended']
                         else 'A aguardar o prazo indicado pela Microsoft.')


class ProviderWaitStore:
    def __init__(self, database_path=None, *, clock=None):
        self.database_path = Path(database_path) if database_path is not None else default_store_path()
        self.clock = clock or time.time
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect(check_format=False)) as db:
            initialize_store(db, 'provider_waits')
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=FULL')

    def _connect(self, *, check_format=True):
        db = sqlite3.connect(self.database_path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            if check_format:
                validate_store(db, 'provider_waits')
            return db
        except BaseException:
            db.close()
            raise

    @staticmethod
    def _read(db, scope):
        if not isinstance(scope, str) or not _SCOPE.fullmatch(scope):
            raise ValueError('Âmbito inválido.')
        row = db.execute('SELECT state_json FROM provider_waits WHERE scope=?', (scope,)).fetchone()
        return decode_pause(row[0], scope) if row else None

    @staticmethod
    def _audit(db, *, actor, action, before, after, reason, now):
        db.execute('INSERT INTO provider_wait_actions VALUES(?,?,?,?,?,?,?,?)',
            (uuid.uuid4().hex, after['scope'], actor, action, reason,
             json.dumps(before), json.dumps(after), now))

    def record(self, scope, *, http_status, retry_after, base_delay=1.0):
        now = self.clock()
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                before = self._read(db, scope)
                failures = min(16, (before['failures'] if before else 0) + 1)
                wait = parse_retry_after(retry_after, now=now,
                    fallback=min(300.0, base_delay * 2 ** min(failures - 1, 8)))
                if before:
                    wait['suspended'] = before['suspended'] or wait['suspended']
                    if before['deadline'] is None or wait['deadline'] is None:
                        wait['deadline'] = None
                    else:
                        wait['deadline'] = max(before['deadline'], wait['deadline'])
                value = {'format': 1, 'scope': scope, 'provider': scope.split(':')[0],
                         'version': (before['version'] if before else 0) + 1,
                         **wait, 'observed_at': now, 'http_status': http_status,
                         'evidence_id': uuid.uuid4().hex, 'failures': failures}
                validate_pause(value, scope)
                db.execute('INSERT INTO provider_waits VALUES(?,?,?) ON CONFLICT(scope) DO UPDATE '
                           'SET state_json=excluded.state_json,updated_at=excluded.updated_at',
                           (scope, json.dumps(value), now))
                self._audit(db, actor='system', action='provider_wait_recorded', before=before,
                            after=value, reason='Resposta Microsoft registada.', now=now)
                db.commit()
                return value
            except BaseException:
                db.rollback()
                raise

    def blocking(self, scopes):
        now = self.clock()
        with closing(self._connect()) as db:
            db.execute('BEGIN')
            values = [value for scope in scopes if (value := self._read(db, scope)) is not None
                      and (value['suspended'] or value['deadline'] > now)]
        return max(values, key=lambda value: (value['suspended'], value['deadline'] is None,
                   value['deadline'] or 0)) if values else None

    def require_ready(self, scopes):
        value = self.blocking(scopes)
        if value:
            raise ProviderWaitError(value, now=self.clock())

    def resume(self, scope, *, expected_version, evidence_id, actor, reason):
        if (type(expected_version) is not int or not isinstance(actor, str) or not actor.strip()
                or not isinstance(reason, str) or not 3 <= len(reason.strip()) <= 400):
            raise ValueError('Identifique o responsável e o motivo da decisão.')
        now = self.clock()
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                before = self._read(db, scope)
                if (not before or before['version'] != expected_version or before['evidence_id'] != evidence_id):
                    raise ValueError('A espera mudou. Atualize o diagnóstico antes de decidir.')
                if not before['suspended']:
                    raise ValueError('Esta espera não requer uma decisão administrativa.')
                if before['deadline'] is None or now < before['deadline']:
                    raise ValueError('O prazo do fornecedor não pode ser antecipado. Confirme a causa da resposta anómala.')
                after = {**before, 'suspended': False, 'version': before['version'] + 1,
                         'evidence_id': uuid.uuid4().hex}
                self._audit(db, actor=actor, action='provider_wait_resumed', before=before,
                            after=after, reason=reason.strip(), now=now)
                db.execute('UPDATE provider_waits SET state_json=?,updated_at=? WHERE scope=?',
                           (json.dumps(after), now, scope))
                db.commit()
                return self.public(after, now=now)
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def public(value, *, now):
        validate_pause(value, value['scope'])
        return {key: value[key] for key in ('scope', 'provider', 'version', 'deadline', 'suspended',
                'observed_at', 'evidence_id')} | {'blocked': value['suspended'] or value['deadline'] > now,
                'can_resume': value['suspended'] and value['deadline'] is not None and value['deadline'] <= now}

    def overview(self):
        now = self.clock()
        with closing(self._connect()) as db:
            rows = db.execute('SELECT scope,state_json FROM provider_waits ORDER BY updated_at DESC LIMIT 100').fetchall()
        return [self.public(decode_pause(row['state_json'], row['scope']), now=now) for row in rows]

    def history(self):
        with closing(self._connect()) as db:
            rows = db.execute('SELECT id,scope,actor,action,reason,created_at FROM provider_wait_actions '
                              'ORDER BY created_at DESC,id DESC LIMIT 100').fetchall()
        return [dict(row) for row in rows]


def validate_provider_rows(db):
    """Offline validation for migration/backup; unknown guard state is not reset."""
    for scope, raw in db.execute('SELECT scope,state_json FROM provider_waits'):
        decode_pause(raw, scope)
    for scope, before, after in db.execute('SELECT scope,before_json,after_json FROM provider_wait_actions'):
        if before != 'null':
            decode_pause(before, scope)
        decode_pause(after, scope)
