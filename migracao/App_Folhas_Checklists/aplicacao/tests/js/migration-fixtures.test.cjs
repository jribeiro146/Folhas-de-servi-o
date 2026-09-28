"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const packageRoot = path.resolve(__dirname, "../../..");
const load = rel => JSON.parse(fs.readFileSync(path.join(packageRoot, rel), "utf8"));
const validation = require("../../src/web/static/js/document-validation.js");
const maintenance = require("../../src/web/static/js/maintenance-model.js");
const catalog = load("dados/catalogos-folha.json");
const definition = load("dados/sadi-definition.json");
const index = load("dados/cenarios/index.json");
for (const item of index) {
    test(`Cenário exportado: ${item.id}`, () => {
        const doc = load(`dados/cenarios/${item.id}.json`);
        const actual = validation.validate(doc, catalog.validation);
        assert.equal(actual.missing.length, item.expected.documentMissing);
        assert.equal(actual.invalid.length, item.expected.documentInvalid);
        assert.deepEqual(doc.maintenance_checklists.map(site => maintenance.validateSite(site, definition).length), item.expected.siteErrors);
        if (item.expected.siteStatus) assert.deepEqual(doc.maintenance_checklists.map(site => maintenance.status(site, definition)), item.expected.siteStatus);
    });
}
test("O catálogo completo SADI mantém 34 perguntas e identificadores únicos por secção", () => {
    const lists = ["general", "conventional", "addressable", "repeater", "peripherals", "trials"].map(key => definition[key]);
    assert.equal(lists.flat().length, 34);
    lists.forEach(list => assert.equal(new Set(list.map(([key]) => key)).size, list.length));
});
test("Dados embebidos do laboratório correspondem aos JSON transportáveis", () => {
    const context = { window: {} };
    vm.runInNewContext(fs.readFileSync(path.join(packageRoot, "laboratorio/dados-demo.js"), "utf8"), context);
    const demo = JSON.parse(JSON.stringify(context.window.MIGRACAO_DEMO));
    assert.deepEqual(demo.catalog, catalog);
    assert.deepEqual(demo.definition, definition);
    for (const item of demo.cases) assert.deepEqual(item.document, load(`dados/cenarios/${item.id}.json`));
});
