const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../../src/web/static/js/admin.js"), "utf8");
const settle = () => new Promise(resolve => setImmediate(resolve));

async function panel(extra = {}) {
    const elements = new Map(), requests = [];
    let fail = false;
    const element = () => ({textContent:"", dataset:{}, children:[], events:{}, attributes:{},
        classList:{add(){}}, append(child){this.children.push(child);}, replaceChildren(){this.children=[];},
        setAttribute(name,value){this.attributes[name]=value;}, addEventListener(name,fn){this.events[name]=fn;},
        focus(){this.focused=true;}});
    const get = id => {if (!elements.has(id)) elements.set(id, element()); return elements.get(id);};
    const document = {body:{dataset:{adminCsrf:"synthetic"}}, getElementById:get, createElement:element};
    const fetch = async (url, options) => {
        requests.push({url, options});
        if (fail) throw new Error("Ligação interrompida.");
        return {ok:true, json:async () => ({success:true, documents:[], jobs:[], audit:[], refresh:null,
            queue_summary:null, ...extra})};
    };
    vm.runInNewContext(source, {document, fetch, Date});
    await settle();
    return {get, requests, fail:() => {fail=true;}};
}

test("switching areas is local and reloading diagnostics preserves the selected area", async () => {
    const ui = await panel();
    ui.get("admin-tab-jobs").events.click();
    assert.equal(ui.requests.length, 1);
    assert.equal(ui.get("admin-panel-documents").hidden, true);
    assert.equal(ui.get("admin-panel-jobs").hidden, false);
    assert.equal(ui.get("admin-tab-jobs").attributes["aria-selected"], "true");
    assert.equal(ui.get("admin-tab-jobs").tabIndex, 0);
    assert.equal(ui.get("admin-tab-documents").tabIndex, -1);
    await ui.get("admin-reload").events.click();
    assert.equal(ui.requests.length, 2);
    assert.equal(ui.get("admin-panel-jobs").hidden, false);
    assert.ok(ui.requests.every(item => item.url === "/api/admin/overview" && !item.options.method));
});

test("keyboard arrows wrap and Home/End select and focus the matching tab", async () => {
    const ui = await panel();
    for (const [from, key, to] of [["documents","ArrowLeft","audit"], ["audit","ArrowRight","documents"],
        ["documents","End","audit"], ["audit","Home","documents"]]) {
        let prevented = false;
        ui.get("admin-tab-" + from).events.keydown({key, preventDefault(){prevented=true;}});
        assert.equal(prevented, true);
        assert.equal(ui.get("admin-panel-" + to).hidden, false);
        assert.equal(ui.get("admin-tab-" + to).focused, true);
    }
    let prevented = false;
    ui.get("admin-tab-documents").events.keydown({key:"Tab", preventDefault(){prevented=true;}});
    assert.equal(prevented, false);
});

test("summary uses complete queue totals rather than the limited visible job list", async () => {
    const ui = await panel({queue_summary:{pending:120, running:2, failed:31}, worker:{state:"stale"}});
    assert.equal(ui.get("admin-count-pending").textContent, 122);
    assert.equal(ui.get("admin-count-failed").textContent, 31);
    assert.equal(ui.get("admin-worker-state").textContent, "Verificar estado");
    const inactive = await panel();
    assert.equal(inactive.get("admin-count-pending").textContent, "—");
    assert.equal(inactive.get("admin-count-failed").textContent, "—");
    assert.equal(inactive.get("admin-worker-state").textContent, "Sem diagnóstico");
});

test("failed refresh labels previous data stale and leaves reload and navigation usable", async () => {
    const ui = await panel({queue_summary:{failed:2}});
    ui.fail();
    await ui.get("admin-reload").events.click();
    assert.equal(ui.get("admin-count-failed").textContent, 2);
    assert.equal(ui.get("admin-status").dataset.state, "error");
    assert.match(ui.get("admin-status").textContent, /desatualizada.*Ligação interrompida/);
    assert.equal(ui.get("admin-reload").disabled, false);
    ui.get("admin-tab-audit").events.click();
    assert.equal(ui.get("admin-panel-audit").hidden, false);
});

test("a failed reconciliation lookup shows feedback beside the lookup form", async () => {
    const ui = await panel();
    ui.fail();
    ui.get("admin-job-id").value = "synthetic-missing";
    ui.get("admin-reconciliation-lookup").events.submit({preventDefault(){}});
    await settle();
    assert.equal(ui.get("admin-lookup-status").textContent, "Ligação interrompida.");
    assert.equal(ui.get("admin-status").dataset.state, "error");
    assert.ok(ui.requests.every(item => !item.options.method));
});
