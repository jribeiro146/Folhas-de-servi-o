(function (root) {
    "use strict";

    class MailJobMonitorError extends Error {
        constructor(message, { code = "mail_job_error", job = null, jobId = null, retryAt = null } = {}) {
            super(message);
            this.name = "MailJobMonitorError";
            this.code = code;
            this.job = job;
            this.jobId = jobId || job?.id || null;
            this.retryAt = retryAt;
        }
    }

    const defaultWait = (milliseconds) => new Promise((resolve) => {
        root.setTimeout(resolve, milliseconds);
    });

    const jobErrorMessage = () =>
        "A folha foi arquivada, mas o e-mail não foi enviado. Contacte o administrador para analisar este trabalho.";

    const waitForMailJob = async (jobId, options = {}) => {
        const identifier = String(jobId || "").trim();
        if (!identifier) {
            throw new MailJobMonitorError(
                "A aplicação não recebeu o identificador do envio de e-mail.",
                { code: "missing_job_id" }
            );
        }

        const fetchFn = options.fetchFn || root.fetch?.bind(root);
        if (typeof fetchFn !== "function") {
            throw new MailJobMonitorError(
                "Não foi possível consultar o estado do envio de e-mail.",
                { code: "status_unavailable" }
            );
        }
        const waitFn = options.waitFn || defaultWait;
        const intervalMs = Number.isFinite(options.intervalMs) ? Math.max(0, options.intervalMs) : 500;
        const timeoutMs = Number.isFinite(options.timeoutMs) ? Math.max(0, options.timeoutMs) : 240000;
        const requestTimeoutMs = Number.isFinite(options.requestTimeoutMs)
            ? Math.max(1, options.requestTimeoutMs) : 15000;
        const clock = options.clockFn || (() => root.performance?.now?.() ?? Date.now());
        const now = options.nowFn || Date.now;
        const deadline = clock() + timeoutMs;
        const maxPolls = Number.isInteger(options.maxPolls)
            ? options.maxPolls
            : Math.max(1, Math.ceil(timeoutMs / Math.max(intervalMs, 1)));

        const pause = async delay => {
            const remaining = deadline - clock();
            if (remaining > 0) await waitFn(Math.min(delay, remaining));
        };
        let lastJob = null;

        for (let poll = 0; poll < maxPolls && clock() < deadline; poll += 1) {
            let response, payload;
            const controller = typeof root.AbortController === "function" ? new root.AbortController() : null;
            let timer;
            try {
                const request = (async () => {
                    const reply = await fetchFn(
                        `/api/graph/jobs/${encodeURIComponent(identifier)}`,
                        { headers: { "Accept": "application/json" }, cache: "no-store", signal: controller?.signal }
                    );
                    const body = reply.ok ? await reply.json() : null;
                    return { response: reply, payload: body };
                })();
                const expiry = new Promise((_, reject) => {
                    timer = root.setTimeout(() => {
                        controller?.abort();
                        reject(new Error("Consultation timed out"));
                    }, Math.max(1, Math.min(requestTimeoutMs, deadline - clock())));
                });
                ({ response, payload } = await Promise.race([request, expiry]));
            } catch (_error) {
                await pause(intervalMs);
                continue;
            } finally {
                root.clearTimeout(timer);
            }
            if (response.status === 429 || response.status >= 500) {
                const header = String(response.headers?.get?.("Retry-After") || "").trim();
                let retryAt = null;
                if (/^\d+$/.test(header)) {
                    retryAt = Math.min(Number.MAX_VALUE, now() + Number(header) * 1000);
                } else if (header && Number.isFinite(Date.parse(header))) {
                    retryAt = Date.parse(header);
                }
                const delay = retryAt === null ? intervalMs : Math.max(intervalMs, retryAt - now());
                if (retryAt !== null && retryAt - now() >= deadline - clock()) {
                    throw new MailJobMonitorError(
                        "O serviço pediu uma pausa nas consultas. O resultado do envio continua por confirmar.",
                        { code: "mail_status_deferred", jobId: identifier, retryAt }
                    );
                }
                await pause(delay);
                continue;
            }
            if (!response.ok) {
                const outcomes = {
                    401: ["A sessão terminou. Inicie sessão para consultar este envio.", "mail_session_expired"],
                    403: ["Não tem autorização para consultar este envio. Contacte o administrador.", "mail_access_denied"],
                    404: ["O estado deste envio não foi encontrado. O resultado continua por confirmar; contacte o administrador.", "mail_status_missing"],
                };
                const [message, code] = outcomes[response.status] ||
                    ["Não foi possível consultar o estado deste envio. O resultado continua por confirmar.", "status_request_failed"];
                throw new MailJobMonitorError(message, { code, jobId: identifier });
            }
            if (!payload?.success || !payload.job) {
                throw new MailJobMonitorError(
                    "Não foi possível consultar o estado do envio de e-mail.",
                    { code: "status_request_failed", jobId: identifier }
                );
            }

            const job = payload.job;
            if (job.id !== identifier) {
                throw new MailJobMonitorError(
                    "A resposta recebida não identifica este envio. O resultado continua por confirmar.",
                    { code: "status_response_invalid", jobId: identifier }
                );
            }
            lastJob = job;
            if (job.result?.mail?.accepted === true) {
                return job;
            }
            if (job.result?.commit_guard_required) {
                throw new MailJobMonitorError(
                    "A conclusão local da folha está por confirmar. Contacte o administrador antes de qualquer repetição.",
                    { code: "mail_commit_unconfirmed", job }
                );
            }
            if (job.status === "complete") {
                throw new MailJobMonitorError(
                    "O trabalho terminou sem confirmação de aceitação do e-mail.",
                    { code: "mail_not_confirmed", job }
                );
            }
            if (job.status === "failed" && !job.will_retry) {
                if (job.result?.invalid_state) {
                    throw new MailJobMonitorError(
                        "Os dados do trabalho são inválidos. É necessária revisão antes de repetir.",
                        { code: "mail_invalid_state", job }
                    );
                }
                if (job.reconciliation_required) {
                    throw new MailJobMonitorError(
                        "O resultado do envio é desconhecido. Confirme a aceitação antes de repetir.",
                        { code: "mail_unknown", job }
                    );
                }
                throw new MailJobMonitorError(
                    jobErrorMessage(job),
                    { code: "mail_failed", job }
                );
            }
            if (job.status === "cancelled") {
                throw new MailJobMonitorError(
                    "O envio de e-mail foi cancelado.",
                    { code: "mail_cancelled", job }
                );
            }

            const nextAttempt = job.next_attempt_at;
            if ((job.will_retry || job.status === "pending") &&
                typeof nextAttempt === "number" && Number.isFinite(nextAttempt) && nextAttempt > 0) {
                const retryAt = Math.min(Number.MAX_VALUE, nextAttempt * 1000);
                const delay = Math.max(intervalMs, retryAt - now());
                if (retryAt - now() >= deadline - clock()) {
                    throw new MailJobMonitorError(
                        "O envio está agendado para uma tentativa posterior. A aceitação ainda não está confirmada.",
                        { code: "mail_scheduled", job, retryAt }
                    );
                }
                await pause(delay);
                continue;
            }

            await pause(intervalMs);
        }

        throw new MailJobMonitorError(
            "A folha foi arquivada, mas ainda não foi possível confirmar o envio do e-mail.",
            { code: "mail_status_timeout", jobId: identifier, job: lastJob }
        );
    };

    const api = { MailJobMonitorError, waitForMailJob };
    root.SensorpointMailJobMonitor = api;
    if (typeof module !== "undefined" && module.exports) {
        module.exports = api;
    }
}(typeof globalThis !== "undefined" ? globalThis : this));
