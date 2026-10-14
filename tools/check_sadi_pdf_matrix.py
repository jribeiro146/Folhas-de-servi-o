"""Synthetic production-policy SADI HTTP/queue matrix with real Chromium PDFs.

No Microsoft login exchange, Graph upload or mail delivery is performed. Uses
the same test factory as pytest, with an actual session parser and PDF renderer.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True


def main():
    from playwright.sync_api import sync_playwright
    from pypdf import PdfReader
    from importlib.metadata import version
    import pytest
    from tools.test_runtime import configure, block_outbound_network
    root = configure()  # Fresh OS-temp paths and disabled operational flags.
    output = ROOT / 'output/pdf' / ('m18-' + root.name)
    output.mkdir(parents=True)
    (output / 'runtime.json').write_text(json.dumps({'synthetic':True, 'runtime':str(root)}), encoding='utf-8')
    results, blocked = [], []

    def passed(message):
        results.append(message)
        print('PASS: ' + message, flush=True)

    # Start Playwright IPC before denying Python outbound connections.
    with sync_playwright() as playwright:
        block_outbound_network()
        from src.services.release_identity import is_delivery_file, write_release_manifest, get_release_info
        from src.services.microsoft_auth_service import MicrosoftAuthService
        from src.services.maintenance_private_service import MaintenancePrivateService
        from src.services.document_data_service import DocumentDataService
        from src.services.graph_storage_service import GraphStorageService
        from tools.synthetic_journey_runtime import services, TENANT
        from tests.test_maintenance_access import synthetic_production_sadi, lease
        from tests.test_maintenance import complete_document, sign_document, checklist_photo

        # Same code-only manifest preparation as the existing pytest fixture.
        # This invented revision is a QA fixture, never a distributable release.
        release = root / 'verified-test-release'
        release.mkdir()
        for name in ('src', 'tools'):
            shutil.copytree(ROOT / name, release / name, ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        for path in ROOT.iterdir():
            if is_delivery_file(path.name):
                shutil.copyfile(path, release / path.name)
        write_release_manifest(release, revision='a' * 40)

        browser = playwright.chromium.launch(headless=True, args=['--host-resolver-rules=MAP * ~NOTFOUND'])
        context = browser.new_context(service_workers='block')
        def route(request):
            if urlsplit(request.request.url).scheme in {'file','data'}:
                request.continue_()
            else:
                blocked.append(request.request.url)
                request.abort()
        context.route('**/*', route)
        def render(source, destination):
            page = context.new_page()
            try:
                page.goto(source.as_uri())
                page.pdf(path=str(destination), print_background=True, prefer_css_page_size=True)
            finally:
                page.close()

        try:
            with pytest.MonkeyPatch.context() as patch:
                mail = services(root)[0]  # Actual mail preparation, strict synthetic transport only.
                app, client, draft, edits, data = synthetic_production_sadi(root / 'scenario', patch,
                    {'renderer':render, 'mail_service':mail,
                     'auth_service':MicrosoftAuthService(tenant_id=TENANT, client_id='synthetic', client_secret='synthetic')}, release)
                queue = app.extensions['test_graph_queue']
                identities = {
                    'jribeiro@sensorpoint.pt':'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
                    'acarvalho@sensorpoint.pt':'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
                    'other@example.invalid':'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
                }
                def login(email):
                    with client.session_transaction() as session:
                        session['microsoft_user'] = {'id':identities[email], 'tenant_id':TENANT,
                            'email':email, 'username':email, 'display_name':'Pessoa fictícia SADI'}

                login('jribeiro@sensorpoint.pt')
                metadata = lease(client, draft, 'pdf-matrix')
                document = complete_document()
                document['customer_email'] = 'customer@example.invalid'
                markers = ['PRIVADO_ALFA_9900', 'PRIVADO_BETA_9900']
                for index, site in enumerate(document['maintenance_checklists']):
                    site['location'] = ['Local Alfa fictício', 'Local Beta fictício'][index]
                    site['final_observations'] = markers[index] + ' - Observação reservada ao local.'
                    site['photos'] = [checklist_photo(size=(700,400))]
                document['maintenance_checklists'][0]['customer_not_present'] = True
                sign_document(document, app.secret_key)
                checked = client.post(f'/api/file/{draft.stem}/maintenance/validate', json={'document':document})
                assert checked.status_code == 200 and checked.json['errors'] == [], checked.json
                assert checked.json['sites'][0]['signed']['technician'] is True
                assert checked.json['sites'][0]['waived'] == {'customer':True, 'technician':False}
                assert all(checked.json['sites'][1]['signed'].values())
                saved = client.post(f'/api/file/{draft.stem}/draft', json={**document, '_edit':metadata})
                assert saved.status_code == 200, saved.json
                queue.run_until_idle()  # Finish only the synthetic draft publication before send.
                passed('Current signatures, absent customer and saved private draft accepted')

                for email in identities:
                    login(email)
                    allowed = email != 'other@example.invalid'
                    response = client.get(f'/api/file/{draft.stem}')
                    assert response.status_code == 200 and response.cache_control.no_store
                    assert (markers[0] in response.get_data(as_text=True)) is allowed
                    bootstrap = client.get(f'/api/file/{draft.stem}/bootstrap?client_id=read-{identities[email]}')
                    assert bootstrap.status_code == (200 if allowed else 423), bootstrap.json
                    assert bootstrap.cache_control.no_store
                    if not allowed:
                        assert bootstrap.json['code'] == 'lease_conflict'
                    assert (markers[0] in bootstrap.get_data(as_text=True)) is allowed
                    preview = client.post(f'/api/file/{draft.stem}/document-preview',
                        json={**document, '_maintenance_site_id':document['maintenance_checklists'][0]['id']})
                    assert preview.status_code == (200 if allowed else 404)
                    assert preview.cache_control.no_store
                assert 'maintenance_checklists' not in DocumentDataService(draft).read()
                passed('João/Ana/other account API and preview policy; private content absent from public sidecar')

                login('jribeiro@sensorpoint.pt')
                changed = deepcopy(document)
                changed['maintenance_checklists'][0]['final_observations'] += ' Alterado.'
                checked = client.post(f'/api/file/{draft.stem}/maintenance/validate', json={'document':changed})
                assert checked.json['sites'][0]['signed']['technician'] is False
                assert all(checked.json['sites'][1]['signed'].values())
                passed('Changing one site invalidates its signature while preserving the other site')

                metadata = lease(client, draft, 'pdf-matrix')
                sent = client.post(f'/api/file/{draft.stem}/send', json={**document, '_edit':metadata})
                assert sent.status_code == 200, sent.json
                bundle_url = sent.json['maintenance_bundle_url']
                private = MaintenancePrivateService(data / 'private/sadi')
                _, entries, directory = private.bundle(bundle_url.rsplit('/',1)[-1])
                assert len(entries) == 3
                queue.run_until_idle()
                assert queue.status(sent.json['graph_job_id'])['status'] == 'complete'
                effects = [json.loads(line) for line in (root / 'effects.jsonl').read_text().splitlines()]
                assert len(effects) == 1 and effects[0]['kind'] == 'mail'
                archive = next((data / 'archive').rglob('*.xlsx'))
                mail_pdf = archive.with_name(archive.stem + '__folha_final.pdf')
                assert hashlib.sha256(mail_pdf.read_bytes()).hexdigest() == effects[0]['pdf_sha256']
                assert effects[0]['attachment'] == mail_pdf.name and '__SADI_' not in mail_pdf.name
                assert not list(archive.parent.glob('*__SADI_*'))
                uploadable = list(GraphStorageService._iter_uploadable_files(archive.parent))
                assert all('__SADI_' not in path.name and path.name != 'maintenance-manifest.json' for path in uploadable)
                assert 'maintenance_checklists' not in DocumentDataService(archive).read()

                pdf_records = []
                for index, entry in enumerate(entries):
                    path = directory / entry['name']
                    reader = PdfReader(path, strict=True)
                    text = '\n'.join(page.extract_text() or '' for page in reader.pages)
                    folded = ' '.join(text.casefold().split())
                    if index == 0:
                        assert all(marker not in text for marker in markers)
                    else:
                        assert markers[index-1] in text and markers[2-index] not in text
                        assert 'assinatura do técnico' in folded and 'assinatura por recolher' not in folded
                        assert ('cliente não presente' in folded) is (index == 1)
                    target = output / ['folha-servico.pdf', 'sadi-local-alfa.pdf', 'sadi-local-beta.pdf'][index]
                    shutil.copyfile(path, target)
                    pdf_records.append({'file':target.name,'pages':len(reader.pages),
                        'sha256':hashlib.sha256(target.read_bytes()).hexdigest()})
                normal_text = '\n'.join(page.extract_text() or '' for page in PdfReader(mail_pdf).pages)
                assert all(marker not in normal_text for marker in markers)
                shutil.copyfile(mail_pdf, output / 'anexo-email.pdf')
                passed('Real PDFs contain only their site; normal mail has exactly one service PDF without private SADI')

                for email in identities:
                    login(email)
                    allowed = email != 'other@example.invalid'
                    page = client.get(bundle_url)
                    assert page.status_code == (200 if allowed else 404) and page.cache_control.no_store
                    for entry in entries:
                        for suffix in ('', '?download=1'):
                            response = client.get(bundle_url + '/pdf/' + entry['name'] + suffix)
                            assert response.status_code == (200 if allowed else 404)
                            assert response.cache_control.no_store
                            if allowed:
                                assert response.data == (directory / entry['name']).read_bytes()
                            response.close()  # Close streamed file handles before leaving the runtime.
                assert blocked == [], blocked
                passed('Private bundle/PDF/download access and no-store confirmed for all three roles')
                report = {'status':'PASS', 'source':get_release_info(), 'results':results,
                    'pdfs':pdf_records, 'mail_pdf_sha256':effects[0]['pdf_sha256'],
                    'browser':browser.version, 'playwright':version('playwright'), 'pypdf':version('pypdf'),
                    'pytest':version('pytest'),
                    'runtime':str(root), 'limits':['Synthetic Microsoft login exchange and Graph/mail transport',
                    'PDFs still require visual inspection', 'Historical Windows rename cause remains unproven',
                    'M22 three-tab offline bootstrap issue remains open']}
                (output / 'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
                print(json.dumps({'status':'PASS','output':str(output)},ensure_ascii=False),flush=True)
        finally:
            context.close()
            browser.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
