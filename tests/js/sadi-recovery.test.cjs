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

function harness(t, {local, remote, failVerification=false, failBootstrap=false, deferSave=false, BroadcastChannel, realCollector=false, pendingPhotos=false,
    records=new Map(), deferVerification=false, sharedServer=null, commitRecovery, validationChecklists, conflictCode='revision_conflict'}) {
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
    let bootstrapBusy=false, reloads=0, populationCount=0;
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
                source_document:structuredClone(sharedServer?.sourceDocument || document),
                editing:{document_id:'doc',revision:sharedServer?.revision || revision,server_document:document,
                    source_conflict:sharedServer?.sourceConflict || null,lease:{token:'synthetic-lease'}}});
        }
        if(url.endsWith('/maintenance/validate')) {
            verifyCount++;
            if(failVerification)throw new Error('synthetic network failure');
            const submitted=JSON.parse(options.body).document;
            const value=reply({success:true,maintenance_checklists:validationChecklists && !submitted.maintenance_checklists?.length
                ? structuredClone(validationChecklists) : normalize(submitted).maintenance_checklists});
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
        if(sharedServer && (request._edit.base_revision!==sharedServer.revision ||
            (sharedServer.sourceConflict && request.source_resolution!==sharedServer.sourceConflict.token))) {
            sharedServer.conflicts++;
            return {ok:false,status:409,json:async()=>({success:false,code:conflictCode,
                editing:{revision:sharedServer.revision,server_document:structuredClone(sharedServer.document),
                    source_conflict:sharedServer.sourceConflict || null}})};
        }
        if(sharedServer) {
            sharedServer.document=normalize(submitted);sharedServer.revision++;sharedServer.accepted++;
            sharedServer.sourceConflict=null;
        }
        const value=reply({success:true,editing:{revision:sharedServer?.revision || 2,server_document:normalize(submitted),source_conflict:null}});
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
    const openDatabase=indexedDB.open; let failDatabaseRead=false;
    indexedDB.open=()=>{
        if(!failDatabaseRead)return openDatabase();
        const request={};setImmediate(()=>{request.error=new Error('synthetic database unavailable');request.onerror?.();});return request;
    };
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
            populateForm:(_name,value)=>{populationCount++;payload=structuredClone(value);view.verificationPending=false;refresh();},
            applyMaintenanceRecovery:value=>{
                // Run the production adapter: recovery must not repopulate the
                // service form, which would reset unsaved photographs/signatures.
                const source=read('document-editor.js');
                const begin=source.indexOf('    const applyMaintenanceRecovery = ');
                assert.notEqual(begin,-1);
                const context={structuredClone,preservedMaintenance:[],window:{MaintenanceEditor:{hydrate:document=>{
                    payload.maintenance_checklists=structuredClone(document.maintenance_checklists);
                    view.verificationPending=false;refresh();
                }}}};
                vm.runInNewContext(source.slice(begin,source.indexOf('\n    const ',begin+10))+'\nthis.apply=applyMaintenanceRecovery;',context);
                context.apply(value);
            },
            setVerificationPending:value=>{view.verificationPending=value;refresh();},
            verifyRecovery:verification.verify,
            pendingRecovery:model.pendingRecovery,
            reconcileSignatures:(submitted,verified)=>{
                const result=model.reconcileSignatures(payload.maintenance_checklists,submitted.maintenance_checklists,verified.maintenance_checklists);
                refresh();return result.length;
            },showToast(){},renderAttachments(){},hasPendingPhotoChanges:()=>pendingPhotos},
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
        reloads:()=>reloads,populationCount:()=>populationCount,
        booting:()=>document.body.classList.contains('editor-booting'),
        payload:()=>payload,setPayload:value=>{payload=structuredClone(value);refresh();},
        saveCount:()=>saveCount,verifyCount:()=>verifyCount,finishSave:()=>pendingSave?.(),pendingSave:()=>Boolean(pendingSave),listeners,
        finishVerification:()=>pendingVerification?.(),pendingVerification:()=>Boolean(pendingVerification),
        failVerification:()=>{failVerification=true;},
        failDatabase:value=>{failDatabaseRead=value;},
        failBootstrap:()=>{failBootstrap=true;},
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

test('legacy empty IndexedDB recovery restores server checklists before hydration and autosave at the current revision',async t=>{
    const local={observations:'Alteração feita na versão sem acesso SADI',maintenance_checklists:[]};
    const remote=signed('fresh-valid');
    const app=harness(t,{local,remote,realCollector:true,validationChecklists:remote.maintenance_checklists});
    await waitFor(()=>app.saveCount()===1 && app.element('autosave-status').dataset.state==='saved');
    const request=app.submittedRequests[0];
    assert.equal(request._edit.base_revision,1);
    assert.equal(request.document.observations,local.observations);
    assert.deepEqual(request.document.maintenance_checklists,remote.maintenance_checklists);
    assert.deepEqual(app.payload().maintenance_checklists,remote.maintenance_checklists);
    assert.equal(app.verifyCount(),1);
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

for(const revision of [1,2])for(const localEdits of [false,true])test(`empty suspended checklist recovery is visible before editing (revision ${revision}, local edits ${localEdits})`,async t=>{
    const empty=signed('fresh-valid');empty.maintenance_checklists=[];
    const sharedServer={document:structuredClone(empty),revision:1,accepted:0,conflicts:0};
    const validationChecklists=[];
    const app=harness(t,{remote:empty,sharedServer,validationChecklists,pendingPhotos:true});
    await waitFor(()=>!app.element('btn-save-draft').disabled);
    if(localEdits) {
        const local=structuredClone(app.payload());local.observations='Texto local antes da suspensão';
        app.setPayload(local);app.coordinator.markDirty();
    }
    app.listeners.get('freeze')();
    sharedServer.document=signed('fresh-valid');sharedServer.revision=revision;
    validationChecklists.push(...sharedServer.document.maintenance_checklists);
    app.listeners.get('resume')();
    const conflict=revision===2 && localEdits;
    await waitFor(()=>app.verifyCount()===1 && (conflict
        ? app.element('autosave-status').dataset.state==='conflict'
        : !app.element('btn-save-draft').disabled));
    assert.deepEqual(app.payload().maintenance_checklists,validationChecklists);
    assert.equal(app.populationCount(),1,'resume must not reset unsaved service photographs or signature pads');
    assert.equal(app.payload()['Assinatura Cliente'],'synthetic-service-ink');
    assert.equal(app.payload().observations,localEdits?'Texto local antes da suspensão':empty.observations);
    assert.deepEqual(app.records.get('user:doc').payload.maintenance_checklists,validationChecklists);
    if(conflict) {
        assert.equal(sharedServer.accepted,0);
        assert.equal(app.element('btn-save-draft').disabled,true);
        await app.element('btn-restore-recovery').events.get('click')();
        await waitFor(()=>sharedServer.accepted===1);
        assert.deepEqual(sharedServer.document.maintenance_checklists,normalize({maintenance_checklists:validationChecklists}).maintenance_checklists);
        assert.equal(sharedServer.document.observations,'Texto local antes da suspensão');
    }
    // The next human edit appends a site to the visible list; it must retain
    // every restored site instead of silently replacing the server list.
    await waitFor(()=>!app.element('btn-save-draft').disabled);
    const accepted=sharedServer.accepted;
    const next=structuredClone(app.payload());
    next.maintenance_checklists.push({id:'new-site',location:'Novo local fictício',signatures:{}});
    app.setPayload(next);app.coordinator.markDirty();
    await waitFor(()=>sharedServer.accepted>accepted && sharedServer.document.maintenance_checklists.length===2
        && app.element('autosave-status').textContent.includes('Fotografias por guardar'));
    assert.deepEqual(sharedServer.document.maintenance_checklists.map(site=>site.id),['site-a','new-site']);
    assert.equal(sharedServer.conflicts,0);
    assert.equal(app.submittedRequests.at(-1).document._maintenance_clear,undefined);
    assert.match(app.element('autosave-status').textContent,/Fotografias por guardar/);
});

test('empty resume waits for the other tab, then verifies the original snapshot against the latest server sites',async t=>{
    const BroadcastChannel=broadcastBus(),records=new Map();
    const empty=signed('fresh-valid');empty.maintenance_checklists=[];
    const sharedServer={document:structuredClone(empty),revision:1,accepted:0,conflicts:0};
    const validationChecklists=[];
    const first=harness(t,{remote:empty,sharedServer,records,BroadcastChannel,validationChecklists});
    await waitFor(()=>!first.element('btn-save-draft').disabled);
    first.listeners.get('freeze')();
    sharedServer.document=signed('fresh-valid');sharedServer.revision=2;
    validationChecklists.push(...sharedServer.document.maintenance_checklists);
    const second=harness(t,{remote:sharedServer.document,sharedServer,records,BroadcastChannel});
    await waitFor(()=>!second.element('btn-save-draft').disabled);
    first.listeners.get('resume')();
    await waitFor(()=>first.verifyCount()===1 && first.element('autosave-status').dataset.state==='conflict');
    assert.equal(first.payload().maintenance_checklists.length,0);
    assert.equal(first.element('btn-save-draft').disabled,true);
    const latest=structuredClone(second.payload());
    latest.maintenance_checklists.push({id:'second-site',location:'Outro local fictício',signatures:{}});
    second.setPayload(latest);second.coordinator.markDirty();
    await waitFor(()=>sharedServer.accepted===1);
    validationChecklists.splice(0,validationChecklists.length,...sharedServer.document.maintenance_checklists);
    second.listeners.get('pagehide')();
    await waitFor(()=>first.verifyCount()===2 && !first.element('btn-save-draft').disabled);
    assert.deepEqual(first.payload().maintenance_checklists.map(site=>site.id),['site-a','second-site']);
    assert.equal(first.saveCount(),0);
    assert.equal(first.populationCount(),1);
    assert.deepEqual(records.get('user:doc').payload.maintenance_checklists,sharedServer.document.maintenance_checklists);
});

test('an empty optional checklist resumes normally when the server is also empty',async t=>{
    const empty=signed('fresh-valid');empty.maintenance_checklists=[];
    const sharedServer={document:structuredClone(empty),revision:1,accepted:0,conflicts:0};
    const app=harness(t,{remote:empty,sharedServer,validationChecklists:[]});
    await waitFor(()=>!app.element('btn-save-draft').disabled);
    app.listeners.get('freeze')();app.listeners.get('resume')();
    await waitFor(()=>app.verifyCount()===1 && !app.element('btn-save-draft').disabled);
    assert.equal(app.element('btn-save-send').disabled,false);
    assert.equal(app.payload().maintenance_checklists.length,0);
    assert.equal(app.populationCount(),1);
    app.coordinator.markDirty();
    await waitFor(()=>sharedServer.accepted===1 && app.element('autosave-status').dataset.state==='saved');
    assert.deepEqual(sharedServer.document.maintenance_checklists,[]);
    assert.equal(app.submittedRequests[0].document._maintenance_clear,undefined);
});

test('a tab claimed during recovered-site persistence keeps the resumed form read-only',async t=>{
    let channel,claim=false;
    class Channel { constructor(){channel=this;} postMessage(){} close(){} }
    class Records extends Map {
        set(key,value) {
            const result=super.set(key,value);
            if(claim && value.payload.maintenance_checklists.length) {
                claim=false;
                queueMicrotask(()=>channel.onmessage({data:{type:'active',fileName:'synthetic-draft',userId:'user',instanceId:'other-tab'}}));
            }
            return result;
        }
    }
    const empty=signed('fresh-valid');empty.maintenance_checklists=[];
    const validationChecklists=[],records=new Records();
    const app=harness(t,{remote:empty,records,BroadcastChannel:Channel,validationChecklists});
    await waitFor(()=>!app.element('btn-save-draft').disabled);
    app.listeners.get('freeze')();claim=true;
    validationChecklists.push(...signed('fresh-valid').maintenance_checklists);
    app.listeners.get('resume')();
    await waitFor(()=>app.element('autosave-status').dataset.state==='conflict' && !claim);
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(app.element('service-form').classList.contains('is-readonly'),true);
    assert.equal(app.element('btn-save-draft').disabled,true);
    assert.equal(app.saveCount(),0);
    app.listeners.get('pagehide')();
    assert.equal(JSON.parse(await app.beacons.at(-1).body.text()).document,null);
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

for(const origin of ['revision_conflict','graph_conflict','resume'])for(const editAgain of [false,true])
    test(`unresolved ${origin} survives reload, new edits=${editAgain}, until explicit choice`,async t=>{
        const initial={customer_name:'Fictício',observations:'Inicial',maintenance_checklists:[]};
        const remote={...initial,observations:'Outra sessão'};
        const records=new Map(),sharedServer={document:initial,revision:1,accepted:0,conflicts:0};
        const first=harness(t,{remote:initial,records,sharedServer,conflictCode:origin});
        await waitFor(()=>!first.element('btn-save-draft').disabled);
        first.setPayload({...initial,observations:'Local sem escolha'});
        sharedServer.document=remote;sharedServer.revision=2;
        first.coordinator.markDirty();
        if(origin==='resume'){
            first.listeners.get('freeze')();
            first.listeners.get('resume')();
        }
        await waitFor(()=>first.element('recovery-panel').classList.contains('is-conflict'));
        await waitFor(()=>records.get('user:doc')?.conflict===true);
        if(editAgain){
            first.setPayload({...initial,observations:'Edição adicional sem escolha'});
            first.coordinator.markDirty();
            await waitFor(()=>records.get('user:doc')?.payload.observations==='Edição adicional sem escolha');
        }
        const local=structuredClone(records.get('user:doc').payload);
        first.listeners.get('pagehide')();
        await new Promise(resolve=>setTimeout(resolve,30));
        assert.equal(JSON.parse(await first.beacons.at(-1).body.text()).document,null);
        const reopened=harness(t,{remote,records,sharedServer});
        await waitFor(()=>!reopened.booting());
        await new Promise(resolve=>setTimeout(resolve,800));
        assert.equal(reopened.saveCount(),0);
        assert.equal(sharedServer.accepted,0);
        assert.equal(sharedServer.document.observations,remote.observations);
        assert.equal(reopened.element('recovery-panel').hidden,false);
        assert.equal(reopened.element('btn-save-draft').disabled,true);
        assert.equal(records.get('user:doc').conflict,true);
        await reopened.element(editAgain?'btn-restore-recovery':'btn-ignore-recovery').events.get('click')();
        assert.equal(reopened.element('recovery-panel').hidden,true);
        assert.equal(sharedServer.accepted,editAgain?1:0);
        assert.equal(sharedServer.document.observations,editAgain?local.observations:remote.observations);
        assert.notEqual(records.get('user:doc')?.conflict,true);
        reopened.listeners.get('pagehide')();
        const again=harness(t,{remote:sharedServer.document,records,sharedServer});
        await waitFor(()=>!again.booting());
        assert.equal(again.element('recovery-panel').classList.contains('is-conflict'),false);
        assert.equal(again.saveCount(),0);
    });

test('ordinary local recovery at the same revision still autosaves without a conflict choice',async t=>{
    const remote={observations:'Original',maintenance_checklists:[]};
    const local={...remote,observations:'Alteração local sem conflito'};
    const sharedServer={document:remote,revision:1,accepted:0,conflicts:0};
    const app=harness(t,{local,remote,sharedServer});
    await waitFor(()=>app.saveCount()===1);
    assert.equal(sharedServer.document.observations,local.observations);
    assert.equal(app.element('recovery-panel').classList.contains('is-conflict'),false);
});

for(const marked of [false,true])for(const choice of ['server','device'])
    test(`initial conflict protects original local recovery from preview edits (marker=${marked}, ${choice})`,async t=>{
        const remote={observations:'Versão do servidor',maintenance_checklists:[]};
        const local={observations:'Trabalho local original a preservar',maintenance_checklists:[]};
        const record={key:'user:doc',userId:'user',fileName:'synthetic-draft',documentId:'doc',
            baseRevision:marked?2:1,conflict:marked,updatedAt:Date.now(),payload:local};
        const records=new Map([['user:doc',structuredClone(record)]]);
        const sharedServer={document:remote,revision:2,accepted:0,conflicts:0};
        const first=harness(t,{remote,records,sharedServer});
        await waitFor(()=>!first.booting());
        assert.equal(first.element('service-form').classList.contains('is-readonly'),true);
        assert.match(first.element('autosave-status').textContent,/Conflito/);
        assert.match(first.element('editing-lock-message').textContent,/duas versões estão preservadas/);
        await assert.rejects(first.coordinator.prepareOperation('draft'),/não está pronta/);
        assert.equal(first.saveCount(),0);
        // Even a delayed/programmatic input event cannot persist the server
        // preview over the original copy. Human inputs are disabled in this state.
        first.setPayload({...remote,observations:'Tentativa de alterar a pré-visualização'});
        first.coordinator.markDirty();
        await new Promise(resolve=>setTimeout(resolve,200));
        first.listeners.get('pagehide')();
        await new Promise(resolve=>setTimeout(resolve,30));
        assert.deepEqual(records.get('user:doc'),record);
        assert.equal(sharedServer.accepted,0);
        const next=harness(t,{remote,records,sharedServer});
        await waitFor(()=>!next.booting());
        assert.equal(next.element('recovery-panel').hidden,false);
        assert.equal(next.element('service-form').classList.contains('is-readonly'),true);
        await next.element(choice==='device'?'btn-restore-recovery':'btn-ignore-recovery').events.get('click')();
        assert.equal(next.element('service-form').classList.contains('is-readonly'),false);
        assert.equal(next.element('btn-save-draft').disabled,false);
        assert.equal(sharedServer.accepted,choice==='device'?1:0);
        assert.equal(sharedServer.document.observations,choice==='device'?local.observations:remote.observations);
    });

test('an observing tab cannot resolve or erase another tab conflict recovery',async t=>{
    const remote={observations:'Servidor',maintenance_checklists:[]};
    const local={observations:'Dispositivo protegido',maintenance_checklists:[]};
    const records=new Map([['user:doc',{key:'user:doc',userId:'user',fileName:'synthetic-draft',documentId:'doc',
        baseRevision:2,conflict:true,updatedAt:Date.now(),payload:local}]]);
    const sharedServer={document:remote,revision:2,accepted:0,conflicts:0},BroadcastChannel=broadcastBus();
    const editor=harness(t,{remote,records,sharedServer,BroadcastChannel});
    await waitFor(()=>!editor.booting());
    const observer=harness(t,{remote,records,sharedServer,BroadcastChannel});
    await waitFor(()=>!observer.booting());
    assert.match(observer.element('autosave-status').textContent,/outra aba/);
    const before=structuredClone(records.get('user:doc'));
    await observer.element('btn-ignore-recovery').events.get('click')();
    await observer.element('btn-restore-recovery').events.get('click')();
    assert.deepEqual(records.get('user:doc'),before);
    assert.equal(sharedServer.accepted,0);
    editor.listeners.get('pagehide')();
    await new Promise(resolve=>setTimeout(resolve,200));
    assert.equal(observer.element('btn-save-draft').disabled,true);
    assert.equal(observer.element('service-form').classList.contains('is-readonly'),true);
    assert.deepEqual(records.get('user:doc'),before);
});

for(const choice of ['server','device'])for(const failure of ['none','bootstrap','database'])
    test(`observer reloads current conflict decision before editing (${choice}, ${failure})`,async t=>{
        const remote={observations:'Servidor escolhido',maintenance_checklists:[]};
        const local={observations:'Cópia do dispositivo',maintenance_checklists:[]};
        const records=new Map([['user:doc',{key:'user:doc',userId:'user',fileName:'synthetic-draft',documentId:'doc',
            baseRevision:2,conflict:true,updatedAt:Date.now(),payload:local}]]);
        const sharedServer={document:remote,revision:2,accepted:0,conflicts:0},BroadcastChannel=broadcastBus();
        const a=harness(t,{remote,records,sharedServer,BroadcastChannel});await waitFor(()=>!a.booting());
        const b=harness(t,{remote,records,sharedServer,BroadcastChannel});await waitFor(()=>!b.booting());
        await a.element(choice==='server'?'btn-ignore-recovery':'btn-restore-recovery').events.get('click')();
        if(failure==='bootstrap')b.failBootstrap();
        if(failure==='database')b.failDatabase(true);
        a.listeners.get('pagehide')();
        await waitFor(()=>b.bootstrapCount()===2 && !b.booting());
        if(failure!=='none') {
            assert.equal(b.element('service-form').classList.contains('is-readonly'),true);
            assert.equal(b.element('btn-save-draft').disabled,true);
            assert.equal(b.element('btn-retry-lease').hidden,false);
            await b.element('btn-restore-recovery').events.get('click')();
            await b.element('btn-ignore-recovery').events.get('click')();
            assert.equal(sharedServer.accepted,choice==='device'?1:0);
            b.recoverNetwork();b.failDatabase(false);
            await b.element('btn-retry-lease').events.get('click')();
        }
        assert.equal(b.element('recovery-panel').hidden,true);
        assert.equal(b.element('btn-save-draft').disabled,false);
        assert.equal(b.payload().observations,choice==='device'?local.observations:remote.observations);
        await b.element('btn-restore-recovery').events.get('click')();
        assert.equal(sharedServer.accepted,choice==='device'?1:0,'stale choice cannot write');
        assert.equal(sharedServer.conflicts,0);
    });

test('closing during observer verification discards the stale read and starts a fresh bootstrap',async t=>{
    const remote={observations:'Servidor',maintenance_checklists:[]};
    const records=new Map([['user:doc',{key:'user:doc',userId:'user',fileName:'synthetic-draft',documentId:'doc',
        baseRevision:2,conflict:true,updatedAt:Date.now(),payload:{...remote,observations:'Já descartada'}}]]);
    const sharedServer={document:remote,revision:2,accepted:0,conflicts:0},BroadcastChannel=broadcastBus();
    const a=harness(t,{remote,records,sharedServer,BroadcastChannel});await waitFor(()=>!a.booting());
    const b=harness(t,{remote,records,sharedServer,BroadcastChannel,deferVerification:true});
    await waitFor(()=>b.pendingVerification());
    await a.element('btn-ignore-recovery').events.get('click')();a.listeners.get('pagehide')();
    await new Promise(resolve=>setImmediate(resolve));b.finishVerification();
    await waitFor(()=>b.bootstrapCount()===2 && !b.booting());
    assert.equal(b.element('recovery-panel').hidden,true);
    assert.equal(b.element('btn-save-draft').disabled,false);
    assert.equal(b.payload().observations,remote.observations);
    await b.element('btn-restore-recovery').events.get('click')();
    assert.equal(sharedServer.accepted,0);
});

test('observer keeps a real unresolved conflict using the latest shared local copy',async t=>{
    const remote={observations:'Inicial',maintenance_checklists:[]};
    const records=new Map(),sharedServer={document:remote,revision:1,accepted:0,conflicts:0},BroadcastChannel=broadcastBus();
    const a=harness(t,{remote,records,sharedServer,BroadcastChannel});await waitFor(()=>!a.booting());
    a.setPayload({...remote,observations:'Local antigo'});
    sharedServer.document={...remote,observations:'Servidor diferente'};sharedServer.revision=2;
    a.coordinator.markDirty();await waitFor(()=>records.get('user:doc')?.conflict);
    const b=harness(t,{remote,records,sharedServer,BroadcastChannel});await waitFor(()=>!b.booting());
    a.setPayload({...remote,observations:'Local mais recente'});a.coordinator.markDirty();
    await waitFor(()=>records.get('user:doc')?.payload.observations==='Local mais recente');
    a.listeners.get('pagehide')();await waitFor(()=>b.bootstrapCount()===2 && !b.booting());
    assert.equal(b.element('service-form').classList.contains('is-readonly'),true);
    assert.match(b.element('autosave-status').textContent,/Conflito/);
    assert.equal(sharedServer.accepted,0);
    await b.element('btn-restore-recovery').events.get('click')();
    assert.equal(sharedServer.document.observations,'Local mais recente');
    assert.equal(sharedServer.accepted,1);
});

const signatureConsumer = (app, {outcome='success', deferred=false}={}) => {
    const begin=editorSource.indexOf('    async function saveSignature(');
    const actual=editorSource.slice(begin,editorSource.indexOf('\n    window.MaintenanceEditor',begin));
    const site={id:'synthetic-site'},pending=[],requests=[],messages=new Map();
    const env={readOnly:()=>app.element('service-form').classList.contains('is-readonly'),
        model:{signatureWaived:()=>false},def:{},sites:[site],
        signatureInput:()=>({name:'Técnico fictício',date:'2026-10-23',image:'synthetic-ink'}),
        signatureJobs:new Map(),signatureMessages:messages,pendingPhotos:false,
        signatureKey:()=> 'same',renderStatus(){},current:()=>site,invalidatedSignatures:new Map(),
        documentData:()=>app.payload(),dirty:()=>app.coordinator.markDirty(),encodeURIComponent,
        window:{__EDITING_COORDINATOR__:app.coordinator,__FILES_EDITOR__:{getActiveFileName:()=> 'synthetic-draft'}},
        fetch:async(url,options)=>{
            requests.push(JSON.parse(options.body));
            if(deferred)await new Promise(resolve=>pending.push(resolve));
            if(outcome==='network')throw new Error('Falha de ligação fictícia');
            return {ok:outcome==='success',json:async()=>outcome==='success'
                ? {signature:{token:'synthetic'}} : {error:'Falha de assinatura fictícia'}};
        }};
    vm.runInNewContext(actual+'\nthis.save=saveSignature;',env);
    return {save:role=>env.save(site,role),requests,messages,finish:()=>pending.splice(0).forEach(fn=>fn()),site};
};

for(const outcome of ['success','refusal','network'])test(`actual signature consumer permits subsequent autosave and draft (${outcome})`,async t=>{
    const remote={observations:'Inicial',maintenance_checklists:[]};
    const app=harness(t,{remote});await waitFor(()=>!app.element('btn-save-draft').disabled);
    const signing=signatureConsumer(app,{outcome});await signing.save('technician');
    assert.equal(signing.requests.length,1);
    app.setPayload({...remote,observations:'Depois de assinar'});app.coordinator.markDirty();
    await waitFor(()=>app.saveCount()===1);
    assert.equal(app.element('btn-save-draft').disabled,false);
    const metadata=await app.coordinator.prepareOperation('draft');
    assert.equal(metadata.base_revision,2);app.coordinator.setCommitPending(false);
});

test('parallel signature proofs do not hold or release the document preparation',async t=>{
    const app=harness(t,{remote:{observations:'Inicial',maintenance_checklists:[]}});
    await waitFor(()=>!app.element('btn-save-draft').disabled);
    const signing=signatureConsumer(app,{deferred:true});
    const a=signing.save('technician'),b=signing.save('customer');
    await waitFor(()=>signing.requests.length===2);
    await app.coordinator.prepareOperation('draft');
    signing.finish();await Promise.all([a,b]);
    assert.equal(app.element('btn-save-draft').disabled,true);
    await assert.rejects(app.coordinator.prepareOperation('send'),/preparação/);
    app.coordinator.setCommitPending(false);
    await waitFor(()=>app.saveCount()===1);
    assert.equal(app.element('btn-save-draft').disabled,false);
});

test('a signature proof can finish during autosave without using its old revision for a commit',async t=>{
    const app=harness(t,{remote:{observations:'Inicial',maintenance_checklists:[]},deferSave:true});
    await waitFor(()=>!app.element('btn-save-draft').disabled);
    app.coordinator.markDirty();await waitFor(()=>app.pendingSave());
    const signing=signatureConsumer(app);await signing.save('technician');
    assert.equal(signing.requests.length,1);
    app.finishSave();await waitFor(()=>app.element('autosave-status').dataset.state==='saved');
    const metadata=await app.coordinator.prepareOperation('draft');
    assert.equal(metadata.base_revision,2);app.coordinator.setCommitPending(false);
});


// The server normalizes an empty material list to a placeholder row and may
// omit an empty private checklist list. Neither change is a user's edit.
for(const phase of ['reopen','resume'])for(const representation of ['materials','checklists','both'])
    test(`equivalent empty sections do not create a conflict on ${phase} (${representation})`,async t=>{
        const local={observations:'Texto preservado',materials:[],maintenance_checklists:[]};
        const remote=structuredClone(local);
        if(representation!=='checklists')remote.materials=[{qty:'',ref:'',description:''}];
        if(representation!=='materials')delete remote.maintenance_checklists;
        const records=new Map(),sharedServer={document:phase==='reopen'?remote:local,revision:phase==='reopen'?5:4,accepted:0,conflicts:0};
        if(phase==='reopen')records.set('user:doc',{key:'user:doc',userId:'user',fileName:'synthetic-draft',documentId:'doc',
            baseRevision:4,conflict:false,updatedAt:Date.now(),payload:structuredClone(local)});
        const app=harness(t,{remote,records,sharedServer});await waitFor(()=>!app.booting());
        if(phase==='resume'){
            app.listeners.get('freeze')();sharedServer.document=remote;sharedServer.revision=5;
            app.listeners.get('resume')();
            await waitFor(()=>app.bootstrapCount()===2 && ['saved','conflict'].includes(app.element('autosave-status').dataset.state));
        }
        assert.equal(app.element('recovery-panel').hidden,true);
        assert.equal(app.element('btn-save-draft').disabled,false);
        assert.equal(app.element('service-form').classList.contains('is-readonly'),false);
        assert.equal(app.payload().observations,local.observations);
        assert.equal(sharedServer.accepted,0,'equivalent data does not need a write');
        assert.equal((await app.coordinator.prepareOperation('draft')).base_revision,5);
        app.coordinator.setCommitPending(false);
    });

for(const [name,change] of [
    ['material reference',p=>p.materials=[{qty:'',ref:'R1',description:''}]],
    ['material description',p=>p.materials=[{qty:'',ref:'',description:'Detetor'}]],
    ['zero numeric quantity',p=>p.materials=[{qty:0,ref:'',description:''}]],
    ['zero text quantity',p=>p.materials=[{qty:'0',ref:'',description:''}]],
    ['unknown row field',p=>p.materials=[{qty:'',ref:'',description:'',extra:''}]],
    ['actual checklist',p=>p.maintenance_checklists=[{id:'local-real',location:'Local fictício'}]],
    ['other form field',p=>p.observations='Texto diferente']
])test(`empty-section comparison keeps a real difference protected: ${name}`,async t=>{
    const remote={observations:'Texto preservado',materials:[],maintenance_checklists:[]};
    const local=structuredClone(remote);change(local);
    const records=new Map([['user:doc',{key:'user:doc',userId:'user',fileName:'synthetic-draft',documentId:'doc',
        baseRevision:4,conflict:false,updatedAt:Date.now(),payload:structuredClone(local)}]]);
    const sharedServer={document:remote,revision:5,accepted:0,conflicts:0};
    const app=harness(t,{remote,records,sharedServer});await waitFor(()=>!app.booting());
    assert.equal(app.element('recovery-panel').hidden,false);
    assert.equal(app.element('btn-save-draft').disabled,true);
    assert.equal(app.element('service-form').classList.contains('is-readonly'),true);
    assert.deepEqual(records.get('user:doc').payload,local,'comparison must not rewrite recovery');
    assert.equal(sharedServer.accepted,0);
});

for(const choice of ['source','recovery'])test(`source conflict during editing loads the true source and stays editable after ${choice}`,async t=>{
    const original={...signed('valid'),intervention_report:'Old server autosave'};
    const sharedServer={document:original,revision:1,accepted:0,conflicts:0};
    const app=harness(t,{remote:original,sharedServer});
    await waitFor(()=>!app.element('btn-save-draft').disabled);
    sharedServer.sourceDocument={...original,intervention_report:'New external source'};
    sharedServer.sourceConflict={token:'synthetic-source-token',detected_at:100};
    sharedServer.revision++;
    app.setPayload({...original,intervention_report:'Latest local work'});
    app.coordinator.markDirty();
    await waitFor(()=>app.bootstrapCount()===2 && !app.element('btn-ignore-recovery').disabled);
    assert.equal(sharedServer.accepted,0);
    const button=choice==='source'?'btn-ignore-recovery':'btn-restore-recovery';
    await app.element(button).events.get('click')();
    const expected=choice==='source'?'New external source':'Latest local work';
    assert.equal(sharedServer.document.intervention_report,expected);
    assert.equal(app.payload().intervention_report,expected);
    assert.equal(app.element('btn-save-draft').disabled,false);
    assert.equal(app.element('btn-save-send').disabled,false);
    app.setPayload({...app.payload(),intervention_report:expected+' then continued'});
    app.coordinator.markDirty();
    await waitFor(()=>sharedServer.accepted===2);
    assert.equal(sharedServer.document.intervention_report,expected+' then continued');
});

test('failed source read offers a read retry and never submits the old server autosave as the source',async t=>{
    const original={...signed('valid'),intervention_report:'Old server autosave'};
    const sharedServer={document:original,revision:1,accepted:0,conflicts:0};
    const app=harness(t,{remote:original,sharedServer});
    await waitFor(()=>!app.element('btn-save-draft').disabled);
    app.failBootstrap();
    sharedServer.sourceDocument={...original,intervention_report:'True external source'};
    sharedServer.sourceConflict={token:'source-token',detected_at:100};sharedServer.revision++;
    app.setPayload({...original,intervention_report:'Local preserved'});
    app.coordinator.markDirty();
    await waitFor(()=>app.element('btn-ignore-recovery').textContent==='Voltar a obter versões');
    assert.equal(sharedServer.accepted,0);
    assert.equal(app.element('btn-restore-recovery').disabled,true);
    app.recoverNetwork();
    await app.element('btn-ignore-recovery').events.get('click')();
    assert.equal(sharedServer.accepted,0); // Retrying a read is never choosing a version.
    await app.element('btn-ignore-recovery').events.get('click')();
    assert.equal(sharedServer.document.intervention_report,'True external source');
});
