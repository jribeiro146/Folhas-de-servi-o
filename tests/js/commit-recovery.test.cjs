const test = require('node:test');
const assert = require('node:assert/strict');
const {create, PREFIX} = require('../../src/web/static/js/commit-recovery.js');
const scope = 'a'.repeat(64);
const storage = () => { const values = new Map(); return {
    getItem: key => values.get(key) || null, setItem: (key,value) => values.set(key,value),
    removeItem: key => values.delete(key), values}; };
const metadata = {document_id:'doc-synthetic', idempotency_key:'operation-synthetic', lease_token:'SECRET-NOT-RETAINED'};

test('damaged references preserve evidence and do not break normal bootstrap or other actions', async () => {
    for (const raw of ['{', 'null', 'x'.repeat(2049), JSON.stringify({scope,kind:'send',documentId:'invalid/id'})]) {
        const store = storage();
        store.setItem(PREFIX + 'synthetic', raw);
        const tracker = create({scope, storage:store, fetchFn:() => assert.fail('Invalid reference must not query')});
        assert.equal(tracker.read('synthetic'), null);
        assert.equal(await tracker.lookup('synthetic'), null);
        tracker.clear('synthetic');
        assert.equal(store.getItem(PREFIX + 'synthetic'), raw);
    }
});

for (const kind of ['send', 'draft']) test('a reload recovers the completed ' + kind + ' through GET only, without retaining form or lease', async () => {
    const store = storage(), calls = [];
    const fetchFn = async (url, options) => {calls.push({url, options}); return {ok:true, status:200,
        json:async () => ({success:true, operation:{kind, status:'complete', result:{success:true, graph_job_id:'send:one'}}})};};
    const first = create({scope, storage:store, fetchFn});
    assert.deepEqual(first.begin('synthetic', metadata, kind), {'X-Mail-Monitor-Scope':scope});
    assert.doesNotMatch([...store.values.values()].join(), /SECRET|lease_token/);
    const afterReload = create({scope, storage:store, fetchFn});
    const receipt = await afterReload.lookup('synthetic');
    assert.equal(receipt.result.graph_job_id, 'send:one');
    assert.equal(calls.length, 1);
    assert.equal(calls[0].options.method, undefined);
    assert.equal(calls[0].options.headers['X-Mail-Monitor-Scope'], scope);
    assert.equal(calls[0].options.cache, 'no-store');
    afterReload.clear('synthetic');
    assert.equal(afterReload.read('synthetic'), null);
});

test('another session never reads or erases the previous account reference', async () => {
    const store = storage();
    create({scope, storage:store}).begin('synthetic', metadata);
    const other = create({scope:'b'.repeat(64), storage:store, fetchFn:() => assert.fail('Foreign query')});
    assert.equal(await other.lookup('synthetic'), null);
    other.clear('synthetic');
    assert.equal(store.values.size, 1);
});

test('unavailable storage refuses before any send can be prepared', () => {
    const tracker = create({scope, storage:{setItem() {throw new Error('quota');}}});
    assert.throws(() => tracker.begin('synthetic', metadata), /não foi enviada/);
});

test('missing operation retains the same reference for explicit retry', async () => {
    const store = storage();
    const tracker = create({scope, storage:store, fetchFn:async () => ({status:404})});
    tracker.begin('synthetic', metadata);
    assert.equal((await tracker.lookup('synthetic')).status, 'missing');
    assert.equal(tracker.read('synthetic').operationId, metadata.idempotency_key);
});

test('changed session requests reload and preserves the original opaque receipt', async () => {
    const store = storage();
    const tracker = create({scope, storage:store, fetchFn:async () => ({status:403,
        json:async () => ({success:false,code:'mail_monitor_session_changed'})})});
    tracker.begin('synthetic', metadata);
    await assert.rejects(() => tracker.lookup('synthetic'), error =>
        error.code === 'mail_monitor_session_changed' && /Recarregue/.test(error.message));
    assert.equal(tracker.read('synthetic').operationId, metadata.idempotency_key);
});

test('unavailable or malformed result never erases the pending reference', async () => {
    for (const response of [{ok:false,status:503}, {ok:true,status:200,json:async () => ({success:true,operation:{status:'complete'}})}]) {
        const store = storage();
        const tracker = create({scope, storage:store, fetchFn:async () => response});
        tracker.begin('synthetic', metadata);
        await assert.rejects(() => tracker.lookup('synthetic'));
        assert.equal(tracker.read('synthetic').operationId, metadata.idempotency_key);
    }
});
