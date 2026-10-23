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
