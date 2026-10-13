(function (root) {
    "use strict";
    const KEY = "sensorpoint-mail-followup-v1";
    const TTL = 24 * 60 * 60 * 1000;
    const LIMIT = 10;
    const validId = id => typeof id === "string" && /^[A-Za-z0-9:_-]{1,240}$/.test(id);
    const validTime = value => typeof value === "number" && Number.isFinite(value) && value >= 0;
    const create = ({scope, storage, fetchFn = root.fetch.bind(root),
        monitor = root.SensorpointMailJobMonitor, now = Date.now,
        clock = () => root.performance?.now?.() ?? Date.now(), onChange = () => {}}) => {
        let records = [], completed = [], hydrated = false, verified = false, epoch = 0;
        let checking = null, timer = null, warning = null;
        const active = new Map(), messages = new Map(), cancellations = new Map();
        const validScope = value => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
        const emit = () => onChange({verified, warning,
            entries: verified ? [...records.map(record => ({...record, code: messages.get(record.id) || "pending"})),
                ...completed] : []});
        const save = (confirmedNow = false) => {
            try {
                if (!storage) throw new Error();
                const existing = storage.getItem(KEY);
                if (existing && existing.length <= 16384 &&
                    JSON.parse(existing)?.scope !== scope && !confirmedNow) {
                    warning = "scope_unverified";
                    return; // An old page must not replace a newer login's references.
                }
                storage.setItem(KEY, JSON.stringify({scope, entries: records}));
                if (["storage_unavailable", "scope_unverified"].includes(warning)) warning = null;
            } catch (_) { warning = "storage_unavailable"; }
        };
        const hydrate = () => {
            if (hydrated) return;
            hydrated = true;
            try {
                if (!storage) throw new Error();
                const raw = storage.getItem(KEY);
                if (!raw) { records = []; return; }
                if (raw.length > 16384) throw new Error();
                const data = JSON.parse(raw);
                if (data.scope !== scope) { records = []; return; }
                if (!Array.isArray(data.entries) || data.entries.length > LIMIT) throw new Error();
                const ids = new Set();
                records = data.entries.filter(record => {
                    if (!record || !validId(record.id) || ids.has(record.id) ||
                        !validTime(record.createdAt) || record.createdAt > now() + 300000 ||
                        record.expiresAt !== record.createdAt + TTL || record.expiresAt <= now() ||
                        (record.retryAt != null && !validTime(record.retryAt))) return false;
                    ids.add(record.id);
                    return true;
                }).map(record => ({id: record.id, createdAt: record.createdAt,
                    expiresAt: record.expiresAt, retryAt: record.retryAt || null}));
                save(); // Drop unknown fields and expired references, never queue data.
            } catch (_) {
                warning = "storage_unavailable";
                try { storage?.removeItem(KEY); } catch (_) { /* optional storage */ }
            }
        };
        const clearOwnScope = () => {
            try {
                const raw = storage?.getItem(KEY);
                if (raw && raw.length <= 16384 && JSON.parse(raw).scope === scope) storage.removeItem(KEY);
            } catch (_) { /* Do not erase a newer login's record from an old BFCache page. */ }
            records = []; completed = []; messages.clear();
        };
        const suspend = () => {
            epoch += 1;
            verified = false;
            hydrated = false;
            checking = null;
            active.clear();
            cancellations.clear();
            root.clearTimeout(timer);
            emit();
        };
        const sessionError = code => {
            const error = new Error(code === "suspended"
                ? "A consulta continua quando voltar a esta página; nenhum envio será repetido."
                : code === "expired" ? "O prazo local de acompanhamento terminou; contacte o administrador."
                : "A sessão de acompanhamento mudou. Atualize a página.");
            error.code = code;
            return error;
        };
        const verify = () => {
            if (checking) return checking;
            const generation = epoch;
            const pending = (async () => {
                let deadline;
                const controller = typeof root.AbortController === "function" ? new root.AbortController() : null;
                try {
                    if (!validScope(scope)) throw sessionError("session_changed");
                    const response = await Promise.race([
                        (async () => {
                            const response = await fetchFn("/api/graph/monitor-context", {
                                headers: {"Accept": "application/json"}, cache: "no-store", signal: controller?.signal,
                            });
                            return {status: response.status, ok: response.ok,
                                data: response.ok ? await response.json() : null};
                        })(),
                        new Promise((_, reject) => { deadline = root.setTimeout(() => {
                            controller?.abort(); reject(new Error("context_unavailable"));
                        }, 10000); }),
                    ]);
                    if (generation !== epoch) throw sessionError("suspended");
                    if (response.status === 401 || response.status === 403 ||
                        (response.ok && response.data?.scope !== scope)) {
                        clearOwnScope();
                        suspend();
                        warning = "session_changed";
                        emit();
                        throw sessionError("session_changed");
                    }
                    if (!response.ok || !response.data?.success) throw new Error("context_unavailable");
                    verified = true;
                    if (!["limit", "storage_unavailable"].includes(warning)) warning = null;
                } catch (error) {
                    if (generation === epoch) {
                        verified = false;
                        warning = error.code === "session_changed" ? "session_changed" : "context_unavailable";
                        emit();
                    }
                    throw error;
                } finally { root.clearTimeout(deadline); }
            })();
            checking = pending;
            pending.finally(() => { if (checking === pending) checking = null; }).catch(() => {});
            return pending;
        };
        const remember = (id, responseScope) => {
            if (validScope(responseScope) && responseScope !== scope) {
                suspend();
                scope = responseScope;
                records = []; completed = []; messages.clear(); warning = null;
            }
            if (!validScope(scope) || !validId(id)) return false;
            hydrate();
            if (!records.some(record => record.id === id)) {
                records = records.filter(record => record.expiresAt > now());
                if (records.length >= LIMIT) { warning = "limit"; emit(); return false; }
                const createdAt = now();
                records.push({id, createdAt, expiresAt: createdAt + TTL, retryAt: null});
                save();
            }
            emit();
            return true;
        };
        const forget = id => {
            const cancellation = cancellations.get(id);
            if (cancellation) cancellation.cancelled = true;
            active.delete(id);
            records = records.filter(record => record.id !== id);
            completed = completed.filter(record => record.id !== id);
            messages.delete(id);
            if (warning === "limit" && records.length < LIMIT) warning = null;
            save();
            emit();
        };
        const defer = (record, retryAt, generation) => {
            if (generation !== epoch || !record || !validTime(retryAt) || retryAt <= now()) return;
            record.retryAt = Math.max(record.retryAt || 0, retryAt);
            save();
        };
        const schedule = () => {
            root.clearTimeout(timer);
            const dates = records.filter(record => record.retryAt > now())
                .map(record => Math.min(record.retryAt, record.expiresAt));
            if (verified && dates.length) timer = root.setTimeout(() => resume(),
                Math.max(1, Math.min(...dates) - now() + 1));
        };
        const start = (id, options = {}) => {
            if (active.has(id)) return active.get(id);
            const record = records.find(item => item.id === id);
            if (record?.retryAt > now()) {
                messages.set(id, "mail_status_deferred");
                emit(); schedule();
                return Promise.reject(new monitor.MailJobMonitorError(
                    "A consulta fica em espera pelo prazo indicado pelo serviço. O resultado continua por confirmar.",
                    {code: "mail_status_deferred", jobId: id, retryAt: record.retryAt}));
            }
            const generation = epoch;
            const cancellation = {cancelled: false};
            cancellations.set(id, cancellation);
            const current = () => generation === epoch && !cancellation.cancelled &&
                (!record || record.expiresAt > now());
            const denied = () => ({ok: false, status: 403});
            const guardedFetch = async (url, options) => {
                if (!current()) return denied();
                const response = await fetchFn(url, {...options,
                    headers: {...options.headers, "X-Mail-Monitor-Scope": scope}});
                if (!current()) return denied();
                if (response.status === 429 || response.status >= 500) {
                    const header = String(response.headers?.get?.("Retry-After") || "").trim();
                    const retryAt = /^\d+$/.test(header) ? Math.min(Number.MAX_VALUE, now() + Number(header) * 1000)
                        : Date.parse(header);
                    defer(record, retryAt, generation);
                }
                return {ok: response.ok, status: response.status, headers: response.headers,
                    json: async () => {
                        const body = await response.json();
                        if (!current()) return {success: false};
                        const job = body?.job;
                        if (body?.success && job?.id === id) {
                            verified = true;
                            if (warning === "context_unavailable") warning = null;
                            save(true); // This scoped GET just confirmed the session server-side.
                        }
                        if (job?.id === id && (job.will_retry || job.status === "pending") &&
                            validTime(job.next_attempt_at)) {
                            defer(record, Math.min(Number.MAX_VALUE, job.next_attempt_at * 1000), generation);
                        }
                        return body;
                    }};
            };
            messages.set(id, "checking"); emit();
            const promise = (async () => {
                try {
                    const job = await monitor.waitForMailJob(id, {...options, fetchFn: guardedFetch,
                        clockFn: () => current() ? (options.clockFn || clock)() : Infinity});
                    if (!current()) throw sessionError("suspended");
                    records = records.filter(item => item.id !== id);
                    messages.delete(id);
                    completed = [...completed.filter(item => item.id !== id),
                        {id, code: "accepted"}].slice(-LIMIT);
                    save(); emit();
                    return job;
                } catch (error) {
                    if (generation !== epoch || cancellation.cancelled) throw sessionError("suspended");
                    if (record?.expiresAt <= now()) { forget(id); throw sessionError("expired"); }
                    if (["mail_session_expired", "mail_access_denied"].includes(error.code)) {
                        clearOwnScope(); suspend(); warning = "session_changed";
                        emit();
                        // The foreground form must not reopen a modal with the
                        // previous account's reference after this rejection.
                        throw sessionError("session_changed");
                    } else {
                        messages.set(id, error.code || "status_unavailable");
                        defer(record, error.retryAt, generation);
                    }
                    emit();
                    throw error;
                } finally {
                    if (generation === epoch && cancellations.get(id) === cancellation) {
                        cancellations.delete(id); active.delete(id); schedule();
                    }
                }
            })();
            active.set(id, promise);
            return promise;
        };
        const waitForMailJob = async (id, options = {}) => {
            remember(id);
            // The POST supplied this reference. Its scoped status GET performs
            // the session check itself and retains the M03 retry/time budget.
            // A separate context preflight must not make this path less resilient.
            return start(id, options);
        };
        const resume = async () => {
            try {
                await verify();
                hydrate();
                records = records.filter(record => record.expiresAt > now());
                save(true); emit(); schedule();
                await Promise.allSettled(records.map(record => start(record.id)));
            } catch (_) { /* Context failure is shown without exposing stored references. */ }
        };
        return {remember, waitForMailJob, resume, suspend, forget};
    };

    const labels = {pending: "Por confirmar", checking: "A consultar o envio",
        accepted: "E-mail aceite para envio pela Microsoft; não confirma entrega",
        mail_unknown: "Resultado desconhecido; é necessária reconciliação pelo administrador",
        mail_failed: "Falha registada; contacte o administrador",
        mail_invalid_state: "Dados inválidos; é necessária revisão",
        mail_commit_unconfirmed: "Conclusão local por confirmar; contacte o administrador",
        mail_cancelled: "Envio cancelado", mail_not_confirmed: "Sem confirmação de aceitação",
        mail_status_missing: "Trabalho não encontrado; resultado por confirmar",
        mail_scheduled: "Tentativa de envio agendada; aceitação por confirmar",
        mail_status_deferred: "Consultas em espera pelo prazo indicado pelo serviço",
        mail_status_timeout: "Ainda sem confirmação; pode voltar a consultar",
        status_response_invalid: "Resposta inválida; resultado por confirmar"};
    const warnings = {storage_unavailable: "Este navegador não guardou o acompanhamento para a próxima navegação.",
        scope_unverified: "A gravação desta referência aguarda confirmação da sessão; a consulta atual pode continuar.",
        limit: "Limite de dez referências nesta aba. Remova um acompanhamento antigo para guardar outro.",
        session_changed: "A sessão de acompanhamento mudou. Atualize a página.",
        context_unavailable: "Não foi possível confirmar a sessão. Pode voltar a consultar; nenhum envio será repetido."};
    if (root.document) {
        let storage;
        try { storage = root.sessionStorage; } catch (_) { /* optional storage */ }
        const tracker = create({scope: root.__FILES_APP__?.mailMonitorScope, storage,
            onChange: view => {
                const panel = root.document.getElementById("mail-followup");
                if (!panel) return;
                panel.replaceChildren();
                panel.hidden = !view.warning && !view.entries.length;
                const node = (tag, text) => {
                    const item = root.document.createElement(tag);
                    item.textContent = text; panel.append(item); return item;
                };
                if (!panel.hidden) node("strong", "Acompanhamento de e-mail nesta aba");
                if (view.warning) node("p", warnings[view.warning] || warnings.context_unavailable);
                for (const entry of view.entries) {
                    node("p", "Referência: " + entry.id + " · " + (labels[entry.code] || "Resultado por confirmar"));
                    if (entry.retryAt > Date.now()) {
                        const date = new Date(entry.retryAt);
                        node("p", Number.isFinite(date.getTime()) ? "Próxima consulta a partir de " + date.toLocaleString("pt-PT")
                            : "O prazo indicado pelo serviço é muito distante.");
                    }
                    const remove = node("button", "Remover acompanhamento");
                    remove.type = "button"; remove.className = "btn btn-secondary";
                    remove.addEventListener("click", () => tracker.forget(entry.id));
                }
                if (!panel.hidden) {
                    const retry = node("button", "Voltar a consultar");
                    retry.type = "button"; retry.className = "btn btn-secondary";
                    retry.addEventListener("click", () => tracker.resume());
                }
            }});
        root.SensorpointMailFollowup = tracker;
        root.document.addEventListener("DOMContentLoaded", () => tracker.resume());
        root.addEventListener("pagehide", () => tracker.suspend());
        root.addEventListener("pageshow", event => { if (event.persisted) tracker.resume(); });
        root.document.addEventListener("visibilitychange", () =>
            root.document.hidden ? tracker.suspend() : tracker.resume());
    }
    if (typeof module !== "undefined" && module.exports) module.exports = {create, KEY, TTL, LIMIT};
}(typeof globalThis !== "undefined" ? globalThis : this));
