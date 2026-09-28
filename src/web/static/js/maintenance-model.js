(function (root) {
    "use strict";
    const present = value => value !== undefined && value !== null && String(value).trim() !== "";
    const applicable = doc => Boolean(doc.service_types?.manutencao && doc.equipments?.sadi);
    const signatureWaived = (site, role, definition) => Boolean(definition.signature_exceptions?.[role]) && site[definition.signature_exceptions[role].field] === true;
    const signatureErrors = (site, definition) => ["technician", "customer"]
        .filter(role => !signatureWaived(site, role, definition) && !site.signatures?.[role]?.token)
        .map(role => `Assinatura do ${role === 'technician' ? 'técnico' : 'cliente'}`);
    function validateSite(site, definition) {
        const errors = [];
        const photos = site.photos || [], photoIds = new Set();
        if (photos.length > (definition.photo_limits?.count || 10)) errors.push('Fotografias: excedeu o limite por local');
        for (const [index,photo] of photos.entries()) {
            if (!/^[a-zA-Z0-9_-]{8,64}$/.test(photo.id || '') || photoIds.has(photo.id)) errors.push(`Fotografia ${index+1}: identificador inválido ou repetido`);
            photoIds.add(photo.id);
            if (photo.error || !/^data:image\/(jpeg|png);base64,[A-Za-z0-9+/=]+$/.test(photo.image || '') || photo.image.length > (definition.photo_limits?.bytes || 1_000_000)*4/3+32) errors.push(`Fotografia ${index+1}: imagem inválida; remova e adicione novamente`);
        }
        for (const [key, label] of [["location", "Identificação do local"], ["date", "Data da manutenção"], ["technician", "Técnico de serviço"]]) {
            if (!present(site[key])) errors.push(label);
        }
        if (site.date && (!/^\d{4}-\d{2}-\d{2}$/.test(site.date) || Number.isNaN(Date.parse(site.date)) || new Date(site.date).toISOString().slice(0, 10) !== site.date)) errors.push("Data da manutenção inválida");
        if (!definition.periods[site.period]) errors.push("Periodicidade");
        if (site.period === "other" && !present(site.period_other)) errors.push("Descrição da periodicidade");
        const checks = (answers, questions, prefix) => {
            for (const [key, label] of questions) {
                const answer = answers?.[key];
                if (!["OK", "NC", "NA"].includes(answer?.answer)) errors.push(`${prefix}: ${label}`);
                else if (answer.answer === "NC" && !present(answer.justification)) errors.push(`${prefix}: justificação NC — ${label}`);
            }
        };
        checks(site.general, definition.general, "Sistema");
        for (const [kind, title] of Object.entries(definition.groups)) {
            if (typeof site.configuration?.[kind] !== "boolean") errors.push(`Existência de ${title.toLowerCase()}`);
            if (!site.configuration?.[kind]) continue;
            if (!site[kind]?.length) errors.push(`Quantidade de ${title.toLowerCase()}`);
            (site[kind] || []).forEach((unit, index) => {
                const prefix = `${title} ${index + 1}`;
                for (const [key, label, type] of definition.fields[kind]) {
                    if (!present(unit[key]) || (type === "number" && !/^\d+$/.test(String(unit[key])))) errors.push(`${prefix}: ${label}`);
                }
                if (/^\d+$/.test(unit.total) && /^\d+$/.test(unit.used) && Number(unit.used) > Number(unit.total)) errors.push(`${prefix}: quantidade em uso superior ao total`);
                checks(unit.checks, definition[kind], prefix);
            });
        }
        checks(site.peripherals, definition.peripherals, "Periféricos");
        checks(site.trials, definition.trials, "Periféricos / Ensaios");
        const percent = present(site.coverage_percent) ? String(site.coverage_percent).replace(",", ".") : "";
        if (percent && !(Number(percent) >= 0 && Number(percent) <= 100)) errors.push("Periféricos: percentagem inválida");
        if (["monthly", "quarterly", "half_yearly"].includes(site.period) && !percent && !present(site.coverage_areas)) errors.push("Periféricos: percentagem ou áreas testadas");
        return errors;
    }
    function status(site, definition) {
        if (validateSite(site, definition).length) {
            return site.period || Object.values(site.general || {}).some(value => value.answer)
                || Object.values(site.configuration || {}).some(value => typeof value === "boolean") ? "Em preenchimento" : "Por preencher";
        }
        return signatureErrors(site, definition).length ? "Por assinar" : "Completa";
    }
    const api = { applicable, validateSite, status, signatureWaived, signatureErrors };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    else root.MaintenanceModel = api;
})(typeof window !== "undefined" ? window : globalThis);
