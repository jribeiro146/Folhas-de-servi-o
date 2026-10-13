const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const model = require('../../src/web/static/js/maintenance-model.js');
const editor = fs.readFileSync(path.join(__dirname, '../../src/web/static/js/maintenance-editor.js'), 'utf8');

function functionSource(name) {
    const start = editor.indexOf(`    function ${name}(`);
    assert.notEqual(start, -1);
    return editor.slice(start, editor.indexOf('\n    function ', start + 10));
}

test('server verification warnings reappear after hydration and identify recollection and possible key change', () => {
    const site = {id:'synthetic-site', location:'Local fictício', signatures:{},
        signature_warnings:{technician:'verification_failed',customer:'verification_failed'}};
    const signatureNotice = {};
    const invalidatedSignatures = new Map([['previous-document:technician','stale']]);
    const context = {sites:[site], signatureNotice, invalidatedSignatures, model,
        def:{signature_exceptions:{customer:{field:'customer_not_present'}},
            signature_verification_warning:'A chave de segurança pode ter mudado. Recolha e guarde uma nova assinatura.'}};
    vm.runInNewContext(`${functionSource('restoreSignatureWarnings')}\n${functionSource('renderSignatureNotice')}\nthis.restore=restoreSignatureWarnings;this.render=renderSignatureNotice;`, context);
    context.restore(); context.render();
    assert.equal(invalidatedSignatures.has('previous-document:technician'), false);
    assert.equal(signatureNotice.hidden, false);
    assert.match(signatureNotice.textContent, /chave de segurança/);
    assert.match(signatureNotice.textContent, /nova assinatura/);
    assert.match(signatureNotice.textContent, /técnico e do cliente/);
    site.customer_not_present = true;
    context.restore(); context.render();
    assert.doesNotMatch(signatureNotice.textContent, /cliente/);
    assert.equal(signatureNotice.hidden, false);
    site.signatures.technician = {token:'new-proof'};
    context.restore(); context.render();
    assert.equal(signatureNotice.hidden, true);
});

test('unknown warning values are not rendered as supplied text', () => {
    const context = {sites:[{id:'synthetic', signatures:{},signature_warnings:{technician:'<script>private</script>'}}],
        invalidatedSignatures:new Map(),model,def:{signature_exceptions:{}}};
    vm.runInNewContext(`${functionSource('restoreSignatureWarnings')}\nrestoreSignatureWarnings();`, context);
    assert.equal(context.invalidatedSignatures.size,0);
});

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
