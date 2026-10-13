const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const model = require('../../src/web/static/js/maintenance-model.js');
const read = name => fs.readFileSync(path.join(__dirname, '../../src/web/static/js', name), 'utf8');
const editorSource = read('maintenance-editor.js');
const start = editorSource.indexOf('    async function verifyRecovery(');
const verifySource = editorSource.slice(start, editorSource.indexOf('\n    function ', start + 10));
const functionSource = name => {
    const begin = editorSource.indexOf(`    function ${name}(`);
    assert.notEqual(begin, -1);
    return editorSource.slice(begin, editorSource.indexOf('\n    function ', begin + 10));
};
const waitFor = async predicate => {
    for (let i=0;i<300;i++) { if(predicate()) return; await new Promise(resolve=>setTimeout(resolve,5)); }
    throw new Error('Expected recovery state was not reached');
};
const signed = token => ({customer_name:'Cliente fictício', observations:'alteração local',
    'Assinatura Cliente':'synthetic-service-ink',
    maintenance_checklists:[{id:'site-a',location:'Local fictício',signatures:{
        technician:{token,image:'synthetic-ink'}, customer:{token,image:'synthetic-ink'}},signature_drafts:{}}]});
const normalize = payload => {
    const result=structuredClone(payload);
    for(const site of result.maintenance_checklists || []) {
        site.signature_warnings ||= {};
        for(const role of ['technician','customer']) {
            if(site.signatures?.[role]?.token === 'old') {
                delete site.signatures[role]; delete site.signature_drafts?.[role];
                site.signature_warnings[role]='verification_failed';
            }
        }
    }
    return result;
};

// Minimal asynchronous IndexedDB interface; the real coordinator reads cursors,
// stores recovery records and handles transactions, not substituted functions.
function database(records) {
    return {open() {
        const request={};
        setImmediate(()=>{request.result={close(){},transaction() {
            const transaction={};
            const done=()=>setImmediate(()=>transaction.oncomplete?.());
            const cursorRequest = values => {
                const cursor={}; let index=0;
                const next=()=>setImmediate(()=>{
                    const value=values[index++];
                    cursor.result=value ? {value:structuredClone(value),continue:next,delete:()=>records.delete(value.key)} : null;
                    cursor.onsuccess?.(); if(!value) done();
                });
                next(); return cursor;
            };
            transaction.objectStore=()=>({
                put(record){records.set(record.key,structuredClone(record));done();},
                delete(key){records.delete(key);done();},
                index(){return {openCursor:user=>cursorRequest([...records.values()].filter(r=>r.userId===user))};},
                openCursor:()=>cursorRequest([...records.values()])
            });
            return transaction;
        }};request.onsuccess?.();});
        return request;
    }};
}

function harness(t, {local, remote, failVerification=false, failBootstrap=false, deferSave=false}) {
    const records=new Map();
    if(local)records.set('user:doc',{key:'user:doc',userId:'user',fileName:'synthetic-draft',documentId:'doc',
        baseRevision:1,updatedAt:Date.now(),payload:structuredClone(local)});
    const elements=new Map(), listeners=new Map(), timers=new Set();
    const classes=()=>{const values=new Set();return {add:name=>values.add(name),remove:name=>values.delete(name),
        toggle:(name,value)=>value ? values.add(name) : values.delete(name),contains:name=>values.has(name)};};
    const element=id=>{
        if(!elements.has(id))elements.set(id,{disabled:true,hidden:false,textContent:'',dataset:{},classList:classes(),events:new Map(),
            addEventListener(name,fn){this.events.set(name,fn);},querySelectorAll:()=>[]});
        return elements.get(id);
    };
    let payload={}, pendingSave, saveCount=0, verifyCount=0;
    const reply=value=>({ok:true,status:200,json:async()=>value});
    const fetch=async (url, options) => {
        if(url.includes('/bootstrap?')) {
            if(failBootstrap)throw new Error('synthetic bootstrap network failure');
            return reply({success:true,is_draft:true,document:remote,
                editing:{document_id:'doc',revision:1,server_document:remote,lease:{token:'synthetic-lease'}}});
        }
        if(url.endsWith('/maintenance/validate')) {
            verifyCount++;
            if(failVerification)throw new Error('synthetic network failure');
            return reply({success:true,maintenance_checklists:normalize(JSON.parse(options.body).document).maintenance_checklists});
        }
        assert.ok(url.endsWith('/autosave'),url);
        saveCount++;
        const submitted=JSON.parse(options.body).document;
        const value=reply({success:true,editing:{revision:2,server_document:normalize(submitted)}});
        if(deferSave)return new Promise(resolve=>{pendingSave=()=>resolve(value);});
        return value;
    };
    const signatureNotice={}, invalidatedSignatures=new Map();
    const view={sites:[],signatureNotice,invalidatedSignatures,model,
        def:{signature_exceptions:{},signature_verification_warning:'A chave de segurança pode ter mudado; recolha uma nova assinatura.',
            signature_pending_warning:'Assinatura por confirmar no servidor. Volte a tentar com ligação.'}};
    vm.runInNewContext(`${functionSource('restoreSignatureWarnings')}\n${functionSource('renderSignatureNotice')}\nthis.refresh=()=>{restoreSignatureWarnings();renderSignatureNotice();};`,view);
    const refresh=()=>{view.sites=payload.maintenance_checklists || [];view.refresh();};
    const verification={fetch,config:{selectedFileIsDraft:true,selectedFileName:'synthetic-draft'},encodeURIComponent};
    vm.runInNewContext(`${verifySource}\nthis.verify=verifyRecovery;`,verification);
    const indexedDB=database(records);
    const window={__FILES_APP__:{selectedFileName:'synthetic-draft',selectedFileIsDraft:true,editorUser:{id:'user'}},
        __FILES_EDITOR__:{collectFormData:()=>structuredClone(payload),
            populateForm:(_name,value)=>{payload=structuredClone(value);refresh();},
            verifyRecovery:verification.verify,
            pendingRecovery:model.pendingRecovery,
            reconcileSignatures:(submitted,verified)=>{
                const result=model.reconcileSignatures(payload.maintenance_checklists,submitted.maintenance_checklists,verified.maintenance_checklists);
                refresh();return result.length;
            },showToast(){},renderAttachments(){},hasPendingPhotoChanges:()=>false},
        crypto:{randomUUID:()=>require('node:crypto').randomUUID()},indexedDB,
        setTimeout:(fn,delay)=>{const timer=setTimeout(fn,delay);timers.add(timer);return timer;},clearTimeout,
        setInterval:()=>({unref(){}}),clearInterval(){},addEventListener:(name,fn)=>listeners.set(name,fn),location:{}};
    const document={readyState:'complete',hidden:false,body:{classList:classes()},getElementById:element,
        querySelector:()=>null,addEventListener:(name,fn)=>listeners.set(name,fn)};
    vm.runInNewContext(read('editing-coordinator.js'),{window,document,fetch,console,Blob,indexedDB,
        IDBKeyRange:{only:value=>value},navigator:{onLine:true,sendBeacon:()=>true},
        sessionStorage:{getItem:()=>null,setItem(){}},setTimeout,clearTimeout});
    t.after(()=>{for(const timer of timers)clearTimeout(timer);});
    return {records,element,notice:signatureNotice,coordinator:window.__EDITING_COORDINATOR__,
        payload:()=>payload,setPayload:value=>{payload=structuredClone(value);refresh();},
        saveCount:()=>saveCount,verifyCount:()=>verifyCount,finishSave:()=>pendingSave?.(),pendingSave:()=>Boolean(pendingSave),listeners,
        recoverNetwork:()=>{failVerification=false;failBootstrap=false;}};
}

test('IndexedDB recovery is verified before hydration; rejected proofs never return to the editor or recovery',async t=>{
    const local=signed('old'), remote=normalize(local);remote.observations='versão anterior';
    const app=harness(t,{local,remote});
    await waitFor(()=>app.element('btn-save-draft').disabled===false);
    assert.equal(app.verifyCount(),1);
    assert.equal(app.payload().observations,'alteração local');
    assert.equal(app.notice.hidden,false);assert.match(app.notice.textContent,/chave de segurança/);
    assert.equal(model.signatureErrors(app.payload().maintenance_checklists[0],{}).length,2);
    assert.equal(Object.keys(app.payload().maintenance_checklists[0].signatures).length,0);
    await waitFor(()=>app.saveCount()===1 && app.element('autosave-status').dataset.state==='saved');
    const stored=app.records.get('user:doc').payload;
    assert.equal(Object.keys(stored.maintenance_checklists[0].signatures).length,0);
    assert.equal(stored.maintenance_checklists[0].signature_warnings.technician,'verification_failed');
});

test('valid signature existing only in local recovery survives server verification',async t=>{
    const local=signed('fresh-valid'),remote=normalize(signed('old'));
    const app=harness(t,{local,remote});
    await waitFor(()=>app.element('btn-save-draft').disabled===false);
    assert.equal(app.payload().maintenance_checklists[0].signatures.technician.token,'fresh-valid');
    assert.equal(app.notice.hidden,true);
});

test('autosave rejection updates the UI and recovery while preserving edits and a later signature',async t=>{
    const app=harness(t,{remote:signed('old'),deferSave:true});
    await waitFor(()=>app.element('btn-save-draft').disabled===false);
    app.coordinator.markDirty();
    await waitFor(app.pendingSave);
    const newer=structuredClone(app.payload());
    newer.observations='edit while saving';
    newer.maintenance_checklists[0].signatures.customer={token:'fresh-valid',image:'fresh-ink'};
    app.setPayload(newer);app.finishSave();
    await waitFor(()=>app.payload().maintenance_checklists[0].signature_warnings?.technician);
    assert.equal(app.payload().observations,'edit while saving');
    assert.equal(app.payload().maintenance_checklists[0].signatures.customer.token,'fresh-valid');
    assert.equal(app.notice.hidden,false);
    await waitFor(()=>app.records.get('user:doc')?.payload.maintenance_checklists[0].signature_warnings?.technician);
});

test('failed recovery verification preserves the original copy and disables commit operations',async t=>{
    const local=signed('old');
    const app=harness(t,{local,remote:normalize(local),failVerification:true});
    await waitFor(()=>app.element('autosave-status').dataset.state==='offline');
    assert.equal(app.element('btn-save-draft').disabled,true);
    assert.equal(app.element('service-form').classList.contains('is-readonly'),true);
    assert.equal(Object.keys(app.payload().maintenance_checklists[0].signatures).length,0);
    assert.equal(app.notice.hidden,false);
    assert.equal(app.records.get('user:doc').payload.maintenance_checklists[0].signatures.technician.token,'old');
    app.coordinator.markDirty();
    assert.equal(app.coordinator.isDirty(),false);
    assert.equal(app.saveCount(),0);
    assert.equal(app.element('btn-retry-lease').hidden,false);
    app.recoverNetwork();
    await app.element('btn-retry-lease').events.get('click')();
    assert.equal(app.verifyCount(),2);
    assert.equal(app.element('btn-save-draft').disabled,false);
    assert.equal(app.element('service-form').classList.contains('is-readonly'),false);
    assert.equal(Object.keys(app.records.get('user:doc').payload.maintenance_checklists[0].signatures).length,0);
});

test('offline bootstrap shows a read-only preview without claiming local signatures are saved',async t=>{
    const local=signed('fresh-valid');
    const app=harness(t,{local,remote:normalize(signed('old')),failBootstrap:true});
    await waitFor(()=>app.element('autosave-status').dataset.state==='offline');
    assert.equal(app.element('service-form').classList.contains('is-readonly'),true);
    assert.equal(app.element('btn-save-draft').disabled,true);
    assert.equal(Object.keys(app.payload().maintenance_checklists[0].signatures).length,0);
    assert.match(app.notice.textContent,/por confirmar no servidor/);
    assert.equal(app.records.get('user:doc').payload.maintenance_checklists[0].signatures.technician.token,'fresh-valid');
    app.coordinator.markDirty();assert.equal(app.coordinator.isDirty(),false);
    app.recoverNetwork();
    await app.element('btn-retry-lease').events.get('click')();
    assert.equal(app.element('btn-save-draft').disabled,false);
    assert.equal(app.payload().maintenance_checklists[0].signatures.technician.token,'fresh-valid');
    assert.equal(app.notice.hidden,true);
});

test('unchanged autosave replaces rejected proofs before marking saved and storing recovery',async t=>{
    const app=harness(t,{remote:signed('old')});
    await waitFor(()=>app.element('btn-save-draft').disabled===false);
    app.coordinator.markDirty();
    await waitFor(()=>app.saveCount()===1 && app.element('autosave-status').dataset.state==='saved');
    assert.equal(Object.keys(app.payload().maintenance_checklists[0].signatures).length,0);
    assert.equal(app.notice.hidden,false);
    assert.equal(Object.keys(app.records.get('user:doc').payload.maintenance_checklists[0].signatures).length,0);
    assert.equal(app.saveCount(),1);
});

test('resume checks the suspended page and refreshes its recovery without replacing other edits',async t=>{
    const app=harness(t,{remote:signed('old')});
    await waitFor(()=>app.element('btn-save-draft').disabled===false);
    app.listeners.get('freeze')();
    app.listeners.get('resume')();
    await waitFor(()=>app.verifyCount()===1 && app.element('btn-save-draft').disabled===false);
    assert.equal(app.notice.hidden,false);
    assert.equal(app.payload().observations,'alteração local');
    assert.equal(Object.keys(app.records.get('user:doc').payload.maintenance_checklists[0].signatures).length,0);
});

test('late rejection never erases a new unsigned capture or an unrelated site',()=>{
    const submitted=signed('old'), current=structuredClone(submitted), verified=normalize(submitted);
    current.maintenance_checklists[0].signature_drafts.technician={image:'new-drawing'};
    current.maintenance_checklists.push({id:'new-site',signatures:{technician:{token:'local-proof'}}});
    model.reconcileSignatures(current.maintenance_checklists,submitted.maintenance_checklists,verified.maintenance_checklists);
    assert.equal(current.maintenance_checklists[0].signature_drafts.technician.image,'new-drawing');
    assert.equal(current.maintenance_checklists[1].signatures.technician.token,'local-proof');
});
