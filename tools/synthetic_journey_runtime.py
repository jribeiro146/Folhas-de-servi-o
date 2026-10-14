"""Test-only web/worker processes sharing one newly created synthetic runtime.

There is no live provider fallback. Invoke through check_integrated_journeys.py;
the inherited marker and every storage root are checked before application imports.
"""
from __future__ import annotations
import base64
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
TENANT = '11111111-1111-4111-8111-111111111111'
ADMIN = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
TECH = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'


def checked_runtime():
    root = Path(os.environ['FS_JOURNEY_ROOT']).resolve(strict=True)
    if root.parent != Path(tempfile.gettempdir()).resolve() or not root.name.startswith('fst-'):
        raise RuntimeError('Expected a newly created test runtime')
    marker = json.loads((root / 'journey-marker.json').read_text())
    if marker != {'nonce': os.environ['FS_JOURNEY_NONCE'], 'synthetic': True}:
        raise RuntimeError('Synthetic runtime marker mismatch')
    expected = {'FS_ENVIRONMENT':'test', 'FS_TEST_SYNTHETIC':'true', 'FS_STORAGE_BACKEND':'local',
                'FS_AUTH_PROVIDER':'none', 'FS_MAIL_ENABLED':'false',
                'FS_TEAMS_NOTIFICATIONS_ENABLED':'false', 'FS_GRAPH_QUEUE_IN_WEB':'false',
                'FS_LOG_ENABLED':'false', 'FS_LOG_REQUESTS':'false'}
    if any(os.environ.get(key) != value for key,value in expected.items()):
        raise RuntimeError('Unsafe test environment')
    for key, relative in {'FS_BASE_PATH':'.', 'FS_APP_DATA_DIR':'data', 'FS_EXCEL_ROOT':'excel',
                          'GRAPH_CACHE_DIR':'cache', 'FS_ENV_FILE':'absent.env',
                          'FS_WORKER_HEALTH_DIR':'worker-health'}.items():
        if Path(os.environ[key]).resolve() != (root / relative).resolve():
            raise RuntimeError('Storage outside the synthetic runtime')
    if (root / 'absent.env').exists():
        raise RuntimeError('The test must not load an env file')
    return root


def fault(root, name):
    """Each process owns its selected fault; files contain no operational data."""
    flag = root / ('fault-' + name)
    if flag.exists():
        flag.unlink()
        return True
    return False


class Response(io.BytesIO):
    status = 202
    headers = {}


def record(root, kind, payload):
    # Append/fsync proves effects even if the worker is killed before returning.
    with (root / 'effects.jsonl').open('ab') as stream:
        stream.write((json.dumps({'kind':kind, 'pid':os.getpid(), **payload})+'\n').encode())
        stream.flush()
        os.fsync(stream.fileno())


def services(root, renderer=None):
    from src.services.graph_mail_service import GraphMailConfig, GraphMailService
    from src.services.teams_notification_service import TeamsNotificationConfig, TeamsNotificationService
    from src.services.local_pdf_service import LocalPdfService

    def transport(request, **kwargs):
        parsed = urlsplit(request.full_url)
        if parsed.hostname == 'login.microsoftonline.com' and parsed.path == '/synthetic/oauth2/v2.0/token':
            claims = base64.urlsafe_b64encode(json.dumps({'roles':['Mail.Send']}).encode()).decode().rstrip('=')
            return Response(json.dumps({'access_token':'synthetic.'+claims+'.synthetic', 'expires_in':3600}).encode())
        if parsed.hostname == 'graph.microsoft.com' and parsed.path == '/v1.0/users/sender@example.invalid/sendMail':
            message = json.loads(request.data)['message']
            attachments = message['attachments']
            assert len(attachments) == 1 and attachments[0]['contentType'] == 'application/pdf'
            pdf = base64.b64decode(attachments[0]['contentBytes'], validate=True)
            assert pdf.startswith(b'%PDF-')
            record(root, 'mail', {'operation':message['internetMessageHeaders'][0]['value'],
                'to':message['toRecipients'], 'cc':message.get('ccRecipients',[]),
                'attachment':attachments[0]['name'], 'pdf_sha256':hashlib.sha256(pdf).hexdigest()})
            return Response(b'')
        if request.full_url == 'https://teams.example.invalid/synthetic':
            record(root, 'teams', {'operation': request.get_header('X-sensorpoint-operation-id'),
                                   'body':json.loads(request.data)})
            return Response(b'')
        raise AssertionError('Unexpected provider request in synthetic transport')

    mail = GraphMailService(GraphMailConfig('synthetic','synthetic','synthetic','sender@example.invalid'), transport=transport)
    teams = TeamsNotificationService(TeamsNotificationConfig('https://teams.example.invalid/synthetic'), transport=transport)
    return mail, teams, LocalPdfService(renderer=renderer)


def build_queue(root, *, renderer=None, release_info=None, contract=None):
    from src.services.graph_sync_queue import GraphSyncQueue
    mail, teams, pdf = services(root, renderer)
    queue = GraphSyncQueue(None, root / 'data/graph-sync.sqlite3', mail_service=mail,
        teams_notification_service=teams, local_pdf_service=pdf, auto_start=False, runtime_contract=contract)
    if release_info is not None:
        queue.release_info = release_info
    return queue


def web(root):
    from tools.test_runtime import block_outbound_network
    block_outbound_network()
    import src.web.application as application
    from src.services import admin_access
    from src.services.document_data_service import DocumentDataService
    from src.services.microsoft_auth_service import MicrosoftAuthService
    from werkzeug.serving import make_server
    # Use the production session parser/role policy with an invented identity;
    # the Microsoft login exchange itself is deliberately outside this matrix.
    application.AUTH_ENABLED = True
    application.ACTIVE_AUTH_PROVIDER = 'microsoft'
    admin_access.ADMIN_TENANT_ID, admin_access.ADMIN_OBJECT_ID = TENANT, ADMIN
    original_write = DocumentDataService.write
    def write(self, document):
        if fault(root, 'disk-full'):
            raise OSError(errno.ENOSPC, 'Synthetic full disk')
        return original_write(self, document)
    DocumentDataService.write = write
    mail, teams, pdf = services(root)
    app = application.create_app(mail_service=mail, teams_notification_service=teams,
        local_pdf_service=pdf, graph_sync_queue=build_queue(root),
        microsoft_auth_service=MicrosoftAuthService(tenant_id=TENANT, client_id=TECH, client_secret='synthetic'))
    server = make_server('127.0.0.1', 0, app, threaded=True)
    (root / 'web-ready.json').write_text(json.dumps({'port':server.server_port, 'pid':os.getpid(),
        'cookie_name':app.config['SESSION_COOKIE_NAME']}))
    def stop_when_requested():
        while not (root / 'stop-processes').exists():
            time.sleep(.1)
        server.shutdown()
    threading.Thread(target=stop_when_requested, daemon=True).start()
    try:
        server.serve_forever()
    finally:
        server.server_close()


def worker(root):
    from playwright.sync_api import sync_playwright
    from tools.test_runtime import block_outbound_network
    # Playwright initializes its loopback IPC before Python network denial.
    with sync_playwright() as playwright:
        block_outbound_network()
        browser = playwright.chromium.launch(headless=True, args=['--host-resolver-rules=MAP * ~NOTFOUND'])
        context = browser.new_context(service_workers='block')
        context.route('**/*', lambda route: route.continue_() if urlsplit(route.request.url).scheme in {'file','data'} else route.abort())
        def render(source, destination):
            page = context.new_page()
            try:
                page.goto(source.as_uri())
                page.pdf(path=str(destination), print_background=True, prefer_css_page_size=True)
            finally:
                page.close()
        import src.queue_worker as queue_worker
        process_once = queue_worker.process_once
        def cycle(queue=None):
            if (root / 'stop-processes').exists():
                raise KeyboardInterrupt
            return process_once(queue)
        queue_worker.process_once = cycle
        def factory(info, contract):
            queue = build_queue(root, renderer=render, release_info=info, contract=contract)
            checkpoint = queue._checkpoint_result
            def checked_checkpoint(job_id, result):
                checkpoint(job_id, result)
                if result.get('phase') == 'mail_accepted' and fault(root, 'crash-after-receipt'):
                    os._exit(73)
            queue._checkpoint_result = checked_checkpoint
            (root / 'worker-ready.json').write_text(json.dumps({'pid':os.getpid()}))
            return queue
        queue_worker._build_admitted_queue = factory
        try:
            try:
                return queue_worker.main(['--watch','--interval','1'])
            except KeyboardInterrupt:
                if not (root / 'stop-processes').exists():
                    raise
                return 0
        finally:
            context.close()
            browser.close()


if __name__ == '__main__':
    runtime = checked_runtime()
    if sys.argv[1:] == ['web']:
        web(runtime)
    elif sys.argv[1:] == ['worker']:
        raise SystemExit(worker(runtime))
    else:
        raise SystemExit('Expected web or worker')
