const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const model = require('../../src/web/static/js/maintenance-model.js');
const editor = fs.readFileSync(path.join(__dirname, '../../src/web/static/js/maintenance-editor.js'), 'utf8');

test('absence removes invalidated customer notice while technician remains required', () => {
    const start = editor.indexOf('    function renderSignatureNotice()');
    const end = editor.indexOf('\n    function ', start + 10);
    const site = {id:'synthetic-site', location:'Local fictício', customer_not_present:false};
    const signatureNotice = {};
    const invalidatedSignatures = new Set(['synthetic-site:technician', 'synthetic-site:customer']);
    const context = {sites:[site], signatureNotice, invalidatedSignatures, model,
        def:{signature_exceptions:{customer:{field:'customer_not_present'}}}};
    vm.runInNewContext(`${editor.slice(start, end)}\nthis.render = renderSignatureNotice;`, context);
    context.render();
    assert.match(signatureNotice.textContent, /cliente/);
    site.customer_not_present = true;
    context.render();
    assert.equal(signatureNotice.hidden, false);
    assert.match(signatureNotice.textContent, /técnico/);
    assert.doesNotMatch(signatureNotice.textContent, /cliente/);
    invalidatedSignatures.delete('synthetic-site:technician');
    context.render();
    assert.equal(signatureNotice.hidden, true);
});
