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

function broadcastBus({dropClosing=false}={}) {
    const channels=new Set();
    return class {
        constructor() {channels.add(this);}
        postMessage(data) {
            if(dropClosing && data.type==='closing')return;
            for(const channel of channels)if(channel!==this)setImmediate(()=>channel.onmessage?.({data:structuredClone(data)}));
        }
        close() {channels.delete(this);}
    };
}

function harness(t, {local, remote, failVerification=false, failBootstrap=false, deferSave=false, BroadcastChannel, realCollector=false,
    records=new Map(), deferVerification=false, sharedServer=null, commitRecovery}) {
    if(local)records.set('user:doc',{key:'user:doc',userId:'user',fileName:'synthetic-draft',documentId:'doc',
        baseRevision:1,updatedAt:Date.now(),payload:structuredClone(local)});
    const elements=new Map(), listeners=new Map(), timers=new Set(), intervals=new Map(), beacons=[], submittedRequests=[];
    const classes=()=>{const values=new Set();return {add:name=>values.add(name),remove:name=>values.delete(name),
        toggle:(name,value)=>value ? values.add(name) : values.delete(name),contains:name=>values.has(name)};};
    const element=id=>{
        if(!elements.has(id))elements.set(id,{disabled:true,hidden:false,textContent:'',dataset:{},classList:classes(),events:new Map(),
            addEventListener(name,fn){this.events.set(name,fn);},querySelectorAll:()=>[]});
        return elements.get(id);
    };
    let payload={}, pendingSave, pendingVerification, saveCount=0, verifyCount=0, bootstrapCount=0, heartbeatRequired=false, revision=1, leaseOwner='';
    let bootstrapBusy=false, reloads=0;
    const navigator={onLine:true,sendBeacon:(url,body)=>{beacons.push({url,body});return true;}};
    const reply=value=>({ok:true,status:200,json:async()=>value});
    const fetch=async (url, options) => {
        if(url.includes('/bootstrap?')) {
            bootstrapCount++;
            if(bootstrapBusy)return {ok:false,status:409,json:async()=>({success:false,code:'operation_in_progress'})};
            if(failBootstrap)throw new Error('synthetic bootstrap network failure');
            if(leaseOwner)return {ok:false,status:423,json:async()=>({success:false,code:'lease_conflict',
                editing:{revision,owner:{owner_name:leaseOwner}}})};
            const document=structuredClone(sharedServer?.document || remote);
            return reply({success:true,is_draft:true,document,
                editing:{document_id:'doc',revision:sharedServer?.revision || revision,server_document:document,lease:{token:'synthetic-lease'}}});
        }
        if(url.endsWith('/maintenance/validate')) {
            verifyCount++;
            if(failVerification)throw new Error('synthetic network failure');
            const value=reply({success:true,maintenance_checklists:normalize(JSON.parse(options.body).document).maintenance_checklists});
            if(deferVerification)return new Promise(resolve=>{pendingVerification=()=>{pendingVerification=null;resolve(value);};});
            return value;
        }
        if(url.endsWith('/lease/heartbeat'))return heartbeatRequired
            ? {ok:false,status:409,json:async()=>({success:false,code:'lease_required',editing:{revision}})}
            : reply({success:true});
        assert.ok(url.endsWith('/autosave'),url);
        saveCount++;
        const request=JSON.parse(options.body), submitted=request.document;
        submittedRequests.push(request);
        if(sharedServer && request._edit.base_revision!==sharedServer.revision) {
            sharedServer.conflicts++;
            return {ok:false,status:409,json:async()=>({success:false,code:'revision_conflict',
                editing:{revision:sharedServer.revision,server_document:structuredClone(sharedServer.document)}})};
        }
        if(sharedServer) {
            sharedServer.document=normalize(submitted);sharedServer.revision++;sharedServer.accepted++;
        }
        const value=reply({success:true,editing:{revision:sharedServer?.revision || 2,server_document:normalize(submitted)}});
        if(deferSave)return new Promise(resolve=>{pendingSave=()=>resolve(value);});
        return value;
    };
    const signatureNotice={}, invalidatedSignatures=new Map();
    const view={sites:[],signatureNotice,invalidatedSignatures,model,verificationPending:false,
        def:{signature_exceptions:{},signature_verification_warning:'A chave de segurança pode ter mudado; recolha uma nova assinatura.',
            signature_pending_warning:'Assinatura por confirmar no servidor. Volte a tentar com ligação.'}};
    vm.runInNewContext(`${functionSource('restoreSignatureWarnings')}\n${functionSource('renderSignatureNotice')}\nthis.refresh=()=>{restoreSignatureWarnings();renderSignatureNotice();};`,view);
    const refresh=()=>{view.sites=payload.maintenance_checklists || [];view.refresh();};
    const verification={fetch,config:{selectedFileIsDraft:true,selectedFileName:'synthetic-draft'},encodeURIComponent};
    vm.runInNewContext(`${verifySource}\nthis.verify=verifyRecovery;`,verification);
    const indexedDB=database(records);
    // Execute the actual form collector for the legacy-empty recovery regression.
    // The HTTP matrix separately exercises server retention for this payload.
    const collectCurrent = () => {
        if (!realCollector) return structuredClone(payload);
        const source=read('document-editor.js');
        const begin=source.indexOf('    const collectFormData = () => {');
        const context={structuredClone,window:{MaintenanceEditor:{collect:()=>structuredClone(payload.maintenance_checklists || [])}},
            preservedMaintenance:[],createEmptyDocument:()=>({}),
            simpleFields:[{value:payload.observations || '',dataset:{field:'observations'}}],
            collectOptionGroup:()=>({}),serviceTypeInputs:[],equipmentInputs:[],materialsUsedYes:{checked:false},
            collectTechnicianRows:()=>[],clientNotPresentInput:{checked:true},signaturePads:[]};
        vm.runInNewContext(source.slice(begin,source.indexOf('    const getFinalizationValidation',begin))+'\nthis.result=collectFormData();',context);
        return context.result;
    };
    const window={__FILES_APP__:{selectedFileName:'synthetic-draft',selectedFileIsDraft:true,editorUser:{id:'user'}},
        __FILES_EDITOR__:{collectFormData:collectCurrent,
            populateForm:(_name,value)=>{payload=structuredClone(value);view.verificationPending=false;refresh();},
            setVerificationPending:value=>{view.verificationPending=value;refresh();},
            verifyRecovery:verification.verify,
            pendingRecovery:model.pendingRecovery,
            reconcileSignatures:(submitted,verified)=>{
                const result=model.reconcileSignatures(payload.maintenance_checklists,submitted.maintenance_checklists,verified.maintenance_checklists);
                refresh();return result.length;
            },showToast(){},renderAttachments(){},hasPendingPhotoChanges:()=>false},
        SensorpointCommitRecovery:commitRecovery,
        crypto:{randomUUID:()=>require('node:crypto').randomUUID()},indexedDB,
        setTimeout:(fn,delay)=>{const timer=setTimeout(fn,delay);timers.add(timer);return timer;},clearTimeout,
        setInterval:(fn,delay)=>{intervals.set(delay,fn);return {unref(){}};},clearInterval(){},addEventListener:(name,fn)=>listeners.set(name,fn),location:{reload:()=>{reloads++;}}};
    if(BroadcastChannel)window.BroadcastChannel=BroadcastChannel;
    const document={readyState:'complete',hidden:false,body:{classList:classes()},getElementById:element,
        querySelector:()=>null,addEventListener:(name,fn)=>listeners.set(name,fn)};
    vm.runInNewContext(read('editing-coordinator.js'),{window,document,fetch,console,Blob,indexedDB,BroadcastChannel,
        IDBKeyRange:{only:value=>value},navigator,
        sessionStorage:{getItem:()=>null,setItem(){}},setTimeout,clearTimeout});
    t.after(()=>{for(const timer of timers)clearTimeout(timer);});
    return {records,element,beacons,navigator,submittedRequests,notice:signatureNotice,coordinator:window.__EDITING_COORDINATOR__,
        bootstrapCount:()=>bootstrapCount, busyBootstrap:value=>{bootstrapBusy=value;},
        reloads:()=>reloads,
        booting:()=>document.body.classList.contains('editor-booting'),
        payload:()=>payload,setPayload:value=>{payload=structuredClone(value);refresh();},
        saveCount:()=>saveCount,verifyCount:()=>verifyCount,finishSave:()=>pendingSave?.(),pendingSave:()=>Boolean(pendingSave),listeners,
        finishVerification:()=>pendingVerification?.(),pendingVerification:()=>Boolean(pendingVerification),
        failVerification:()=>{failVerification=true;},
        denyLease:owner=>{leaseOwner=owner;},
        requireLease:(remoteRevision=revision)=>{heartbeatRequired=true;revision=remoteRevision;return intervals.get(15000)();},
        recoverNetwork:()=>{failVerification=false;failBootstrap=false;heartbeatRequired=false;leaseOwner='';}};
}

for(const kind of ['send','draft']) for(const obstruction of ['none','busy','offline','verification'])test(`interrupted ${kind} resumes the same form before editing (${obstruction})`,async t=>{
    let receipt=null, cleared=0;
    const commitRecovery={read:()=>receipt?.record,lookup:async()=>receipt,clear:()=>{cleared++;receipt=null;}};
    const original=signed('fresh-valid');
    const app=harness(t,{remote:original,commitRecovery});
    await waitFor(()=>!app.element('btn-save-send').disabled);
    const latest=signed('fresh-valid');latest.observations='Texto mais recente ainda nesta página';
    app.setPayload(latest);app.coordinator.markDirty();
    const oldKey=app.coordinator.operationMetadata(kind).idempotency_key;
    app.coordinator.setCommitPending(true);
    receipt={status:'interrupted',kind,record:{kind,documentId:'doc',operationId:oldKey}};
    if(obstruction==='busy')app.busyBootstrap(true);
    if(obstruction==='offline')app.navigator.onLine=false;
    if(obstruction==='verification')app.failVerification();
    assert.equal(await app.coordinator.recoverPendingCommit(),false);
    app.coordinator.setCommitPending(false); // sendFile's finally
    assert.equal(cleared,kind==='send'?1:0);
    if(obstruction!=='none') {
        assert.equal(app.element('btn-save-send').disabled,true);
        assert.equal(app.element('service-form').classList.contains('is-readonly'),true);
        assert.equal(app.element('btn-retry-lease').hidden,false);
        assert.equal(app.saveCount(),0,'unverified recovery must not autosave');
        assert.equal(app.records.get('user:doc').payload.observations,latest.observations);
        app.busyBootstrap(false);app.navigator.onLine=true;app.recoverNetwork();
        await app.element('btn-retry-lease').events.get('click')();
    }
    assert.equal(app.element('btn-save-send').disabled,false);
    assert.equal(app.element('service-form').classList.contains('is-readonly'),false);
    assert.ok(app.bootstrapCount()>=2,'interruption requires a fresh bootstrap without reloading');
    assert.equal(app.payload().observations,latest.observations);
    assert.equal(app.payload().maintenance_checklists[0].signatures.technician.token,'fresh-valid');
    await waitFor(()=>app.saveCount()===1);
    if(kind==='send')assert.notEqual(app.coordinator.operationMetadata(kind).idempotency_key,oldKey);
    else assert.equal(app.coordinator.operationMetadata(kind).idempotency_key,oldKey);
    assert.equal(app.records.get('user:doc').payload.observations,latest.observations);
});

test('an unavailable retired partial copy clears only its reference and keeps the current form',async t=>{
    let receipt=null,cleared=0;
    const tracker={read:()=>receipt?.record,lookup:async()=>receipt,clear:()=>{cleared++;receipt=null;}};
    const app=harness(t,{remote:signed('fresh-valid'),commitRecovery:tracker});
    await waitFor(()=>!app.element('btn-save-draft').disabled);
    const latest=signed('fresh-valid');latest.observations='Texto atual preservado';
    app.setPayload(latest);app.coordinator.markDirty();
    const key=app.coordinator.operationMetadata('draft').idempotency_key;
    receipt={kind:'draft',status:'failed',result:{code:'draft_copy_unavailable'},record:{kind:'draft',documentId:'doc',operationId:key}};
    assert.equal(await app.coordinator.recoverPendingCommit(),false);
    assert.equal(cleared,1);
    assert.equal(app.payload().observations,latest.observations);
    assert.notEqual(app.coordinator.operationMetadata('draft').idempotency_key,key);
});

test('changed session preserves the latest form and the recovery action reloads instead of retrying the old scope',async t=>{
    let changed=false, lookups=0;
    const tracker={read:()=>null,lookup:async()=>{lookups++;if(changed)throw Object.assign(new Error('Sessão mudou'),{code:'mail_monitor_session_changed'});return null;}};
    const app=harness(t,{remote:signed('fresh-valid'),commitRecovery:tracker});
    await waitFor(()=>!app.element('btn-save-send').disabled);
    const latest=signed('fresh-valid');latest.observations='Texto atual antes da mudança de sessão';
    app.setPayload(latest);app.coordinator.markDirty();app.coordinator.setCommitPending(true);changed=true;
    await assert.rejects(()=>app.coordinator.recoverPendingCommit(),{code:'mail_monitor_session_changed'});
    app.coordinator.setCommitPending(false);
    assert.equal(app.element('btn-save-send').disabled,true);
    assert.equal(app.element('btn-retry-lease').textContent,'Recarregar');
    assert.equal(app.records.get('user:doc').payload.observations,latest.observations);
    const before=lookups;
    await app.listeners.get('online')();
    await app.element('btn-retry-lease').events.get('click')();
    assert.equal(app.reloads(),1);assert.equal(lookups,before);assert.equal(app.bootstrapCount(),1);
    assert.equal(app.records.get('user:doc').payload.observations,latest.observations);
    assert.equal(app.saveCount(),0);
});

test('legacy empty IndexedDB recovery at the current revision never gains a clear marker from the new editor',async t=>{
    const local={observations:'Alteração feita na versão sem acesso SADI',maintenance_checklists:[]};
    const app=harness(t,{local,remote:signed('fresh-valid'),realCollector:true});
    await waitFor(()=>app.saveCount()===1 && app.element('autosave-status').dataset.state==='saved');
    const request=app.submittedRequests[0];
    assert.equal(request._edit.base_revision,1);
    assert.equal(request.document.observations,local.observations);
    assert.deepEqual(request.document.maintenance_checklists,[]);
    assert.equal(request.document._maintenance_clear,undefined);
});

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

test('failed resume is read-only and an explicit retry preserves the suspended form',async t=>{
    const app=harness(t,{remote:signed('old')});
    await waitFor(()=>app.element('btn-save-draft').disabled===false);
    const latest=structuredClone(app.payload());latest.observations='edição mais recente nesta página';
    app.setPayload(latest);
    app.coordinator.markDirty();
    app.listeners.get('freeze')();
    await waitFor(()=>app.records.has('user:doc'));
    const before=structuredClone(app.records.get('user:doc'));
    app.failVerification();app.listeners.get('resume')();
    await waitFor(()=>app.element('btn-retry-lease').hidden===false);
    assert.equal(app.element('service-form').classList.contains('is-readonly'),true);
    assert.equal(app.element('btn-save-draft').disabled,true);
    assert.match(app.notice.textContent,/por confirmar no servidor/);
    assert.deepEqual(app.payload(),latest);
    assert.deepEqual(app.records.get('user:doc'),before);
    assert.equal(app.saveCount(),0);
    app.recoverNetwork();
    await app.element('btn-retry-lease').events.get('click')();
    assert.equal(app.verifyCount(),2);
    assert.equal(app.element('btn-save-draft').disabled,false);
    assert.equal(app.element('service-form').classList.contains('is-readonly'),false);
    assert.equal(app.payload().observations,latest.observations);
    assert.equal(Object.keys(app.payload().maintenance_checklists[0].signatures).length,0);
    assert.equal(app.saveCount(),1);
});

test('heartbeat lease recovery keeps newer form edits and close never sends unverified content',async t=>{
    const initial=signed('fresh-valid');
    const app=harness(t,{local:initial,remote:initial});
    await waitFor(()=>app.element('btn-save-draft').disabled===false);
    const before=structuredClone(app.records.get('user:doc'));
    const latest=structuredClone(app.payload());latest.observations='mais recente que IndexedDB';
    app.setPayload(latest);app.coordinator.markDirty();app.failVerification();
    await app.requireLease(3);
    await waitFor(()=>app.element('btn-retry-lease').hidden===false);
    assert.equal(app.element('service-form').classList.contains('is-readonly'),true);
    assert.equal(app.element('btn-save-draft').disabled,true);
    assert.deepEqual(app.payload(),latest);
    assert.deepEqual(app.records.get('user:doc'),before);
    assert.equal(app.saveCount(),0);
    app.listeners.get('pagehide')();
    assert.equal(app.beacons.length,1);
    assert.equal(JSON.parse(await app.beacons[0].body.text()).document,null);
    await waitFor(()=>app.records.get('user:doc').payload.observations===latest.observations);
    assert.equal(app.records.get('user:doc').baseRevision,1);
    app.recoverNetwork();
    await app.element('btn-retry-lease').events.get('click')();
    assert.equal(app.verifyCount(),3);
    assert.equal(app.element('editing-recovery-panel').hidden,false);
    assert.equal(app.element('btn-save-draft').disabled,true);
    assert.equal(app.payload().observations,latest.observations);
    assert.equal(app.saveCount(),0);
});

test('closing failed initial verification only releases the reservation and retains the original recovery',async t=>{
    const local=signed('old');
    const app=harness(t,{local,remote:normalize(local),failVerification:true});
    await waitFor(()=>app.element('btn-retry-lease').hidden===false);
    const before=structuredClone(app.records.get('user:doc'));
    app.listeners.get('pagehide')();
    assert.equal(app.beacons.length,1);
    assert.equal(JSON.parse(await app.beacons[0].body.text()).document,null);
    assert.deepEqual(app.records.get('user:doc'),before);
});

for(const stage of ['initial','resume','bootstrap'])test(`${stage} verification failure allows a second tab to edit, but retry waits for that tab`,async t=>{
    const BroadcastChannel=broadcastBus(), remote=signed('fresh-valid');
    const first=harness(t,{local:remote,remote,BroadcastChannel,failVerification:stage==='initial',failBootstrap:stage==='bootstrap'});
    if(stage==='resume') {
        await waitFor(()=>first.element('btn-save-draft').disabled===false);
        first.listeners.get('freeze')();first.failVerification();first.listeners.get('resume')();
    }
    await waitFor(()=>first.element('btn-retry-lease').hidden===false);
    const second=harness(t,{remote,BroadcastChannel});
    await waitFor(()=>second.element('btn-save-draft').disabled===false);
    assert.equal(second.element('service-form').classList.contains('is-readonly'),false);
    assert.equal(first.element('service-form').classList.contains('is-readonly'),true);
    first.recoverNetwork();
    await first.element('btn-retry-lease').events.get('click')();
    assert.equal(first.element('btn-save-draft').disabled,true);
    assert.equal(first.element('service-form').classList.contains('is-readonly'),true);
    assert.equal(second.element('btn-save-draft').disabled,false);
    assert.equal(first.saveCount(),0);
    second.listeners.get('pagehide')();
    await waitFor(()=>first.element('btn-save-draft').disabled===false);
    assert.equal(first.element('service-form').classList.contains('is-readonly'),false);
    assert.equal(second.element('btn-save-draft').disabled,true);
});

for(const failure of ['verification','bootstrap'])test(`three tabs restore the ${failure} recovery retry after the editor closes`,async t=>{
    const BroadcastChannel=broadcastBus(), remote=normalize(signed('fresh-valid')), records=new Map();
    const first=harness(t,{local:remote,remote,records,BroadcastChannel,
        failVerification:failure==='verification',failBootstrap:failure==='bootstrap'});
    await waitFor(()=>first.element('btn-retry-lease').hidden===false);
    const second=harness(t,{remote,records,BroadcastChannel});
    await waitFor(()=>second.element('btn-save-draft').disabled===false && !second.coordinator.isDirty());
    const third=harness(t,{remote,records,BroadcastChannel});
    await waitFor(()=>!third.booting() && third.element('autosave-status').dataset.state==='conflict');
    await waitFor(()=>first.element('btn-retry-lease').hidden===true);
    const original=structuredClone(records.get('user:doc'));
    third.listeners.get('pagehide')();
    second.listeners.get('pagehide')();
    await waitFor(()=>first.element('btn-retry-lease').hidden===false);
    assert.equal(first.element('service-form').classList.contains('is-readonly'),true);
    assert.equal(first.element('btn-save-draft').disabled,true);
    assert.match(first.element('autosave-status').textContent,/Recuperação por confirmar/);
    assert.deepEqual(records.get('user:doc'),original);
    assert.equal(first.saveCount(),0);
    first.recoverNetwork();
    await first.element('btn-retry-lease').events.get('click')();
    assert.equal(first.element('btn-save-draft').disabled,false);
    assert.equal(first.element('service-form').classList.contains('is-readonly'),false);
});

for(const editorOpen of [false,true])test(`online bootstrap retry resamples tabs when the previous editor is ${editorOpen?'open':'closed without a closing message'}`,async t=>{
    const BroadcastChannel=broadcastBus({dropClosing:true}), remote=normalize(signed('fresh-valid')), records=new Map();
    const first=harness(t,{local:remote,remote,records,BroadcastChannel,failBootstrap:true});
    await waitFor(()=>first.element('btn-retry-lease').hidden===false);
    const second=harness(t,{remote,records,BroadcastChannel});
    await waitFor(()=>second.element('btn-save-draft').disabled===false && !second.coordinator.isDirty());
    const third=harness(t,{remote,records,BroadcastChannel});
    await waitFor(()=>!third.booting() && third.element('autosave-status').dataset.state==='conflict');
    await waitFor(()=>first.element('btn-retry-lease').hidden===true);
    const original=structuredClone(records.get('user:doc'));
    third.listeners.get('pagehide')();
    if(!editorOpen)second.listeners.get('pagehide')();
    first.recoverNetwork();await first.listeners.get('online')();
    assert.equal(first.element('btn-save-draft').disabled,editorOpen);
    assert.equal(first.element('service-form').classList.contains('is-readonly'),editorOpen);
    assert.equal(first.element('autosave-status').dataset.state,editorOpen?'conflict':'saved');
    assert.equal(first.saveCount(),0);
    if(editorOpen)assert.deepEqual(records.get('user:doc'),original);
    else {
        assert.deepEqual(records.get('user:doc').payload,original.payload);
        assert.equal(records.get('user:doc').baseRevision,original.baseRevision);
    }
});

test('an observing tab with stale local edits receives a revision conflict after the editor saves and closes',async t=>{
    const BroadcastChannel=broadcastBus(), remote=signed('fresh-valid'), records=new Map();
    const sharedServer={document:remote,revision:1,accepted:0,conflicts:0};
    const editor=harness(t,{remote,records,BroadcastChannel,sharedServer});
    await waitFor(()=>editor.element('btn-save-draft').disabled===false);
    const local=structuredClone(remote);local.observations='alterações locais anteriores da observadora';
    const observer=harness(t,{local,remote,records,BroadcastChannel,sharedServer});
    await waitFor(()=>!observer.booting());
    assert.equal(observer.coordinator.isDirty(),true);
    assert.equal(observer.element('btn-save-draft').disabled,true);
    assert.equal(observer.saveCount(),0);
    const latest=structuredClone(editor.payload());latest.observations='versão nova confirmada pela editora';
    editor.setPayload(latest);editor.coordinator.markDirty();
    await waitFor(()=>editor.element('autosave-status').dataset.state==='saved' && sharedServer.accepted===1);
    const confirmed=structuredClone(sharedServer.document);
    editor.listeners.get('pagehide')();
    await waitFor(()=>observer.element('recovery-panel').classList.contains('is-conflict'));
    assert.equal(observer.element('btn-save-draft').disabled,true);
    assert.equal(observer.element('recovery-panel').hidden,false);
    assert.equal(observer.payload().observations,local.observations);
    assert.deepEqual(sharedServer.document,confirmed);
    assert.equal(sharedServer.accepted,1);
    assert.equal(sharedServer.conflicts,1);
});

test('a read-only tab finishing deferred verification cannot overwrite the active tab shared recovery',async t=>{
    const BroadcastChannel=broadcastBus(), remote=signed('fresh-valid'), records=new Map();
    const local=structuredClone(remote);local.observations='cópia local anterior às últimas edições';
    const editor=harness(t,{local,remote,records,BroadcastChannel,deferSave:true});
    await waitFor(()=>editor.element('btn-save-draft').disabled===false);
    const observer=harness(t,{remote,records,BroadcastChannel,deferVerification:true});
    await waitFor(observer.pendingVerification);
    const latest=structuredClone(editor.payload());latest.observations='alterações novas da aba editora';
    editor.setPayload(latest);editor.coordinator.markDirty();
    await waitFor(()=>records.get('user:doc').payload.observations===latest.observations);
    const before=structuredClone(records.get('user:doc'));
    observer.finishVerification();
    // Wait for initialize, not just the immediate BroadcastChannel handler.
    // The editor's server request remains deferred.
    await waitFor(()=>!observer.booting());
    assert.equal(observer.element('btn-save-draft').disabled,true);
    assert.deepEqual(records.get('user:doc'),before);
    assert.equal(observer.coordinator.isDirty(),true);
    observer.listeners.get('pagehide')();
    assert.equal(JSON.parse(await observer.beacons[0].body.text()).document,null);
    await new Promise(resolve=>setImmediate(resolve));
    assert.deepEqual(records.get('user:doc'),before);
    assert.equal(editor.element('btn-save-draft').disabled,false);
});

test('resume denied by another lease stays read-only, shows the owner and allows explicit retry',async t=>{
    const app=harness(t,{remote:signed('fresh-valid')});
    await waitFor(()=>app.element('btn-save-draft').disabled===false);
    app.listeners.get('freeze')();app.denyLease('Técnico fictício B');app.listeners.get('resume')();
    await waitFor(()=>app.element('btn-retry-lease').hidden===false);
    assert.equal(app.element('service-form').classList.contains('is-readonly'),true);
    assert.equal(app.element('btn-save-draft').disabled,true);
    assert.match(app.element('editing-lock-message').textContent,/Técnico fictício B/);
    assert.equal(app.saveCount(),0);
    const before=app.beacons.length;
    app.listeners.get('pagehide')();
    assert.equal(app.beacons.length,before);
    app.recoverNetwork();await app.element('btn-retry-lease').events.get('click')();
    assert.equal(app.element('btn-save-draft').disabled,false);
});

test('lease-required heartbeat during initial verification failure retries once and keeps original recovery',async t=>{
    const local=signed('old');
    const app=harness(t,{local,remote:normalize(local),failVerification:true});
    await waitFor(()=>!app.booting());
    const before=structuredClone(app.records.get('user:doc'));
    assert.equal(app.verifyCount(),1);
    await app.requireLease();
    await waitFor(()=>app.verifyCount()===2 && !app.booting());
    assert.equal(app.element('service-form').classList.contains('is-readonly'),true);
    assert.equal(app.element('btn-retry-lease').hidden,false);
    assert.deepEqual(app.records.get('user:doc'),before);
    assert.equal(app.saveCount(),0);
});

test('late rejection never erases a new unsigned capture or an unrelated site',()=>{
    const submitted=signed('old'), current=structuredClone(submitted), verified=normalize(submitted);
    current.maintenance_checklists[0].signature_drafts.technician={image:'new-drawing'};
    current.maintenance_checklists.push({id:'new-site',signatures:{technician:{token:'local-proof'}}});
    model.reconcileSignatures(current.maintenance_checklists,submitted.maintenance_checklists,verified.maintenance_checklists);
    assert.equal(current.maintenance_checklists[0].signature_drafts.technician.image,'new-drawing');
    assert.equal(current.maintenance_checklists[1].signatures.technician.token,'local-proof');
});
