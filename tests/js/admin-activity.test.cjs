const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../../src/web/static/js/admin.js"), "utf8");
const settle = () => new Promise(resolve => setImmediate(resolve));
const entry = (overrides = {}) => ({sheet_name:"2026_9901_Fictícia", actor_name:"Ana Fictícia", kind:"send",
    created_at:100, updated_at:150, status:"complete", queue_status:"complete", mail_status:"accepted", ...overrides});
async function panel() {
    const elements = new Map(), requests = [], pending = [];
    const element = () => ({textContent:"", value:"all", dataset:{}, children:[], events:{},
        classList:{add(){}}, append(child){this.children.push(child);}, replaceChildren(){this.children=[];},
        setAttribute(){}, addEventListener(name,fn){this.events[name]=fn;}, focus(){}});
    const get = id => {if (!elements.has(id)) elements.set(id, element()); return elements.get(id);};
    const document = {body:{dataset:{adminCsrf:"synthetic"}}, getElementById:get, createElement:element};
    const fetch = async (url, options) => {
        requests.push({url, options});
        if (url.startsWith("/api/admin/activity")) return new Promise((resolve,reject) => pending.push({resolve,reject}));
        return {ok:true, json:async () => ({success:true, documents:[], jobs:[], audit:[], refresh:null})};
    };
    vm.runInNewContext(source, {document, fetch, Date});
    await settle();
    return {get, requests, pending, deliver:async (index, entries, extra = {}) => {
        pending[index].resolve({ok:true, json:async () => ({success:true, activity:{entries, limit:100, has_more:false,
            unavailable_documents:0, ...extra}})}); await settle();
    }};
}
const text = element => [element.textContent, ...element.children.map(text)].join(" ");

test("history fetches on opening and changing filters; a local completion is not an accepted email", async () => {
    const ui = await panel();
    assert.equal(ui.requests.length, 1);
    ui.get("admin-tab-audit").events.click();
    await ui.deliver(0, [entry({queue_status:"none", mail_status:"not_recorded"})]);
    assert.match(text(ui.get("admin-activity")), /Ana Fictícia.*Pedido:.*Sem registo de envio/);
    assert.doesNotMatch(text(ui.get("admin-activity")), /E-mail aceite/);
    ui.get("admin-activity-kind").value = "send";
    ui.get("admin-activity-kind").events.change();
    assert.equal(ui.requests.at(-1).url, "/api/admin/activity?kind=send");
    await ui.deliver(1, [entry(), entry({queue_status:"pending", mail_status:"pending"})]);
    assert.match(text(ui.get("admin-activity")), /E-mail aceite pelo serviço/);
    assert.equal(ui.get("admin-activity").children[1].children[0].children[1].dataset.tone, "neutral");
    assert.ok(ui.requests.every(r => !r.options.method));
});

test("rapid filter changes ignore an older response and its error", async () => {
    const ui = await panel();
    ui.get("admin-tab-audit").events.click();
    ui.get("admin-activity-kind").value = "cancel";
    ui.get("admin-activity-kind").events.change();
    await ui.deliver(1, [entry({kind:"cancel", sheet_name:"Cancelamento atual"})]);
    await ui.deliver(0, [entry({sheet_name:"Resposta antiga"})]);
    assert.match(text(ui.get("admin-activity")), /Cancelamento atual/);
    assert.doesNotMatch(text(ui.get("admin-activity")), /Resposta antiga/);
    ui.get("admin-activity-reload").events.click();
    ui.get("admin-activity-kind").events.change();
    await ui.deliver(3, []);
    ui.pending[2].reject(new Error("Erro antigo")); await settle();
    assert.equal(ui.get("admin-activity-status").dataset.state, "ready");
});

test("uncertain cancellation requires reconciliation and a superseded publication is explicit", async () => {
    const ui = await panel();
    ui.get("admin-tab-audit").events.click();
    await ui.deliver(0, [entry({kind:"cancel", queue_status:"failed", reconciliation_required:true}),
        entry({kind:"draft", queue_status:"superseded"})]);
    const cards = ui.get("admin-activity").children;
    assert.match(text(cards[0]), /Resultado por confirmar.*Requer reconciliação/);
    assert.equal(cards[0].children[0].children[1].dataset.tone, "warning");
    assert.doesNotMatch(text(cards[0]), /Falha no processamento/);
    assert.match(text(cards[1]), /Substituída por publicação mais recente/);
    assert.equal(cards[1].children[0].children[1].dataset.tone, "neutral");
});

test("failures leave history retryable; empty, limited and unavailable histories are explicit", async () => {
    const ui = await panel();
    ui.get("admin-tab-audit").events.click();
    ui.pending[0].reject(new Error("Sem ligação")); await settle();
    assert.match(ui.get("admin-activity-status").textContent, /Sem ligação/);
    assert.equal(ui.get("admin-activity-reload").disabled, false);
    ui.get("admin-activity-reload").events.click();
    await ui.deliver(1, []);
    assert.match(text(ui.get("admin-activity")), /Não existem pedidos/);
    ui.get("admin-activity-reload").events.click();
    await ui.deliver(2, [entry({actor_name:"<img src=x onerror=alert(1)>"})], {has_more:true, unavailable_documents:1});
    assert.match(ui.get("admin-activity-status").textContent, /100 mais recentes.*recuperação/);
    assert.match(text(ui.get("admin-activity")), /<img/);
    assert.equal(ui.get("admin-activity").children[0].children[1].innerHTML, undefined);
});
