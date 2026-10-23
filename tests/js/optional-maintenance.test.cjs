const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const model = require('../../src/web/static/js/maintenance-model.js');
const source = fs.readFileSync(path.join(__dirname, '../../src/web/static/js/maintenance-editor.js'), 'utf8');
const start = source.indexOf('        validate(document) {');
const method = source.slice(start, source.indexOf('\n        showValidation,', start));
const document = {service_types:{manutencao:true}, equipments:{sadi:true}};

function validate(overrides={}) {
    const context = {model, sites:[], signatureJobs:new Map(), pendingPhotos:0,
        verificationPending:false, def:{signature_pending_warning:'Verificação em curso'}, ...overrides};
    vm.runInNewContext(`this.editor = {${method}}`, context);
    return Array.from(context.editor.validate(document));
}

test('empty or incomplete unsigned SADI checklists do not block service-sheet finalization', () => {
    assert.deepEqual(validate(), []);
    assert.deepEqual(validate({sites:[{id:'local',location:'',signatures:{}}]}), []);
});
test('recovery verification must finish before finalizing optional checklists', () => {
    assert.deepEqual(validate({verificationPending:true}), ['Verificação em curso']);
});
test('optional checklist signatures and photos cannot be lost while still being saved', () => {
    assert.match(validate({signatureJobs:new Map([['local:technician',{}]])})[0], /Aguarde.*assinaturas/);
    assert.match(validate({pendingPhotos:1})[0], /Aguarde.*fotografias/);
});

const noticeStart = source.indexOf('        finalizationNotice(document) {');
const noticeMethod = source.slice(noticeStart, start);
function finalizationNotice(payload, issues) {
    const context = {model, allIssues:()=>issues};
    vm.runInNewContext(`this.editor = {${noticeMethod}}`, context);
    return context.editor.finalizationNotice(payload);
}
const documentSource = fs.readFileSync(path.join(__dirname, '../../src/web/static/js/document-editor.js'), 'utf8');
const collectStart = documentSource.indexOf('    const collectFormData = () => {');
const collectSource = documentSource.slice(collectStart, documentSource.indexOf('    const getFinalizationValidation', collectStart));
function collectChecklistPayload(editor, preserved=[]) {
    const context = {structuredClone, window:{MaintenanceEditor:editor}, preservedMaintenance:preserved,
        createEmptyDocument:()=>({}), simpleFields:[], collectOptionGroup:()=>({}),
        serviceTypeInputs:[],equipmentInputs:[],materialsUsedYes:{checked:false},
        collectTechnicianRows:()=>[],clientNotPresentInput:{checked:true},signaturePads:[]};
    vm.runInNewContext(collectSource + '\nthis.payload=collectFormData();',context);
    return context.payload;
}
test('empty editor or legacy recovery data never invents an intention to clear private checklists', () => {
    assert.equal(collectChecklistPayload({collect:()=>[]})._maintenance_clear,undefined);
    assert.equal(collectChecklistPayload(undefined)._maintenance_clear,undefined);
    const sites = [{id:'site-a',location:'Local fictício'}];
    const populated = collectChecklistPayload({collect:()=>sites});
    assert.equal(populated._maintenance_clear,undefined);
    assert.deepEqual(populated.maintenance_checklists,sites);
    const preserved = collectChecklistPayload(undefined,sites);
    assert.equal(preserved._maintenance_clear,undefined);
    assert.deepEqual(preserved.maintenance_checklists,sites);
});
const handlerStart = documentSource.indexOf('    btnSaveSend?.addEventListener("click",');
const handlerSource = documentSource.slice(handlerStart, documentSource.indexOf('    btnCancelFile?.addEventListener',handlerStart));
function clickFinalize({pending=[], issues=[], allowed=true, mail=false}={}) {
    let handler, confirmation, toast, summaries=0;
    const context = {activeFileName:'synthetic-sheet', collectFormData:()=>({...document,customer_email:'nobody@example.invalid'}),
        getInvalidDurationInputs:()=>[], getFinalizationValidation:()=>({missing:pending,invalid:[]}),
        showToast:message=>{toast=message;},showConfirm:value=>{confirmation=value;},
        btnSaveSend:{addEventListener:(_name,fn)=>{handler=fn;}},mailEnabled:mail,mailTestRecipient:'',teamsEnabled:false,
        window:{MaintenanceEditor:allowed ? {validate:()=>pending,finalizationNotice:payload=>finalizationNotice(payload,issues),
            showValidation:()=>{summaries++;}} : undefined}};
    vm.runInNewContext(handlerSource,context);
    handler();
    return {confirmation,toast,summaries};
}
test('finalize consumer explains pending writes without opening unrelated checklist issues', () => {
    const pending = validate({signatureJobs:new Map([['local:technician',{}]])});
    const result = clickFinalize({pending,issues:[{message:'Missing signature'}]});
    assert.match(result.toast,/Aguarde.*assinaturas/);
    assert.equal(result.summaries,0);
    assert.equal(result.confirmation,undefined);
});
test('normal archive and email confirmations explain incomplete SADI without blocking', () => {
    for (const mail of [false,true]) {
        const result = clickFinalize({issues:[{message:'Missing signature'}],mail});
        assert.match(result.confirmation.message,/não serão gerados PDFs SADI/);
        assert.match(result.confirmation.message,/não poderá continuar.*depois de arquivar/);
        assert.match(result.confirmation.message,/guarde o rascunho/);
        assert.equal(result.confirmation.confirmLabel,mail?'Guardar e enviar':'Guardar e arquivar');
        assert.equal(typeof result.confirmation.onAccept,'function');
    }
});
test('complete, unrelated and unavailable checklists add no incomplete notice', () => {
    assert.equal(finalizationNotice(document,[]),'');
    assert.equal(finalizationNotice({service_types:{},equipments:{}},[{message:'ignored'}]),'');
    assert.doesNotMatch(clickFinalize().confirmation.message,/checklists/);
    assert.doesNotMatch(clickFinalize({allowed:false,issues:[{}]}).confirmation.message,/checklists/);
});
