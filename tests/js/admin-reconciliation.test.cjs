const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../../src/web/static/js/admin.js"), "utf8");
const settle = () => new Promise(resolve => setImmediate(resolve));

async function panel({repair = false, failure = null} = {}) {
    const elements = new Map(), requests = [];
    const element = () => ({textContent:"", value:"", files:[], children:[], events:{}, dataset:{}, disabled:false,
        classList:{add(){}}, append(child){this.children.push(child);}, replaceChildren(){this.children=[];},
        addEventListener(name, fn){this.events[name]=fn;}, showModal(){this.open=true;}, close(){this.open=false;}, focus(){}});
    const get = id => { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); };
    const document = {body:{dataset:{adminCsrf:"synthetic-csrf"}}, getElementById:get, createElement:element};
    const overview = {success:true, jobs:[], documents:[], audit:[], refresh:null, queue_summary:null};
    const fetch = async (url, options = {}) => {
        requests.push({url, ...options});
        if (options.method === "POST") return {ok:!failure, json:async () => failure || {success:true, result:{applied:repair}}};
        return {ok:true, json:async () => url.endsWith("/reconciliation") ? {success:true, reconciliation:{
            id:"send:synthetic", kind:"archive_and_mail_local", status:repair ? "complete" : "failed",
            revision:"current-revision", can_record:!repair, can_repair_teams:repair, history:[]}} : overview};
    };
    vm.runInNewContext(source, {document, fetch, Date});
    await settle();
    get("admin-job-id").value = "send:synthetic";
    await get("admin-reconciliation-lookup").events.submit({preventDefault(){}});
    await settle();
    get("admin-reconciliation-reason").value = " Provider record checked ";
    return {get, requests, submit:async () => {
        await get("admin-reconciliation-form").events.submit({preventDefault(){}});
        await settle();
    }};
}

test("indeterminate decision binds the current revision and sends no replay request", async () => {
    const ui = await panel();
    assert.equal(ui.get("admin-decision").value, "indeterminate");
    await ui.submit();
    const posts = ui.requests.filter(x => x.method === "POST");
    assert.equal(posts.length, 1);
    assert.match(posts[0].url, /send%3Asynthetic\/reconciliation$/);
    assert.equal(posts[0].headers["X-Admin-CSRF"], "synthetic-csrf");
    assert.deepEqual(JSON.parse(posts[0].body), {revision:"current-revision", decision:"indeterminate", reason:"Provider record checked", evidence:null});
    assert.match(ui.get("admin-status").textContent, /retenção mantida/);
});

test("acceptance cannot be submitted without a bounded valid evidence file", async () => {
    const ui = await panel();
    ui.get("admin-decision").value = "accepted";
    await ui.submit();
    assert.match(ui.get("admin-reconciliation-error").textContent, /Anexe/);
    ui.get("admin-evidence").files = [{size:65537, text:async () => "{}"}];
    await ui.submit();
    ui.get("admin-evidence").files = [{size:12, text:async () => "private-invalid-json"}];
    await ui.submit();
    assert.match(ui.get("admin-reconciliation-error").textContent, /JSON válido/);
    assert.doesNotMatch(ui.get("admin-reconciliation-error").textContent, /private-invalid/);
    assert.equal(ui.requests.filter(x => x.method === "POST").length, 0);
});

test("evidence is sent once and is not rendered or retried after a stale decision", async () => {
    const ui = await panel({failure:{success:false, error:"A operação mudou. Atualize o diagnóstico."}});
    ui.get("admin-decision").value = "rejected";
    ui.get("admin-evidence").files = [{size:10, text:async () => '{"reference":"private-reference"}'}];
    await ui.submit();
    assert.equal(ui.requests.filter(x => x.method === "POST").length, 1);
    assert.equal(ui.get("admin-reconciliation").open, true);
    assert.match(ui.get("admin-reconciliation-error").textContent, /mudou/);
    assert.doesNotMatch(ui.get("admin-reconciliation-error").textContent, /private-reference/);
});

test("accepted parent offers only independent Teams repair without an evidence file", async () => {
    const ui = await panel({repair:true});
    assert.equal(ui.get("admin-accepted").disabled, true);
    assert.equal(ui.get("admin-decision").value, "repair_teams");
    await ui.submit();
    const posts = ui.requests.filter(x => x.method === "POST");
    assert.equal(posts.length, 1);
    assert.equal(JSON.parse(posts[0].body).decision, "repair_teams");
    assert.doesNotMatch(posts[0].url, /\/retry$/);
});
