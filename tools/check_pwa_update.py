"""Finite browser check of PWA A -> B, using public source assets and synthetic HTML only."""
from __future__ import annotations
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

def wait_async(page, expression):
    """Await each async browser predicate before deciding whether to poll again."""
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if page.evaluate(expression):
            return
        page.wait_for_timeout(100)
    raise AssertionError("PWA browser state deadline: " + expression)

def main():
    from playwright.sync_api import sync_playwright, expect
    from tools.serve_pwa_synthetic import FixtureHandler
    from tools.test_runtime import block_outbound_network
    output = ROOT / "output/playwright"
    output.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as playwright:
        block_outbound_network()  # After Playwright's Windows loopback IPC is initialized.
        FixtureHandler.release = "a" * 64
        server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        browser = playwright.chromium.launch(headless=True, args=[
            "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1, EXCLUDE localhost",
        ])
        try:
            context = browser.new_context(service_workers="allow", viewport={"width":1280,"height":900})
            # Use actual loopback HTTP. The fixture and SW contain only same-origin URLs;
            # DNS for every other host is disabled by the browser launch option.
            page = context.new_page()
            page.set_default_timeout(15000)
            page.goto(base)
            wait_async(page, "async()=>(await navigator.serviceWorker.getRegistration())?.active?.state==='activated'")
            assert not page.evaluate("!!navigator.serviceWorker.controller")
            page.goto(base + "/document")
            page.wait_for_function("!!navigator.serviceWorker.controller", timeout=10000)
            results.append("A installs and only controls a later navigation")
            print(results[-1], flush=True)
            page.get_by_role("textbox", name="Trabalho por guardar").fill("Texto sintético ainda por guardar")
            page.evaluate("()=>fetch('/__test/release/b',{method:'POST'}).then(r=>r.text())")
            page.evaluate("async()=>await (await navigator.serviceWorker.getRegistration()).update()")
            wait_async(page, "async()=>!!(await navigator.serviceWorker.getRegistration())?.waiting")
            expect(page.get_by_role("status")).to_contain_text("guardado no servidor")
            expect(page.get_by_role("textbox")).to_have_value("Texto sintético ainda por guardar")
            assert page.evaluate("async()=>{const r=await navigator.serviceWorker.getRegistration();return r.active===navigator.serviceWorker.controller && !!r.waiting;}")
            page.screenshot(path=str(output / "m19-update-waiting.png"), full_page=True)
            results.append("B waits; A remains the controller; update notice leaves unsaved text intact")
            print(results[-1], flush=True)
            version_a, version_b = "a"*16, "b"*16
            online = page.evaluate("""async version=>{
                const r=await fetch('/static/js/pwa.js?v='+version);
                return {status:r.status,identity:r.headers.get('X-App-Version')};
            }""", version_b)
            assert online == {"status":200,"identity":"b"*64}
            results.append("Old controller passes verified B assets online")
            context.set_offline(True)
            offline = page.evaluate("""async versions=>{
                const result={};
                for(const version of versions) {
                    const r=await fetch('/static/js/pwa.js?v='+version);
                    result[version]=r.status;
                }
                try {await fetch('/api/files');result.api='unexpected success';}
                catch (_) {result.api='network failure';}
                return result;
            }""", [version_a, version_b])
            assert offline == {version_a:200, version_b:503, "api":"network failure"}
            expect(page.get_by_role("textbox")).to_have_value("Texto sintético ainda por guardar")
            results.append("Offline: A exact resource works; B returns 503; API has no cached success; text remains")
            print(results[-1], flush=True)
            other = context.new_page()
            other.goto(base + "/private.pdf")
            expect(other.get_by_role("heading", name="Sem ligação ao servidor")).to_be_visible()
            other.screenshot(path=str(output / "m19-offline-public.png"), full_page=True)
            results.append("Offline private navigation only yields public offline page")
            other.close()
            context.set_offline(False)
            keys = page.evaluate("""async()=>{
                const data={};
                for(const key of await caches.keys()) data[key]=(await (await caches.open(key)).keys()).map(r=>r.url);
                return data;
            }""")
            for key, urls in keys.items():
                version = ("a" if key.endswith("a"*64) else "b")*16
                assert len(urls) >= 20
                assert all(url.endswith("?v="+version) for url in urls)
                assert all(urlsplit(url).path.startswith("/static/") or urlsplit(url).path=="/manifest.webmanifest" for url in urls)
            # Keep only an unrelated blank tab so the browser/context is not destroyed.
            blank = context.new_page()
            page.close()
            # A genuinely closed old tab permits activation. No skipWaiting message is sent.
            blank.goto(base)
            wait_async(blank, "async()=>{const r=await navigator.serviceWorker.getRegistration();return r?.active?.state==='activated' && !r.waiting;}")
            expect(blank.get_by_text("Versão sintética B", exact=True)).to_be_visible()
            wait_async(blank, "async()=>(await caches.keys()).every(k=>!k.endsWith('a'.repeat(64)))")
            current = blank.evaluate("async()=>await caches.keys()")
            assert current == ["sensorpoint-service-static-"+"b"*64]
            results.append("Closing controlled A tabs activates B and removes only the old application cache")
            print(results[-1], flush=True)
            payload = {"browser":browser.version,"results":results,"cache_keys":current,
                       "note":"Synthetic release identities; real public SW/PWA assets, no operational app or provider"}
            (output / "m19-browser-result.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
            print(json.dumps(payload,ensure_ascii=False),flush=True)
            context.close()
        finally:
            browser.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

