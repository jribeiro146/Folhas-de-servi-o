(function (root) {
    const text = (value) => String(value ?? "").trim();
    const validDate = (value) => {
        if (!/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(value) || value.startsWith("0000")) return false;
        const date = new Date(`${value}T00:00:00Z`);
        return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value;
    };

    // The server supplies the required field definitions. Drafts never call this validation.
    const validate = (payload, rules) => {
        const missing = [];
        const invalid = [];
        const requireFields = (record, fields, suffix = "") => {
            fields.forEach(({ key, label }) => {
                if (!text(record[key])) missing.push(label + suffix);
            });
        };
        requireFields(payload, rules.requiredFields);
        const rows = (payload.technician_records || []).filter((record) => (
            rules.technicianRequiredFields.some(({ key }) => text(record[key]))
        ));
        (rows.length ? rows : [{}]).forEach((record, index) => {
            const suffix = ` do técnico ${index + 1}`;
            requireFields(record, rules.technicianRequiredFields, suffix);
            [["start_time", "Hora de início"], ["end_time", "Hora de fim"]].forEach(([key, label]) => {
                const value = text(record[key]);
                if (value && !/^([01][0-9]|2[0-3]):[0-5][0-9]$/.test(value)) invalid.push(label + suffix);
            });
            if (text(record.date) && !validDate(text(record.date))) invalid.push("Data" + suffix);
        });
        if (!payload.client_not_present) {
            requireFields(payload, rules.signatureRequiredFields);
            if (text(payload.customer_signer_name) && text(payload.customer_signer_name).split(/\s+/).length < 2) {
                invalid.push("Primeiro e último nome");
            }
            if (text(payload.customer_signature_date) && !validDate(text(payload.customer_signature_date))) {
                invalid.push("Data da assinatura");
            }
        }
        return { missing, invalid };
    };
    if (typeof module !== "undefined" && module.exports) module.exports = { validate };
    else root.DocumentValidation = { validate };
})(typeof window !== "undefined" ? window : globalThis);
