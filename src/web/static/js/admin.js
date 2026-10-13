(function () {
    "use strict";
    const $ = (id) => document.getElementById(id);
    const csrf = document.body.dataset.adminCsrf;
    let pending = null;
    let reconciliation = null;
    const decisions = {accepted:"Aceitação comprovada", rejected:"Recusa comprovada",
        indeterminate:"Indeterminado — bloqueio mantido", repair_teams:"Recuperação do aviso Teams"};
    const kinds = {upload_active:"Publicar rascunho",archive_and_remove:"Arquivar e publicar folha",
        archive_and_mail_local:"Enviar folha por e-mail",notify_teams:"Enviar aviso Teams",remove_active:"Retirar folha ativa"};
    const statuses = {pending:"Pendente",running:"Em processamento",failed:"Falhou",held:"Aguardar confirmação local",complete:"Concluído"};
    const jobErrors = {job_failed:"Falha de processamento; consultar o registo da operação.",
        job_reconciliation_required:"Resultado incerto; é necessária reconciliação antes de repetir.",
        job_invalid_state:"Estado inválido; é necessária recuperação antes de repetir."};
    const actions = {release_sheet:"Folha libertada",restore_sheet_access:"Acesso reposto",
        retry_job_requested:"Repetição pedida",retry_job_accepted:"Repetição aceite",refresh_requested:"Atualização pedida"};
    const date = (timestamp) => timestamp ? new Date(timestamp * 1000).toLocaleString("pt-PT") : "Sem atividade registada";
    const node = (tag, text, parent) => {
        const element = document.createElement(tag);
        element.textContent = text;
        parent.append(element);
        return element;
    };
    const request = async (url, data) => {
        const response = await fetch(url, data ? { method: "POST", headers: {
            "Content-Type": "application/json", "X-Admin-CSRF": csrf
        }, body: JSON.stringify(data) } : { headers: { "Accept": "application/json" } });
        const result = await response.json();
        if (!response.ok || !result.success) throw new Error(result.error || "Não foi possível concluir o pedido.");
        return result;
    };
    const action = (parent, label, url, data, detail, dangerous = false) => {
        const button = node("button", label, parent);
        button.type = "button";
        if (dangerous) button.classList.add("danger");
        button.addEventListener("click", () => {
            pending = { url, data };
            $("admin-action-title").textContent = label;
            $("admin-action-detail").textContent = detail;
            $("admin-reason").value = "";
            $("admin-action-error").textContent = "";
            $("admin-confirm").showModal();
            $("admin-reason").focus();
        });
        return button;
    };
    const renderDocuments = (documents) => {
        const root = $("admin-documents");
        root.replaceChildren();
        if (!documents.length) node("p", "Não existem reservas para gerir.", root);
        for (const doc of documents) {
            const card = node("article", "", root);
            node("h3", doc.name, card);
            if (doc.quarantined) {
                node("p", "Esta folha requer recuperação. Os dados guardados foram preservados; a edição e o desbloqueio estão suspensos.", card);
                continue;
            }
            node("p", "Proprietário: " + (doc.owner?.owner_name || "Sem proprietário") + " · Sessões: " + doc.session_count, card);
            node("p", "Última atividade: " + date(doc.last_seen_at) + " · Gravação automática: " + date(doc.autosaved_at), card);
            const controls = node("div", "", card);
            controls.classList.add("toolbar");
            const release = action(controls, "Expulsar e libertar", "/api/admin/documents/" + encodeURIComponent(doc.document_id) + "/release",
                { lock_version: doc.lock_version }, "Terminar as sessões desta folha e libertar a reserva. Os técnicos expulsos ficam sem acesso de edição nesta folha até à reposição.", true);
            release.disabled = doc.pending_operation || (!doc.owner && !doc.session_count);
            if (doc.pending_operation) node("p", "Existe uma operação pendente. Resolva-a antes de libertar esta folha.", card);
            for (const user of doc.blocked_users) {
                action(controls, "Repor acesso: " + user.name, "/api/admin/documents/" + encodeURIComponent(doc.document_id) + "/restore",
                    { user_id: user.id, lock_version: doc.lock_version }, "Repor o acesso a esta folha. Não retira a reserva de outro técnico nem recupera o token anterior.");
            }
        }
    };
    const renderJobs = (jobs) => {
        const root = $("admin-jobs");
        root.replaceChildren();
        if (!jobs.length) node("p", "Não existem trabalhos pendentes ou falhados.", root);
        for (const job of jobs) {
            const card = node("article", "", root);
            node("h3", (kinds[job.kind] || job.kind) + " · " + (statuses[job.status] || job.status), card);
            node("p", "Operação: " + job.id + " · Tentativas: " + job.attempts, card);
            node("p", jobErrors[job.error_code] || (job.will_retry ? "Nova tentativa automática agendada." :
                job.last_error || job.phase || "A aguardar processamento."), card);
            if (job.retry_not_before > Date.now() / 1000) {
                const deadline = new Date(job.retry_not_before * 1000);
                const provider = job.kind === "notify_teams" ? "Teams" : "fornecedor";
                node("p", Number.isNaN(deadline.getTime())
                    ? "O " + provider + " indicou um prazo muito distante. A repetição está bloqueada."
                    : "A repetição está bloqueada pelo " + provider + " até " + date(job.retry_not_before) + ".", card);
            }
            if (job.error_code === "job_reconciliation_required" || job.invalid_state ||
                (job.status === "failed" && job.reconciliation_required && !job.will_retry)) {
                node("p", "Requer revisão. A repetição está bloqueada.", card);
            }
            if (job.kind === "remove_active" && job.status === "failed") node("p", "Cancelamento não concluído. Confirme o estado remoto antes de intervir.", card);
            if (job.can_retry) action(card, "Repetir trabalho", "/api/admin/jobs/" + encodeURIComponent(job.id) + "/retry", {},
                "Repetir apenas esta operação. Se incluir um envio recusado, pode originar um envio de e-mail.");
            if (job.status === "failed" && job.kind !== "upload_active" && (job.reconciliation_required || job.invalid_state)) {
                const review = node("button", "Consultar reconciliação", card);
                review.type = "button";
                review.addEventListener("click", () => openReconciliation(job.id));
            }
        }
    };
    const openReconciliation = async (id) => {
        try {
            const url = "/api/admin/jobs/" + encodeURIComponent(id) + "/reconciliation";
            const data = (await request(url)).reconciliation;
            const allowed = Array.isArray(data.allowed_decisions) ? data.allowed_decisions : [];
            reconciliation = {url, revision: data.revision, allowed};
            $("admin-reconciliation-detail").textContent = "Operação: " + data.id + " · " + (kinds[data.kind] || data.kind) + " · " + (statuses[data.status] || data.status);
            if (data.request_id) $("admin-reconciliation-detail").textContent += " · Pedido externo: " + data.request_id;
            for (const key of ["indeterminate", "accepted", "rejected"]) $("admin-" + key).disabled = !allowed.includes(key);
            $("admin-repair-teams").disabled = !allowed.includes("repair_teams");
            $("admin-decision").value = allowed[0] || "indeterminate";
            $("admin-reconciliation-submit").disabled = !allowed.length;
            $("admin-evidence").value = "";
            $("admin-reconciliation-reason").value = "";
            $("admin-reconciliation-error").textContent = allowed.length ? "" : "Esta operação não admite uma decisão administrativa neste estado.";
            const history = $("admin-reconciliation-history");
            history.replaceChildren();
            for (const event of data.history) {
                node("p", "Administrador · " + date(event.at) + " · " + decisions[event.decision] + (event.applied ? "" : " · Retenção mantida"), history);
                node("p", event.reason, history);
            }
            $("admin-reconciliation").showModal();
        } catch (error) { $("admin-status").textContent = error.message; }
    };
    const load = async () => {
        $("admin-reload").disabled = true;
        try {
            const data = await request("/api/admin/overview");
            renderDocuments(data.documents);
            renderJobs(data.jobs);
            const history = $("admin-audit");
            history.replaceChildren();
            if (!data.audit.length) node("p", "Ainda não existem intervenções administrativas.", history);
            for (const entry of data.audit) {
                const card = node("article", "", history);
                node("h3", (actions[entry.action] || entry.action) + " · " + date(entry.created_at), card);
                node("p", (entry.actor_label || "Administrador") + " · " + entry.target, card);
                node("p", entry.reason, card);
            }
            $("admin-refresh").hidden = !data.refresh;
            $("admin-graph").textContent = data.refresh
                ? "SharePoint: " + (data.refresh.in_progress ? "a atualizar" : "sem atualização em curso") +
                  ". Última atualização: " + date(data.refresh.last_completed_at) +
                  (data.refresh.last_error ? ". Erro: " + data.refresh.last_error : "")
                : "SharePoint não está ativo nesta instalação.";
            $("admin-queue-status").textContent = data.queue_summary
                ? "Fila: " + (data.queue_summary.pending || 0) + " pendentes, " +
                  (data.queue_summary.running || 0) + " em processamento, " + (data.queue_summary.failed || 0) + " falhados."
                : "A fila de publicações não está ativa nesta instalação.";
            $("admin-status").textContent = "Diagnóstico atualizado. Reservas: " + data.documents.length + ". Trabalhos apresentados: " + data.jobs.length + ".";
        } catch (error) {
            $("admin-status").textContent = error.message;
        } finally {
            $("admin-reload").disabled = false;
        }
    };
    $("admin-reload").addEventListener("click", load);
    $("admin-reconciliation-lookup").addEventListener("submit", (event) => {
        event.preventDefault();
        openReconciliation($("admin-job-id").value.trim());
    });
    $("admin-reconciliation-cancel").addEventListener("click", () => { reconciliation = null; $("admin-reconciliation").close(); });
    $("admin-reconciliation-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!reconciliation) return;
        $("admin-reconciliation-submit").disabled = true;
        try {
            const decision = $("admin-decision").value;
            if (!reconciliation.allowed.includes(decision)) throw new Error("Esta decisão não está disponível. Atualize o diagnóstico.");
            let evidence = null;
            if (decision === "accepted" || decision === "rejected") {
                const file = $("admin-evidence").files?.[0];
                if (!file || file.size > 65536) throw new Error("Anexe o registo de evidência, até 64 KiB.");
                try { evidence = JSON.parse(await file.text()); }
                catch { throw new Error("O ficheiro de evidência não contém JSON válido."); }
            }
            const result = (await request(reconciliation.url, {revision: reconciliation.revision, decision,
                reason: $("admin-reconciliation-reason").value.trim(), evidence})).result;
            reconciliation = null;
            $("admin-reconciliation").close();
            await load();
            $("admin-status").textContent += result.applied ? " Decisão registada." : " Decisão registada; retenção mantida.";
        } catch (error) { $("admin-reconciliation-error").textContent = error.message; }
        finally { $("admin-reconciliation-submit").disabled = !reconciliation?.allowed.length; }
    });
    $("admin-refresh").addEventListener("click", () => {
        pending = { url: "/api/admin/refresh", data: {} };
        $("admin-action-title").textContent = "Atualizar SharePoint";
        $("admin-action-detail").textContent = "Pedir uma nova leitura da lista remota e atualizar a cache.";
        $("admin-reason").value = "";
        $("admin-action-error").textContent = "";
        $("admin-confirm").showModal();
        $("admin-reason").focus();
    });
    $("admin-cancel").addEventListener("click", () => { pending = null; $("admin-confirm").close(); });
    $("admin-action-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!pending) return;
        $("admin-submit").disabled = true;
        try {
            await request(pending.url, { ...pending.data, reason: $("admin-reason").value.trim() });
            pending = null;
            $("admin-confirm").close();
            await load();
            $("admin-status").textContent += " Intervenção aceite.";
        } catch (error) {
            $("admin-action-error").textContent = error.message + " Se a reserva mudou, cancele e atualize o diagnóstico.";
        } finally {
            $("admin-submit").disabled = false;
        }
    });
    load();
}());
