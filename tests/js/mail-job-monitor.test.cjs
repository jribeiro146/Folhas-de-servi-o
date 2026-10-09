const test = require("node:test");
const assert = require("node:assert/strict");

const {
    waitForMailJob,
} = require("../../src/web/static/js/mail-job-monitor.js");

test("an ambiguous send requires reconciliation instead of offering retry", async () => {
    const job = { id: "synthetic", status: "failed", will_retry: false,
        reconciliation_required: true, result: { phase: "mail_unknown" } };
    await assert.rejects(waitForMailJob(job.id, {
        fetchFn: async () => ({ ok: true, json: async () => ({ success: true, job }) }),
        waitFn: async () => {},
    }), error => error.code === "mail_unknown" && error.job === job);
});

test("migration quarantine keeps the unknown-send warning visible", async () => {
    const job = { id: "migrated", status: "failed", will_retry: false,
        reconciliation_required: true,
        result: { invalid_state: true, migration_hold: true, phase: "sending_mail" } };
    await assert.rejects(waitForMailJob(job.id, {
        fetchFn: async () => ({ ok: true, json: async () => ({ success: true, job }) }),
        waitFn: async () => {},
    }), error => error.code === "mail_unknown" && /desconhecido/.test(error.message));
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

test("reports a terminal failure with the job available for administrative diagnosis", async () => {
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

test("requires state review instead of offering retry for corrupt jobs", async () => {
    const job = { id: "corrupt", status: "failed", will_retry: false,
        result: { invalid_state: true } };
    const fetchFn = async () => ({ ok: true, json: async () => ({ success: true, job }) });
    await assert.rejects(waitForMailJob("corrupt", { fetchFn, waitFn: async () => {} }),
        (error) => error.code === "mail_invalid_state");
});

test("a temporary 503 with HTML waits for Retry-After and consults only the same job", async () => {
    const calls = [], waits = [];
    const job = { id: "send:temporary", status: "complete", result: { mail: { accepted: true } } };
    const result = await waitForMailJob(job.id, {
        fetchFn: async (url, options) => {
            calls.push({ url, method: options.method || "GET", body: options.body });
            if (calls.length === 1) return { ok: false, status: 503,
                headers: { get: name => name === "Retry-After" ? "2" : null },
                json: async () => { throw new SyntaxError("Proxy HTML"); } };
            return { ok: true, status: 200, json: async () => ({ success: true, job }) };
        },
        waitFn: async delay => waits.push(delay),
        maxPolls: 3,
    });
    assert.equal(result, job);
    assert.deepEqual(waits, [2000]);
    assert.deepEqual(calls, [
        { url: "/api/graph/jobs/send%3Atemporary", method: "GET", body: undefined },
        { url: "/api/graph/jobs/send%3Atemporary", method: "GET", body: undefined },
    ]);
});

for (const outage of ["network", 502, "invalid_json"]) {
    test(`recovers the same job after a temporary ${outage} consultation failure`, async () => {
        let calls = 0;
        const job = { id: "recover", status: "complete", result: { mail: { accepted: true } } };
        const result = await waitForMailJob(job.id, {
            maxPolls: 3, waitFn: async () => {},
            fetchFn: async () => {
                if (++calls === 1) {
                    if (outage === "network") throw new TypeError("Network unavailable");
                    return { ok: outage !== 502, status: outage === 502 ? 502 : 200,
                        json: async () => { throw new SyntaxError("Temporary truncated response"); } };
                }
                return { ok: true, status: 200, json: async () => ({ success: true, job }) };
            },
        });
        assert.equal(result, job);
        assert.equal(calls, 2);
    });
}

for (const [status, code] of [[401, "mail_session_expired"], [403, "mail_access_denied"],
    [404, "mail_status_missing"], [400, "status_request_failed"]]) {
    test(`HTTP ${status} has a safe specific outcome even when its body is HTML`, async () => {
        let calls = 0;
        await assert.rejects(waitForMailJob("original", {
            maxPolls: 2, waitFn: async () => {},
            fetchFn: async () => {
                calls++;
                return { ok: false, status, json: async () => { throw new SyntaxError("secret"); } };
            },
        }), error => {
            assert.equal(error.code, code);
            assert.equal(error.jobId, "original");
            assert.doesNotMatch(error.message, /secret|não foi enviado/);
            return true;
        });
        assert.equal(calls, 1);
    });
}

test("a silent request is aborted within the consultation budget without resending", { timeout: 1500 }, async () => {
    let calls = 0, signal;
    const started = Date.now();
    await assert.rejects(waitForMailJob("silent", {
        timeoutMs: 40, requestTimeoutMs: 15, intervalMs: 5,
        fetchFn: async (_url, options) => { calls++; signal = options.signal; return new Promise(() => {}); },
    }), error => error.code === "mail_status_timeout" && error.jobId === "silent");
    assert.ok(Date.now() - started < 1000);
    assert.ok(calls >= 1 && calls <= 3);
    assert.equal(signal.aborted, true);
});

test("Retry-After HTTP date on 429 is respected, including when the deadline exceeds this session", async () => {
    for (const [header, expectedCode, expectedDelay] of [
        ["Fri, 09 Oct 2026 10:00:02 GMT", null, 2000],
        ["86400", "mail_status_deferred", null],
    ]) {
        let calls = 0;
        const waits = [];
        const job = { id: "throttled", status: "complete", result: { mail: { accepted: true } } };
        const promise = waitForMailJob(job.id, {
            nowFn: () => Date.parse("2026-10-09T10:00:00Z"),
            maxPolls: 3, waitFn: async delay => waits.push(delay),
            fetchFn: async () => ++calls === 1 ? {
                ok: false, status: 429, headers: { get: () => header },
            } : { ok: true, json: async () => ({ success: true, job }) },
        });
        if (expectedCode) {
            await assert.rejects(promise, error => error.code === expectedCode &&
                error.jobId === job.id && error.retryAt === Date.parse("2026-10-10T10:00:00Z"));
            assert.equal(calls, 1);
            assert.deepEqual(waits, []);
        } else {
            assert.equal(await promise, job);
            assert.deepEqual(waits, [expectedDelay]);
        }
    }
});

test("provisional commits and long scheduled jobs remain unconfirmed, with safe diagnostics", async () => {
    for (const [job, code] of [
        [{ id: "state", status: "held" }, "mail_status_timeout"],
        [{ id: "state", status: "failed", result: { commit_guard_required: true } }, "mail_commit_unconfirmed"],
        [{ id: "state", status: "failed", will_retry: true, next_attempt_at: 1791547200 }, "mail_scheduled"],
        [{ id: "state", status: "failed", last_error: "PRIVATE_PROVIDER_PAYLOAD" }, "mail_failed"],
        [{ id: "different", status: "complete", result: { mail: { accepted: true } } }, "status_response_invalid"],
    ]) {
        await assert.rejects(waitForMailJob("state", {
            maxPolls: 1, waitFn: async () => {}, nowFn: () => Date.parse("2026-10-09T10:00:00Z"),
            fetchFn: async () => ({ ok: true, json: async () => ({ success: true, job }) }),
        }), error => {
            assert.equal(error.code, code);
            assert.equal(error.jobId, "state");
            assert.doesNotMatch(error.message, /PRIVATE_PROVIDER_PAYLOAD/);
            if (code === "mail_scheduled") assert.equal(error.retryAt, 1791547200000);
            return true;
        });
    }
});

test("a response body that never finishes is bounded just like a silent request", { timeout: 1500 }, async () => {
    let signal;
    await assert.rejects(waitForMailJob("silent-body", {
        timeoutMs: 30, requestTimeoutMs: 10, intervalMs: 5,
        fetchFn: async (_url, options) => {
            signal = options.signal;
            return { ok: true, json: async () => new Promise(() => {}) };
        },
    }), error => error.code === "mail_status_timeout" && error.jobId === "silent-body");
    assert.equal(signal.aborted, true);
});

test("repeated outages stop after the poll limit and retain the original identifier", async () => {
    let calls = 0;
    await assert.rejects(waitForMailJob("offline", {
        maxPolls: 3, waitFn: async () => {},
        fetchFn: async () => { calls++; throw new TypeError("PRIVATE_NETWORK_DETAIL"); },
    }), error => {
        assert.equal(error.code, "mail_status_timeout");
        assert.equal(error.jobId, "offline");
        assert.doesNotMatch(error.message, /PRIVATE_NETWORK_DETAIL|não foi enviado/);
        return true;
    });
    assert.equal(calls, 3);
});

test("a held commit can become accepted during consultation", async () => {
    const job = { id: "held-then-accepted", status: "complete", result: { mail: { accepted: true } } };
    const replies = [{ id: job.id, status: "held" }, job];
    const result = await waitForMailJob(job.id, {
        maxPolls: 3, waitFn: async () => {},
        fetchFn: async () => ({ ok: true, json: async () => ({ success: true, job: replies.shift() }) }),
    });
    assert.equal(result, job);
});

for (const scenario of ["past_pending", "past_retry", "503_without_deadline", "503_past_deadline", "held"]) {
    test(`an inactive worker or unavailable status ends as timeout: ${scenario}`, async () => {
        let elapsed = 0;
        const now = Date.parse("2026-10-09T10:00:00Z");
        await assert.rejects(waitForMailJob("waiting", {
            timeoutMs: 25, intervalMs: 10, maxPolls: 8, clockFn: () => elapsed,
            nowFn: () => now + elapsed,
            waitFn: async delay => { elapsed += delay; },
            fetchFn: async () => scenario.startsWith("503") ? {
                ok: false, status: 503,
                headers: { get: () => scenario === "503_past_deadline" ? "Fri, 09 Oct 2026 09:00:00 GMT" : null },
            } : { ok: true, json: async () => ({ success: true, job: {
                id: "waiting", status: scenario === "held" ? "held" : scenario === "past_retry" ? "failed" : "pending",
                will_retry: scenario === "past_retry", next_attempt_at: now / 1000 - 100,
            } }) },
        }), error => {
            assert.equal(error.code, "mail_status_timeout");
            assert.equal(error.retryAt, null);
            assert.doesNotMatch(error.message, /agendado|pediu uma pausa|conclusão local/i);
            return true;
        });
    });
}

test("a future queue retry within the consultation budget waits until due and then confirms", async () => {
    const waits = [];
    let calls = 0;
    const now = Date.parse("2026-10-09T10:00:00Z");
    const accepted = { id: "due", status: "complete", result: { mail: { accepted: true } } };
    const result = await waitForMailJob("due", {
        timeoutMs: 10000, maxPolls: 3, nowFn: () => now,
        waitFn: async delay => waits.push(delay),
        fetchFn: async () => ({ ok: true, json: async () => ({ success: true,
            job: ++calls === 1 ? { id: "due", status: "failed", will_retry: true,
                next_attempt_at: now / 1000 + 2 } : accepted,
        }) }),
    });
    assert.equal(result, accepted);
    assert.deepEqual(waits, [2000]);
    assert.equal(calls, 2);
});
