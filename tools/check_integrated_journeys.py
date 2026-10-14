"""Finite real-browser journeys with separate synthetic web and queue worker.

Run independently of pytest: python -B tools/check_integrated_journeys.py.
Only new OS-temp stores and simulated provider effects are used. Browser assets,
editing coordinator, HTTP routes, SQLite queue and worker loop remain real.
"""
from __future__ import annotations
import atexit
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import time
from urllib.parse import parse_qs, quote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True


def until(predicate, message, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(.1)
    raise AssertionError(message)


def main():
    # Fail before creating processes when optional browser/PDF dependencies are missing.
    from playwright.sync_api import sync_playwright, expect
    from pypdf import PdfReader
    from importlib.metadata import version
    from tools.test_runtime import configure, block_outbound_network, seed_synthetic_data
    from tools.synthetic_journey_runtime import ADMIN, TECH, TENANT
    root = configure()
    nonce = secrets.token_hex(24)
    os.environ.update(FS_JOURNEY_ROOT=str(root), FS_JOURNEY_NONCE=nonce,
                      FS_WORKER_HEALTH_DIR=str(root / 'worker-health'))
    (root / 'journey-marker.json').write_text(json.dumps({'nonce':nonce, 'synthetic':True}))
    output = ROOT / 'output/playwright' / ('m16-' + root.name)
    output.mkdir(parents=True)
    processes, streams, results, errors, blocked = [], [], [], [], []
    state = {'autosave_status':None, 'list_status':None}

    def cleanup():
        (root / 'stop-processes').touch()
        for process in processes:
            if process.poll() is None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
        for stream in streams:
            stream.close()
    # Covers failures before the browser's own try/finally, including startup.
    atexit.register(cleanup)

    def start(kind):
        stream = (output / f'{kind}-{len(processes)}.txt').open('wb')
        streams.append(stream)
        process = subprocess.Popen([sys.executable, '-B', str(ROOT / 'tools/synthetic_journey_runtime.py'), kind],
            cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        processes.append(process)
        return process

    def ready(file, process):
        def read():
            assert process.poll() is None, f'Process exited {process.returncode}: see {output}'
            try:
                return json.loads((root / file).read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                return None
        return until(read, 'Process startup timed out', 45)

    def passed(message):
        results.append(message)
        print('PASS: ' + message, flush=True)

    def effect_rows():
        path = root / 'effects.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def queue_rows():
        with sqlite3.connect(root / 'data/graph-sync.sqlite3') as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute('SELECT id,kind,status,result_json FROM graph_sync_jobs')]

    with sync_playwright() as playwright:
        block_outbound_network()
        # All data is invented and created after the environment is isolated.
        from src.services.document_data_service import DocumentDataService
        from src.services.file_service import FileService
        from src.document_schema import normalize_document_payload
        sources, first = seed_synthetic_data(count=4)
        files = FileService(sources[0].parent)
        drafts = [first] + [files.create_draft_copy(source, 'Técnico fictício') for source in sources[1:3]]
        for draft in drafts:
            DocumentDataService(draft).write(normalize_document_payload({
                'customer_name':'Cliente fictício integração', 'customer_email':'customer@example.invalid',
                'service_types':{'assistencia':True}, 'equipments':{'vss':True}, 'client_not_present':True,
                'technician_records':[{'technician':'Técnico fictício','date':'2026-10-14',
                                       'start_time':'09:00','end_time':'10:00'}]}))
        web = start('web')
        web_info = ready('web-ready.json', web)
        worker = start('worker')
        worker_info = ready('worker-ready.json', worker)
        assert web_info['pid'] != worker_info['pid'] != os.getpid()
        base = f"http://127.0.0.1:{web_info['port']}"
        browser = playwright.chromium.launch(headless=True, args=[
            '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1'])
        # Only synthetic signed sessions: no login exchange or provider credentials.
        from flask import Flask
        signer_app = Flask('synthetic-session')
        signer_app.secret_key = os.environ['FS_SECRET_KEY']
        signer = signer_app.session_interface.get_signing_serializer(signer_app)

        def cookie(admin=False):
            user = {'id':ADMIN if admin else TECH, 'tenant_id':TENANT, 'username':'synthetic',
                    'display_name':'Administrador fictício' if admin else 'Técnico fictício',
                    'email':'jribeiro@sensorpoint.pt' if admin else 'technician@example.invalid'}
            return {'name':web_info['cookie_name'], 'value':signer.dumps({'microsoft_user':user}),
                    'url':base, 'httpOnly':True, 'sameSite':'Lax'}

        def context(admin=False):
            ctx = browser.new_context(viewport={'width':1280,'height':920}, service_workers='block')
            ctx.add_cookies([cookie(admin)])
            def route(request):
                url = urlsplit(request.request.url)
                if url.netloc != urlsplit(base).netloc:
                    blocked.append(url.hostname)
                    request.abort()
                elif url.path.endswith('/autosave') and state['autosave_status']:
                    request.fulfill(status=state['autosave_status'], json={'success':False,'error':'Falha sintética temporária'})
                elif url.path == '/api/files' and state['list_status']:
                    request.fulfill(status=state['list_status'], json={'success':False,'error':'Lista sintética indisponível'})
                else:
                    request.continue_()
            ctx.route('**/*', route)
            ctx.on('page', lambda p: p.on('pageerror', lambda error: errors.append(str(error))))
            return ctx

        def open_draft(page, draft):
            page.goto(base + '/?file=' + quote(draft.stem))
            expect(page.locator('#intervention-report')).to_be_editable(timeout=15000)

        def saved(page):
            expect(page.locator('#autosave-status')).to_have_attribute('data-state','saved', timeout=15000)

        def local_value(page):
            return page.evaluate("""() => new Promise((resolve,reject)=>{
                const r=indexedDB.open('sensorpoint-service-recovery-v1');
                r.onerror=()=>reject(r.error); r.onsuccess=()=>{const db=r.result;
                    const q=db.transaction('recoveries').objectStore('recoveries').getAll();
                    q.onsuccess=()=>{resolve(q.result.map(v=>v.payload.intervention_report));db.close();};
                    q.onerror=()=>{reject(q.error);db.close();};};})""")

        try:
            ctx = context()
            page = ctx.new_page()
            page.set_default_timeout(15000)
            page.goto(base)
            expect(page.locator(f'a[data-name="{first.stem}"]')).to_be_visible()
            page.locator(f'a[data-name="{first.stem}"]').click()
            expect(page.locator('#intervention-report')).to_be_editable()
            assert page.evaluate('!!window.__EDITING_COORDINATOR_STARTED__')
            page.locator('#intervention-report').fill('Texto confirmado no servidor')
            saved(page)
            passed('Real listing/open/coordinator and HTTP autosave')

            state['autosave_status'] = 503
            page.locator('#intervention-report').fill('Texto preservado durante 503')
            expect(page.locator('#autosave-status')).to_have_attribute('data-state','offline')
            assert 'Texto preservado durante 503' in local_value(page)
            expect(page.locator('#btn-save-send')).to_be_disabled()
            state['autosave_status'] = None
            saved(page)
            expect(page.locator('#intervention-report')).to_have_value('Texto preservado durante 503')
            passed('503 preserves IndexedDB/form and retries autosave without a commit')

            ctx.set_offline(True)
            page.locator('#intervention-report').fill('Texto offline recuperável')
            expect(page.locator('#autosave-status')).to_have_attribute('data-state','offline')
            assert 'Texto offline recuperável' in local_value(page)
            ctx.set_offline(False)
            saved(page)
            passed('Offline/online preserves and confirms the latest form')

            ctx.clear_cookies()
            with page.expect_response(lambda r:r.url.endswith('/autosave') and r.status==401):
                page.locator('#intervention-report').fill('Texto com sessão expirada')
            expect(page.locator('#intervention-report')).not_to_be_editable()
            until(lambda:'Texto com sessão expirada' in local_value(page), '401 recovery not persisted')
            ctx.add_cookies([cookie()])
            page.reload()
            expect(page.locator('#intervention-report')).to_have_value('Texto com sessão expirada')
            saved(page)
            passed('Actual HTTP 401 protects data; restored synthetic login reloads and saves recovery')

            second_ctx = context()
            second = second_ctx.new_page()
            open_draft(second, first)
            page.locator('#intervention-report').fill('Versão mais recente do primeiro navegador')
            saved(page)
            with second.expect_response(lambda r:r.url.endswith('/autosave') and r.status==409):
                second.locator('#intervention-report').fill('Versão concorrente preservada')
            expect(second.locator('#recovery-panel')).to_be_visible()
            assert 'Versão concorrente preservada' in local_value(second)
            second.screenshot(path=str(output / 'conflict.png'), full_page=True)
            second.locator('#btn-ignore-recovery').click()
            expect(second.locator('#intervention-report')).to_have_value('Versão mais recente do primeiro navegador')
            second_ctx.close()
            passed('Two independent browser contexts trigger a real revision conflict and explicit choice')
            ctx.close()
            ctx = context()
            page = ctx.new_page()
            # Abort only A's bootstrap: HTML/assets and B/C still use the real
            # isolated server. BroadcastChannel and tab lifecycle are Chromium's.
            page.route('**/bootstrap?**', lambda request: request.abort())
            page.goto(base + '/?file=' + quote(first.stem))
            expect(page.locator('#btn-retry-lease')).to_be_visible()
            expect(page.locator('#btn-save-draft')).to_be_disabled()
            editing_tab = ctx.new_page()
            open_draft(editing_tab, first)
            observing_tab = ctx.new_page()
            observing_tab.goto(base + '/?file=' + quote(first.stem))
            expect(observing_tab.locator('#autosave-status')).to_have_attribute('data-state','conflict')
            expect(page.locator('#btn-retry-lease')).to_be_hidden()
            observing_tab.close()
            expect(editing_tab.locator('#btn-save-draft')).to_be_enabled()
            expect(page.locator('#btn-save-draft')).to_be_disabled()
            editing_tab.close()
            expect(page.locator('#btn-retry-lease')).to_be_visible()
            expect(page.locator('#btn-save-draft')).to_be_disabled()
            page.screenshot(path=str(output / 'm22-bootstrap-retry.png'), full_page=True)
            page.unroute('**/bootstrap?**')
            page.locator('#btn-retry-lease').click()
            expect(page.locator('#intervention-report')).to_be_editable()
            expect(page.locator('#intervention-report')).to_have_value('Versão mais recente do primeiro navegador')
            passed('M22 real three-tab bootstrap failure restores explicit retry after the editor closes')

            state['list_status'] = 503
            page.locator('#btn-toggle-sidebar').click()
            page.locator('#btn-refresh').click()
            expect(page.locator('#file-refresh-status')).to_contain_text('Lista por confirmar')
            expect(page.locator('#intervention-report')).to_have_value('Versão mais recente do primeiro navegador')
            state['list_status'] = None
            page.locator('#btn-refresh').click()
            page.keyboard.press('Escape')
            passed('Failed list refresh retains the open document')

            (root / 'fault-disk-full').touch()
            before_document = DocumentDataService(first).read()
            with page.expect_response(lambda r:r.url.endswith('/draft')) as response:
                page.locator('#btn-save-draft').click()
            assert response.value.status == 500
            # Draft bundle writes are sequential. Excel can contain the new text
            # before a sidecar fails; verify all recovery sources, not byte rollback.
            from src.services.excel_service import ExcelService
            assert ExcelService(first).read_link()['AN'] == 'Versão mais recente do primeiro navegador'
            assert DocumentDataService(first).read() == before_document
            with sqlite3.connect(root / 'data/editing-state/editing-state.sqlite3') as db:
                snapshots = [json.loads(row[0]) for row in db.execute('SELECT state_json FROM editing_states')]
            assert any((s.get('autosave') or {}).get('document',{}).get('intervention_report') == 'Versão mais recente do primeiro navegador' for s in snapshots)
            expect(page.locator('#intervention-report')).to_have_value('Versão mais recente do primeiro navegador')
            page.reload()
            expect(page.locator('#intervention-report')).to_have_value('Versão mais recente do primeiro navegador')
            with page.expect_navigation(wait_until='load'):
                with page.expect_response(lambda r:r.url.endswith('/draft') and r.status==200):
                    page.locator('#btn-save-draft').click()
            expect(page.locator('#intervention-report')).to_be_editable()
            assert DocumentDataService(first).read()['intervention_report'] == 'Versão mais recente do primeiro navegador'
            passed('ENOSPC leaves readable Excel, prior sidecar and autosave/form intact; explicit retry reconciles them')

            (root / 'fault-crash-after-receipt').touch()
            page.locator('#btn-save-send').click()
            with page.expect_response(lambda r:r.url.endswith('/send') and r.status==200):
                page.locator('#confirm-accept').click()
            until(lambda:worker.poll() is not None, 'Worker did not reach receipt crash', 45)
            assert worker.returncode == 73
            assert len([r for r in effect_rows() if r['kind']=='mail']) == 1
            # Simulate elapsed reclaim time only after the original worker has died.
            with sqlite3.connect(root / 'data/graph-sync.sqlite3') as db:
                db.execute("UPDATE graph_sync_jobs SET updated_at=0 WHERE status='running'")
            (root / 'worker-ready.json').unlink()
            resumed = start('worker')
            ready('worker-ready.json', resumed)
            until(lambda:queue_rows() and all(r['status']=='complete' for r in queue_rows()), 'Queue not recovered', 45)
            effects = effect_rows()
            assert [r['kind'] for r in effects].count('mail') == [r['kind'] for r in effects].count('teams') == 1
            assert effects[0]['pid'] == worker_info['pid'] and effects[1]['pid'] == resumed.pid
            pdfs = list((root / 'excel/Arquivadas').rglob('*__folha_final.pdf'))
            assert len(pdfs) == 1
            reader = PdfReader(pdfs[0], strict=True)
            assert len(reader.pages) > 0 and 'Cliente fictício' in ''.join(p.extract_text() or '' for p in reader.pages)
            assert hashlib.sha256(pdfs[0].read_bytes()).hexdigest() == effects[0]['pdf_sha256']
            until(lambda:not first.exists(), 'Finalized draft was not retired')
            page.wait_for_url(base + '/')
            passed('Real PDF/mail/Teams queue survives worker death after receipt with exactly one of each effect')

            open_draft(page, drafts[1])
            page.locator('#btn-cancel-file').click()
            with page.expect_response(lambda r:r.url.endswith('/cancel') and r.status==200):
                page.locator('#confirm-accept').click()
            until(lambda:not drafts[1].exists(), 'Canceled draft still active')
            page.wait_for_url(base + '/')
            assert list((root / 'excel/Canceladas').rglob('*.xlsx'))
            assert effect_rows() == effects
            passed('Browser cancellation retires the active draft without mail or Teams')

            open_draft(page, drafts[2])
            page.locator('#intervention-report').fill('Preservar após intervenção administrativa')
            saved(page)
            admin_ctx = context(True)
            admin_page = admin_ctx.new_page()
            admin_page.goto(base+'/admin')
            card = admin_page.locator('#admin-documents article').filter(has_text=drafts[2].stem)
            card.get_by_role('button', name='Expulsar e libertar', exact=True).click()
            admin_page.locator('#admin-reason').fill('Ensaio sintético de preservação')
            admin_page.locator('#admin-submit').click()
            expect(admin_page.locator('#admin-confirm')).not_to_be_visible()
            with page.expect_response(lambda r:r.url.endswith('/autosave') and r.status==423):
                page.locator('#intervention-report').fill('Texto local depois da revogação')
            expect(page.locator('#intervention-report')).not_to_be_editable()
            expect(page.locator('#autosave-status')).to_contain_text('retirado pelo administrador')
            with sqlite3.connect(root / 'data/editing-state/editing-state.sqlite3') as db:
                states = [json.loads(row[0]) for row in db.execute('SELECT state_json FROM editing_states')]
            state_after_revoke = next(s for s in states if drafts[2].stem in s['aliases'])
            assert state_after_revoke['autosave']['document']['intervention_report'] == 'Preservar após intervenção administrativa'
            card.get_by_role('button', name='Repor acesso:', exact=False).click()
            admin_page.locator('#admin-reason').fill('Reposição sintética após diagnóstico')
            admin_page.locator('#admin-submit').click()
            expect(admin_page.locator('#admin-confirm')).not_to_be_visible()
            page.reload()
            expect(page.locator('#intervention-report')).to_be_editable()
            denied = ctx.new_page().goto(base+'/admin')
            assert denied.status == 403
            passed('Real admin release/restore and non-admin refusal preserve editing state')
            page.screenshot(path=str(output / 'restored-editor.png'), full_page=True)

            open_draft(page, sources[3])
            original_bytes = sources[3].read_bytes()
            page.locator('#intervention-report').fill('Novo rascunho criado no navegador')
            saved(page)
            before_drafts = set(sources[3].parent.glob('*/*.xlsx'))
            with page.expect_navigation(wait_until='load'):
                with page.expect_response(lambda r:r.url.endswith('/draft') and r.status==200):
                    page.locator('#btn-save-draft').click()
            new_name = parse_qs(urlsplit(page.url).query)['file'][0]
            assert new_name != sources[3].stem
            new_draft = files.get_file_by_name(new_name)
            assert new_draft is not None and files.is_draft_file(new_draft)
            assert set(sources[3].parent.glob('*/*.xlsx')) - before_drafts == {new_draft}
            assert DocumentDataService(new_draft).read()['intervention_report'] == 'Novo rascunho criado no navegador'
            assert sources[3].read_bytes() == original_bytes and effect_rows() == effects
            passed('Creating a draft through the real UI preserves the original and creates no delivery')
            assert errors == [] and blocked == [], (errors,blocked)
            report = {'status':'PASS','source':__import__('src.services.release_identity',fromlist=['get_release_info']).get_release_info(),
                'browser':browser.version, 'python':sys.version.split()[0],
                'playwright':version('playwright'), 'pypdf':version('pypdf'),
                'web_pid':web.pid,'initial_worker_pid':worker.pid,
                'resumed_worker_pid':resumed.pid,'results':results,'effects':effects,
                'limits':['Synthetic authentication exchange and provider transports',
                          'Service worker excluded here; covered separately by M19',
                          'Local backend; Graph remote consistency covered by dedicated regressions',
                          'M22 lost-closing online retry and shared recovery conflicts covered separately by Node regressions'], 'runtime':str(root)}
            (output / 'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            print(json.dumps({'status':'PASS','checks':len(results),'output':str(output)},ensure_ascii=False),flush=True)
        except Exception:
            try:
                page.screenshot(path=str(output / 'failure.png'), full_page=True)
            except Exception:
                pass
            raise
        finally:
            browser.close()
            cleanup()
            atexit.unregister(cleanup)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
