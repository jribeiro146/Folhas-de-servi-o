(function (root) {
    "use strict";
    const PREFIX = "sensorpoint-commit-recovery-v1:";
    const validId = value => typeof value === "string" && /^[A-Za-z0-9:_-]{1,240}$/.test(value);
    const create = ({scope, storage, fetchFn = root.fetch.bind(root)}) => {
        const key = file => PREFIX + encodeURIComponent(file);
        const read = file => {
            try {
                const raw = storage?.getItem(key(file));
                if (!raw || raw.length > 2048) return null;
                const record = JSON.parse(raw);
                if (record?.scope !== scope || record.kind !== "send" || !validId(record.documentId)
                        || !validId(record.operationId)) return null;
                return {scope, kind: record.kind, documentId: record.documentId, operationId: record.operationId};
            } catch (_) {
                // An unusable hint is not authority to send or to disable other
                // actions. Preserve it and fall back to normal server bootstrap,
                // whose lease/closed-document checks still govern all writes.
                return null;
            }
        };
        const begin = (file, metadata) => {
            if (!/^[a-f0-9]{64}$/.test(scope || "") || !validId(metadata.document_id)
                    || !validId(metadata.idempotency_key)) throw new Error("A sessão de gravação não está pronta.");
            const record = {scope, kind: "send", documentId: metadata.document_id, operationId: metadata.idempotency_key};
            // Save before the POST: a lost response/reload must remain consultable.
            // Refuse to send when this browser cannot retain the opaque reference.
            try {
                storage.setItem(key(file), JSON.stringify(record));
                if (storage.getItem(key(file)) !== JSON.stringify(record)) throw new Error();
            } catch (_) { throw new Error("O navegador não conseguiu guardar a referência da operação. A folha não foi enviada."); }
            return {"X-Mail-Monitor-Scope": scope};
        };
        const clear = file => {
            if (read(file)) storage.removeItem(key(file));
        };
        const lookup = async file => {
            const record = read(file);
            if (!record) return null;
            const controller = new root.AbortController();
            const timeout = root.setTimeout(() => controller.abort(), 10000);
            try {
                const response = await fetchFn(`/api/editing/operations/${encodeURIComponent(record.documentId)}/${encodeURIComponent(record.operationId)}`,
                    {headers: {"Accept":"application/json", "X-Mail-Monitor-Scope":scope}, cache:"no-store", signal:controller.signal});
                if (response.status === 404) return {record, status:"missing"};
                if (response.status === 403) {
                    const refusal = await response.json().catch(() => ({}));
                    if (refusal.code === "mail_monitor_session_changed") {
                        const error = new Error("A sessão mudou. Recarregue a página para retomar a edição.");
                        error.code = refusal.code;
                        throw error;
                    }
                }
                if (!response.ok) throw new Error("Não foi possível consultar a gravação. Volte a tentar; nenhum envio foi repetido.");
                const data = await response.json();
                if (!data.success || data.operation?.kind !== record.kind
                        || !["complete", "pending", "failed", "interrupted"].includes(data.operation.status)) throw new Error("Resposta de gravação inválida.");
                return {...data.operation, record};
            } finally { root.clearTimeout(timeout); }
        };
        return {read, begin, clear, lookup};
    };
    if (root.document) {
        let storage;
        try { storage = root.sessionStorage; } catch (_) { /* begin refuses before a write */ }
        root.SensorpointCommitRecovery = create({scope:root.__FILES_APP__?.mailMonitorScope, storage});
    }
    if (typeof module !== "undefined" && module.exports) module.exports = {create, PREFIX};
}(typeof globalThis !== "undefined" ? globalThis : this));
