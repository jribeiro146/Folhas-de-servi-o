const { test } = require("node:test");
const assert = require("node:assert/strict");
const { validate } = require("../../src/web/static/js/document-validation.js");

const rules = {
    requiredFields: [{ key: "customer_name", label: "Cliente / Customer" }],
    technicianRequiredFields: Object.entries({ technician: "Nome", start_time: "Hora de início", end_time: "Hora de fim", total_hours: "Total de horas", date: "Data" }).map(([key, label]) => ({ key, label })),
    signatureRequiredFields: [{ key: "customer_signer_name", label: "Primeiro e último nome" }, { key: "customer_signature_date", label: "Data da assinatura" }],
};
const complete = () => ({
    customer_name: "Cliente fictício", customer_signer_name: "Maria Santos", customer_signature_date: "2026-09-08",
    technician_records: [{ technician: "Técnico fictício", start_time: "09:00", end_time: "10:00", total_hours: "1 h", date: "2026-09-08" }],
});

test("all technician fields and signature details are required", () => {
    assert.deepEqual(validate({}, rules).missing, ["Cliente / Customer", "Nome do técnico 1", "Hora de início do técnico 1", "Hora de fim do técnico 1", "Total de horas do técnico 1", "Data do técnico 1", "Primeiro e último nome", "Data da assinatura"]);
});

test("each used row is validated, unused rows are ignored", () => {
    const doc = complete();
    doc.technician_records.push({ total_hours_overridden: false });
    assert.deepEqual(validate(doc, rules), { missing: [], invalid: [] });
    doc.technician_records.push({ ...doc.technician_records[0], date: "" });
    assert.deepEqual(validate(doc, rules).missing, ["Data do técnico 2"]);
});

test("client absence waives only the signature details", () => {
    const doc = complete();
    doc.client_not_present = true;
    doc.customer_signer_name = "";
    doc.customer_signature_date = "";
    assert.deepEqual(validate(doc, rules), { missing: [], invalid: [] });
    doc.technician_records[0].start_time = "";
    assert.deepEqual(validate(doc, rules).missing, ["Hora de início do técnico 1"]);
});

test("signer needs first and last name, preserving accents and compound names", () => {
    const doc = complete();
    for (const name of ["Maria", " Maria  ", "Santos-Silva"]) {
        doc.customer_signer_name = name;
        assert.deepEqual(validate(doc, rules).invalid, ["Primeiro e último nome"]);
    }
    for (const name of ["Maria Santos", " João   de Sá ", "Ana-Maria D'Ávila"]) {
        doc.customer_signer_name = name;
        assert.deepEqual(validate(doc, rules).invalid, []);
    }
});

test("impossible dates and times are rejected", () => {
    const doc = complete();
    doc.customer_signature_date = "2026-02-30";
    Object.assign(doc.technician_records[0], { date: "2026-02-29", start_time: "25:00", end_time: "09:75" });
    assert.deepEqual(validate(doc, rules).invalid, ["Hora de início do técnico 1", "Hora de fim do técnico 1", "Data do técnico 1", "Data da assinatura"]);
    doc.client_not_present = true;
    assert.equal(validate(doc, rules).invalid.includes("Data da assinatura"), false);
});
