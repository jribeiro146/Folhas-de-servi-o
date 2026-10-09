"""Exercise the real editor/monitor in Chromium with synthetic, intercepted HTTP.

No web server, operational configuration, worker, or external request is used.
Run separately from pytest with the optional Playwright browser dependency.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "output/playwright")
    args = parser.parse_args()
    from tools.test_runtime import configure, block_outbound_network

    configure()
    from playwright.sync_api import sync_playwright, expect

    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as playwright:
        # Windows' asyncio loop creates its own loopback socket pair on entry.
        # Block network before importing the application, after that IPC setup.
        block_outbound_network()
        from src.web.application import create_app
        app = create_app()
        with app.test_client() as client:
            response = client.get("/")
            assert response.status_code == 200
            html = response.get_data(as_text=True)
        browser = playwright.chromium.launch(headless=True)
        try:
            for scenario in ("failed", "scheduled", "distant", "missing"):
                context = browser.new_context(viewport={"width": 1280, "height": 920}, service_workers="block")
                page = context.new_page()
                page.set_default_timeout(10000)
                page_errors, calls, blocked = [], [], []
                page.on("pageerror", lambda error: page_errors.append(str(error)))

                def intercept(route):
                    request = route.request
                    url = urlsplit(request.url)
                    if url.netloc != "synthetic.invalid":
                        blocked.append(request.url)
                        route.abort()
                        return
                    path = unquote(url.path)
                    if path == "/":
                        route.fulfill(status=200, content_type="text/html", body=html)
                    elif path.startswith("/static/"):
                        asset = (ROOT / "src/web" / path.lstrip("/")).resolve()
                        if not asset.is_relative_to((ROOT / "src/web/static").resolve()) or not asset.is_file():
                            route.abort()
                        elif asset.name in {"editing-coordinator.js", "pwa.js", "active-file-list.js"}:
                            route.fulfill(status=200, content_type="text/javascript", body="")
                        else:
                            route.fulfill(path=asset)
                    elif path == "/api/file/synthetic/send":
                        calls.append((request.method, path))
                        route.fulfill(json={"success": True, "email_status": "pending", "graph_job_id": "synthetic-job"})
                    elif path == "/api/graph/jobs/synthetic-job":
                        calls.append((request.method, path))
                        if scenario == "missing":
                            route.fulfill(status=404, body="Missing")
                        elif scenario == "distant":
                            route.fulfill(status=429, headers={"Retry-After": "9" * 64}, body="")
                        else:
                            job = {"id": "synthetic-job", "status": "failed", "last_error": "PRIVATE_PROVIDER_DETAIL"}
                            if scenario == "scheduled":
                                job.update(will_retry=True, next_attempt_at=time.time() + 86400)
                            route.fulfill(json={"success": True, "job": job})
                    else:
                        blocked.append(request.url)
                        route.abort()

                context.route("**/*", intercept)
                page.goto("https://synthetic.invalid/", wait_until="load")
                page.wait_for_function("window.__FILES_EDITOR_READY__ === true")
                page.evaluate("""() => {
                    const app = window.__FILES_APP__;
                    app.requiredFields = [];
                    app.technicianRequiredFields = [];
                    app.signatureRequiredFields = [];
                    let committed = false;
                    window.__EDITING_COORDINATOR__ = {
                        operationMetadata: () => ({document_id:'synthetic', client_id:'tab',
                            lease_token:'synthetic', base_revision:0, idempotency_key:'synthetic'}),
                        markCommitted: async () => { committed = true; },
                        syncActionState: () => {
                            for (const id of ['btn-save-send', 'btn-save-draft', 'btn-cancel-file']) {
                                document.getElementById(id).disabled = committed;
                            }
                        }
                    };
                    window.__FILES_EDITOR__.populateForm('synthetic', {
                        customer_email:'synthetic@example.invalid', client_not_present:true,
                        technician_records:[{technician:'Técnico fictício', date:'2026-10-09',
                            start_time:'10:00', end_time:'11:00', total_hours:'1'}]
                    });
                    window.__EDITING_COORDINATOR__.syncActionState();
                }""")
                page.locator("#btn-save-send").click()
                expect(page.locator("#confirm-panel")).to_be_visible()
                page.locator("#confirm-accept").click()
                expect(page.locator("#confirm-title")).to_have_text("Estado do envio")
                message = page.locator("#confirm-message").inner_text()
                assert "synthetic-job" in message and "PRIVATE_PROVIDER_DETAIL" not in message
                assert "Invalid Date" not in message
                if scenario == "scheduled":
                    assert "Próxima consulta a partir de" in message
                elif scenario == "distant":
                    assert "prazo indicado pelo serviço é muito distante" in message
                else:
                    assert "Próxima consulta" not in message
                expect(page.locator("#confirm-accept")).to_have_text("Fechar")
                page.screenshot(path=str(args.output / f"mail-monitor-{scenario}.png"), full_page=True)
                page.locator("#confirm-accept").click()
                expect(page.locator("#confirm-panel")).to_be_hidden()
                assert calls == [("POST", "/api/file/synthetic/send"), ("GET", "/api/graph/jobs/synthetic-job")], calls
                assert not page_errors, page_errors
                assert not blocked, blocked
                results.append({"scenario": scenario, "result": "pass", "requests": len(calls)})
                context.close()
        finally:
            browser.close()
    print(json.dumps({"browser": "Chromium", "synthetic": True, "results": results}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
