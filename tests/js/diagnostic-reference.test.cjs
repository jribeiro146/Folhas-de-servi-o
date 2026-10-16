const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../../src/web/static/js/document-editor.js"), "utf8");
const actions = source.slice(source.indexOf("    const commitMessageKey ="), source.indexOf("    fileSearch?.addEventListener"));

for (const name of ['saveDraft','sendFile']) for (const recovered of [false,true]) {
    test(name+' reconciles invalid responses before any explicit retry; recovered='+recovered, async()=>{
        for(const body of ['html',null,{success:true}]) {
            const calls=[], messages=[];
            const context={activeFileName:'synthetic',mailEnabled:false,hideConfirm(){},setBusy(){},
                collectFormData:()=>({intervention_report:'Current form'}),requireOperationMetadata:async()=>({}),buildCommitRequest:()=>({}),
                commitPhotoState:()=>assert.fail('An invalid response is not proof of commit'),
                showToast:message=>messages.push(message),
                fetch:async()=>{calls.push('POST');return {ok:false,status:500,headers:{get:()=> 'synthetic-reference'},json:async()=>{
                    if(body==='html')throw new SyntaxError("Unexpected token '<', '<!doctype html>' is not valid JSON");
                    return body;
                }};},
                window:{SensorpointCommitRecovery:{begin:()=>({})},__EDITING_COORDINATOR__:{
                    recoverPendingCommit:async()=>{calls.push('GET receipt');return recovered;},
                    markCommitted:()=>assert.fail('Cannot commit without valid response'),handleConflict:()=>false}}};
            vm.runInNewContext(actions+'\nthis.run='+name+';',context);
            await context.run({intervention_report:'Current form'});
            assert.deepEqual(calls,['POST','GET receipt']);
            assert.equal(messages.length,recovered?0:1);
            assert.doesNotMatch(messages.join(),/Unexpected|doctype|SyntaxError/);
            if(!recovered)assert.match(messages[0],/synthetic-reference/);
        }
    });
}

for (const name of ["saveDraft", "sendFile", "cancelFile", "openDocumentPreview"]) {
    test(name + " shows the diagnostic reference without repeating the POST", async () => {
        const messages = [], requests = [];
        const error = "Não foi possível concluir o pedido. Referência: ref-sintetica.";
        const context = {
            activeFileName: "synthetic", mailEnabled: true, hideConfirm() {}, setBusy() {}, t: key => key,
            collectFormData: () => ({}), requireOperationMetadata: async () => ({}), buildCommitRequest: () => ({}),
            commitPhotoState: () => assert.fail("Refused operation cannot commit"),
            showToast: message => messages.push(message),
            fetch: async (url, options) => {
                requests.push([url, options.method]);
                return { ok: false, status: 503, json: async () => ({ success: false,
                    error, reference: "ref-sintetica", code: "operation_failed" }) };
            },
            window: {
                SensorpointCommitRecovery: {begin: () => ({})},
                location: { href: "original" },
                open: () => ({ document: { write() {} }, close() {} }),
                __EDITING_COORDINATOR__: { handleConflict: () => false,
                    markCommitted: () => assert.fail("Refused operation cannot commit") },
            },
        };
        vm.runInNewContext(actions + "\nthis.run = " + name + ";", context);
        await context.run({ customer_email: "synthetic@example.invalid" });
        assert.deepEqual(messages, [error]);
        assert.equal(requests.length, 1);
        assert.equal(requests[0][1], "POST");
        assert.equal(context.window.location.href, "original");
    });
}
