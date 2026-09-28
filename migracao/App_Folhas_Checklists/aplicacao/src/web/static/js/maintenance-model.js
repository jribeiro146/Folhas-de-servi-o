(function (root) {
    "use strict";
    const present = value => value !== undefined && value !== null && String(value).trim() !== "";
    const applicable = doc => Boolean(doc.service_types?.manutencao && doc.equipments?.sadi);
    function validateSiteDetails(site, definition) {
        const errors = [];
        const add = (message, path, target = "path") => errors.push({message, path, target});
        const photos = site.photos || [], photoIds = new Set();
        if (photos.length > (definition.photo_limits?.count || 10)) add('Fotografias: excedeu o limite por local', '', 'photos');
        for (const [index,photo] of photos.entries()) {
            if (!/^[a-zA-Z0-9_-]{8,64}$/.test(photo.id || '') || photoIds.has(photo.id)) add(`Fotografia ${index+1}: identificador inválido ou repetido`, String(index), 'photo');
            photoIds.add(photo.id);
            if (photo.error || !/^data:image\/(jpeg|png);base64,[A-Za-z0-9+/=]+$/.test(photo.image || '') || photo.image.length > (definition.photo_limits?.bytes || 1_000_000)*4/3+32) add(`Fotografia ${index+1}: imagem inválida; remova e adicione novamente`, String(index), 'photo');
        }
        for (const [key, label] of [["location", "Identificação do local"], ["date", "Data da manutenção"], ["technician", "Técnico de serviço"]]) {
            if (!present(site[key])) add(label, key);
        }
        if (site.date && (!/^\d{4}-\d{2}-\d{2}$/.test(site.date) || Number.isNaN(Date.parse(site.date)) || new Date(site.date).toISOString().slice(0, 10) !== site.date)) add("Data da manutenção inválida", "date");
        if (!definition.periods[site.period]) add("Periodicidade", "period");
        if (site.period === "other" && !present(site.period_other)) add("Descrição da periodicidade", "period_other");
        const checks = (answers, questions, prefix, path) => {
            for (const [key, label] of questions) {
                const answer = answers?.[key];
                if (!["OK", "NC", "NA"].includes(answer?.answer)) add(`${prefix}: ${label}`, `${path}.${key}.answer`);
                else if (answer.answer === "NC" && !present(answer.justification)) add(`${prefix}: justificação NC — ${label}`, `${path}.${key}.justification`);
            }
        };
        checks(site.general, definition.general, "Sistema", "general");
        for (const [kind, title] of Object.entries(definition.groups)) {
            if (typeof site.configuration?.[kind] !== "boolean") add(`Existência de ${title.toLowerCase()}`, kind, "configuration");
            if (!site.configuration?.[kind]) continue;
            if (!site[kind]?.length) add(`Quantidade de ${title.toLowerCase()}`, kind, "count");
            (site[kind] || []).forEach((unit, index) => {
                const prefix = `${title} ${index + 1}`;
                for (const [key, label, type] of definition.fields[kind]) {
                    if (!present(unit[key]) || (type === "number" && !/^\d+$/.test(String(unit[key])))) add(`${prefix}: ${label}`, `${kind}.${index}.${key}`);
                }
                if (/^\d+$/.test(unit.total) && /^\d+$/.test(unit.used) && Number(unit.used) > Number(unit.total)) add(`${prefix}: quantidade em uso superior ao total`, `${kind}.${index}.used`);
                checks(unit.checks, definition[kind], prefix, `${kind}.${index}.checks`);
            });
        }
        checks(site.peripherals, definition.peripherals, "Periféricos", "peripherals");
        checks(site.trials, definition.trials, "Periféricos / Ensaios", "trials");
        const percent = present(site.coverage_percent) ? String(site.coverage_percent).replace(",", ".") : "";
        if (percent && !(Number(percent) >= 0 && Number(percent) <= 100)) add("Periféricos: percentagem inválida", "coverage_percent");
        if (["monthly", "quarterly", "half_yearly"].includes(site.period) && !percent && !present(site.coverage_areas)) add("Periféricos: percentagem ou áreas testadas", "coverage_percent");
        return errors;
    }
    // Preserve the existing string contract for callers and stored validation messages.
    const validateSite = (site, definition) => validateSiteDetails(site, definition).map(issue => issue.message);
    function status(site, definition) {
        if (validateSite(site, definition).length) {
            return site.period || Object.values(site.general || {}).some(value => value.answer)
                || Object.values(site.configuration || {}).some(value => typeof value === "boolean") ? "Em preenchimento" : "Por preencher";
        }
        return site.signatures?.technician?.token && site.signatures?.customer?.token ? "Completa" : "Por assinar";
    }
    const api = { applicable, validateSite, validateSiteDetails, status };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    else root.MaintenanceModel = api;
})(typeof window !== "undefined" ? window : globalThis);
