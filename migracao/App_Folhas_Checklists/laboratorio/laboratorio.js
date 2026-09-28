(() => {
    "use strict";
    const data = window.MIGRACAO_DEMO;
    const selector = document.getElementById("scenario");
    const payload = document.getElementById("payload");
    const results = document.getElementById("results");
    const add = (tag, text, className = "") => {
        const element = document.createElement(tag);
        element.textContent = text;
        element.className = className;
        results.append(element);
        return element;
    };
    const errors = (items) => {
        const list = add("ul", "");
        items.forEach(text => {
            const item = document.createElement("li");
            item.textContent = text;
            list.append(item);
        });
    };
    function validate() {
        results.replaceChildren();
        try {
            const doc = JSON.parse(payload.value);
            if (!doc || typeof doc !== "object" || Array.isArray(doc)) throw new Error("O documento deve ser um objeto JSON.");
            if (doc.technician_records && (!Array.isArray(doc.technician_records) || doc.technician_records.some(row => !row || typeof row !== "object"))) throw new Error("technician_records deve conter objetos.");
            if (doc.maintenance_checklists && !Array.isArray(doc.maintenance_checklists)) throw new Error("maintenance_checklists deve ser uma lista.");
            const result = window.DocumentValidation.validate(doc, data.catalog.validation);
            add("h3", "Folha de serviço");
            if (!result.missing.length && !result.invalid.length) add("p", "Campos obrigatórios e formatos verificados pelo JS: sem erros.", "ok");
            errors([...result.missing.map(x => `Falta: ${x}`), ...result.invalid.map(x => `Inválido: ${x}`)]);
            const applicable = window.MaintenanceModel.applicable(doc);
            add("h3", "Checklists SADI");
            add("p", applicable ? "Manutenção + SADI selecionados." : "Não aplicável à seleção atual.");
            const sites = doc.maintenance_checklists || [];
            if (applicable && !sites.length) add("p", "Sem locais. O validador de local, por si só, não valida a quantidade de locais do documento.", "error");
            if (applicable) sites.forEach((site, index) => {
                const pending = window.MaintenanceModel.validateSite(site, data.definition);
                const state = window.MaintenanceModel.status(site, data.definition);
                add("h3", `Local ${index + 1}: ${site.location || "Por identificar"}`);
                add("p", state === "Completa" ? "Completa no indicador visual — a presença de tokens não comprova assinaturas válidas." : state, pending.length ? "error" : "ok");
                errors(pending);
            });
            add("p", "Este resultado não verifica assinaturas, persistência, autorização, concorrência nem finalização.");
        } catch (error) {
            results.replaceChildren();
            add("p", `Não foi possível validar: ${error.message}`, "error");
            add("p", "Reponha um exemplo ou corrija a estrutura do JSON. Os validadores originais esperam dados já normalizados.");
        }
    }
    function load() {
        const scenario = data.cases.find(item => item.id === selector.value);
        payload.value = JSON.stringify(scenario.document, null, 2);
        document.getElementById("scenario-note").textContent = scenario.note;
        validate();
    }
    data.cases.forEach(item => {
        const option = document.createElement("option");
        option.value = item.id;
        option.textContent = item.title;
        selector.append(option);
    });
    selector.addEventListener("change", load);
    document.getElementById("restore").addEventListener("click", load);
    document.getElementById("validate").addEventListener("click", validate);
    load();
})();
