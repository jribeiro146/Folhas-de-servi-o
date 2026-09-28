const test = require("node:test");
const assert = require("node:assert/strict");

const {
    waitForMailJob,
} = require("../../src/web/static/js/mail-job-monitor.js");

test("an ambiguous send requires reconciliation and does not offer a duplicate retry", async () => {
    const fetchFn = async () => ({ ok: true, json: async () => ({ success: true, job: {
        id: "unknown", status: "failed", reconciliation_required: true,
        result: { phase: "mail_unknown" }, will_retry: false,
    } }) });
    await assert.rejects(waitForMailJob("unknown", { fetchFn, waitFn: async () => {} }),
        error => error.code === "mail_unknown" && /desconhecido/i.test(error.message));
});

test("waits through a scheduled retry and only confirms an accepted email", async () => {
    const jobs = [
        { id: "send:1", status: "pending", will_retry: false },
        {
            id: "send:1",
            status: "failed",
            will_retry: true,
            last_error: "Serviço temporariamente indisponível.",
        },
        {
            id: "send:1",
            status: "complete",
            will_retry: false,
            result: { mail: { accepted: true } },
        },
    ];
    let requests = 0;
    const fetchFn = async () => {
        requests += 1;
        return {
            ok: true,
            json: async () => ({ success: true, job: jobs.shift() }),
        };
    };

    const completed = await waitForMailJob("send:1", {
        fetchFn,
        waitFn: async () => {},
        maxPolls: 5,
        intervalMs: 0,
    });

    assert.equal(completed.status, "complete");
    assert.equal(completed.result.mail.accepted, true);
    assert.equal(requests, 3);
});

test("reports a terminal failure with the job available for explicit retry", async () => {
    const failedJob = {
        id: "send:2",
        status: "failed",
        will_retry: false,
        last_error: "A autenticação do serviço de e-mail foi rejeitada.",
    };
    const fetchFn = async () => ({
        ok: true,
        json: async () => ({ success: true, job: failedJob }),
    });

    await assert.rejects(
        waitForMailJob("send:2", { fetchFn, waitFn: async () => {} }),
        (error) => {
            assert.equal(error.code, "mail_failed");
            assert.equal(error.job, failedJob);
            assert.match(error.message, /não foi enviado/i);
            return true;
        }
    );
});

test("does not claim success when a completed job lacks Graph acceptance", async () => {
    const fetchFn = async () => ({
        ok: true,
        json: async () => ({
            success: true,
            job: { id: "send:3", status: "complete", result: { mail: null } },
        }),
    });

    await assert.rejects(
        waitForMailJob("send:3", { fetchFn, waitFn: async () => {} }),
        (error) => error.code === "mail_not_confirmed"
    );
});

test("confirms the email when Graph accepted it even if a later Teams step failed", async () => {
    const job = {
        id: "send:4",
        status: "failed",
        will_retry: false,
        last_error: "O aviso Teams falhou.",
        result: { mail: { accepted: true } },
    };
    const fetchFn = async () => ({
        ok: true,
        json: async () => ({ success: true, job }),
    });

    const result = await waitForMailJob("send:4", {
        fetchFn,
        waitFn: async () => {},
    });

    assert.equal(result, job);
});

test("keeps monitoring beyond the server stale threshold by default", async () => {
    let requests = 0;
    const fetchFn = async () => {
        requests += 1;
        const job = requests > 360
            ? {
                id: "send:5",
                status: "failed",
                will_retry: false,
                last_error: "O processamento ficou sem atividade.",
            }
            : { id: "send:5", status: "running", will_retry: false };
        return {
            ok: true,
            json: async () => ({ success: true, job }),
        };
    };

    await assert.rejects(
        waitForMailJob("send:5", { fetchFn, waitFn: async () => {} }),
        (error) => error.code === "mail_failed"
    );
    assert.equal(requests, 361);
});
