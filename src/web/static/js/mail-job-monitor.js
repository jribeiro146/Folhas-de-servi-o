(function (root) {
    "use strict";

    class MailJobMonitorError extends Error {
        constructor(message, { code = "mail_job_error", job = null } = {}) {
            super(message);
            this.name = "MailJobMonitorError";
            this.code = code;
            this.job = job;
        }
    }

    const defaultWait = (milliseconds) => new Promise((resolve) => {
        root.setTimeout(resolve, milliseconds);
    });

    const jobErrorMessage = (job) => {
        const detail = String(job?.last_error || "").trim();
        return detail
            ? `A folha foi arquivada, mas o e-mail não foi enviado: ${detail}`
            : "A folha foi arquivada, mas o e-mail não foi enviado.";
    };

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
        const intervalMs = Number.isFinite(options.intervalMs) ? options.intervalMs : 500;
        const timeoutMs = Number.isFinite(options.timeoutMs) ? options.timeoutMs : 240000;
        const maxPolls = Number.isInteger(options.maxPolls)
            ? options.maxPolls
            : Math.max(1, Math.ceil(timeoutMs / Math.max(intervalMs, 1)));

        for (let poll = 0; poll < maxPolls; poll += 1) {
            const response = await fetchFn(
                `/api/graph/jobs/${encodeURIComponent(identifier)}`,
                { headers: { "Accept": "application/json" } }
            );
            const payload = await response.json();
            if (!response.ok || !payload.success || !payload.job) {
                throw new MailJobMonitorError(
                    payload.error || "Não foi possível consultar o estado do envio de e-mail.",
                    { code: "status_request_failed" }
                );
            }

            const job = payload.job;
            if (job.result?.mail?.accepted === true) {
                return job;
            }
            if (job.status === "complete") {
                throw new MailJobMonitorError(
                    "O trabalho terminou sem confirmação de aceitação do e-mail.",
                    { code: "mail_not_confirmed", job }
                );
            }
            if (job.status === "failed" && !job.will_retry) {
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

            await waitFn(intervalMs);
        }

        throw new MailJobMonitorError(
            "A folha foi arquivada, mas ainda não foi possível confirmar o envio do e-mail.",
            { code: "mail_status_timeout" }
        );
    };

    const api = { MailJobMonitorError, waitForMailJob };
    root.SensorpointMailJobMonitor = api;
    if (typeof module !== "undefined" && module.exports) {
        module.exports = api;
    }
}(typeof globalThis !== "undefined" ? globalThis : this));
