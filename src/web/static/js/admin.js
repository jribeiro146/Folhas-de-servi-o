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
    const phases = {uploading_archive:"Publicação do arquivo em curso", generating_pdf:"PDF em preparação",
        pdf_ready:"PDF preparado", removal_unknown:"Resultado da remoção incerto", removing_active:"Remoção da folha ativa em curso",
        active_removed:"Folha ativa removida", cancellation_proof_missing:"Falta confirmação do cancelamento",
        canceling_remote:"Cancelamento remoto em curso", cancellation_rejected:"Cancelamento recusado",
        cancellation_confirmed:"Cancelamento confirmado", message_ready:"Mensagem preparada",
        mail_snapshot_invalid:"Cópia para envio inválida", mail_rejected:"E-mail recusado", sending_mail:"Envio de e-mail iniciado",
        mail_unknown:"Resultado do e-mail incerto", mail_accepted:"E-mail aceite pelo fornecedor",
        teams_rejected:"Aviso Teams recusado", sending_teams:"Envio do aviso Teams iniciado",
        teams_unknown:"Resultado do aviso Teams incerto", teams_accepted:"Aviso Teams aceite pelo fornecedor"};
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
    const tabs = ["documents", "jobs", "audit"];
    let activeTab = "documents";
    const selectTab = (selected, focus = false) => {
        activeTab = selected;
        for (const name of tabs) {
            const button = $("admin-tab-" + name);
            button.setAttribute("aria-selected", String(name === selected));
            button.tabIndex = name === selected ? 0 : -1;
            $("admin-panel-" + name).hidden = name !== selected;
        }
        if (focus) $("admin-tab-" + selected).focus();
        if (selected === "audit") loadActivity();
    };
    // Local navigation only. Reloading diagnostics must keep the current area.
    for (const [index, name] of tabs.entries()) {
        const button = $("admin-tab-" + name);
        button.addEventListener("click", () => selectTab(name));
        button.addEventListener("keydown", (event) => {
            const destinations = {ArrowRight: (index + 1) % tabs.length,
                ArrowLeft: (index + tabs.length - 1) % tabs.length, Home: 0, End: tabs.length - 1};
            if (Object.hasOwn(destinations, event.key)) {
                event.preventDefault();
                selectTab(tabs[destinations[event.key]], true);
            }
        });
    }
    const heading = (parent, title, label, tone = "neutral") => {
        const row = node("div", "", parent);
        row.classList.add("item-heading");
        node("h3", title, row);
        const badge = node("span", label, row);
        badge.classList.add("badge");
        badge.dataset.tone = tone;
    };
    const empty = (parent, text) => node("p", text, parent).classList.add("empty-state");
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
    let activityRequest = 0;
    const loadActivity = async () => {
        const generation = ++activityRequest;
        const status = $("admin-activity-status"), root = $("admin-activity");
        status.textContent = "A carregar a atividade…";
        status.dataset.state = "loading";
        root.replaceChildren();
        $("admin-activity-reload").disabled = true;
        try {
            const kind = $("admin-activity-kind").value || "all";
            const data = (await request("/api/admin/activity?kind=" + encodeURIComponent(kind))).activity;
            if (generation !== activityRequest) return;
            const labels = {send:"Finalizar ficha", draft:"Guardar rascunho", cancel:"Cancelar ficha"};
            const states = {pending:"Pedido em curso", failed:"Falha no servidor", complete:"Concluída no servidor"};
            const queueStates = {held:"A aguardar confirmação local", pending:"Em fila", running:"Em processamento",
                failed:"Falha no processamento", complete:"Processamento concluído", cancelled:"Trabalho cancelado",
                unavailable:"Resultado externo indisponível"};
            const mailStates = {accepted:"E-mail aceite pelo serviço de envio", rejected:"E-mail recusado pelo serviço de envio",
                unknown:"Resultado do e-mail por confirmar", pending:"E-mail por enviar", not_requested:"Sem pedido de e-mail",
                not_recorded:"Sem registo de envio de e-mail"};
            if (!data.entries.length) empty(root, "Não existem pedidos registados deste tipo.");
            for (const entry of data.entries) {
                const card = node("article", "", root);
                const warning = entry.status === "failed" || ["failed", "unavailable", "held"].includes(entry.queue_status)
                    || ["unknown", "rejected"].includes(entry.mail_status);
                heading(card, entry.sheet_name, queueStates[entry.queue_status] || states[entry.status],
                    warning ? "warning" : entry.status === "complete" && ["none", "complete"].includes(entry.queue_status) ? "good" : "neutral");
                node("p", (labels[entry.kind] || "Operação") + " · " + entry.actor_name, card);
                node("p", "Pedido: " + date(entry.created_at) + " · Estado atualizado: " + date(entry.updated_at), card).classList.add("item-meta");
                if (entry.kind === "send") node("p", mailStates[entry.mail_status] || mailStates.unknown, card);
            }
            status.textContent = data.entries.length + " pedido(s) apresentado(s)." + (data.has_more ? " Existem registos anteriores; são apresentados os 100 mais recentes deste tipo." : "")
                + (data.unavailable_documents ? " Há fichas com histórico indisponível por requererem recuperação." : "");
            status.dataset.state = data.unavailable_documents ? "error" : "ready";
        } catch (error) {
            if (generation !== activityRequest) return;
            status.textContent = "Não foi possível consultar o histórico. " + error.message;
            status.dataset.state = "error";
        } finally {
            if (generation === activityRequest) $("admin-activity-reload").disabled = false;
        }
    };
    $("admin-activity-kind").addEventListener("change", loadActivity);
    $("admin-activity-reload").addEventListener("click", loadActivity);
    const renderDocuments = (documents) => {
        const root = $("admin-documents");
        root.replaceChildren();
        if (!documents.length) empty(root, "Não existem reservas para gerir.");
        for (const doc of documents) {
            const card = node("article", "", root);
            heading(card, doc.name, doc.quarantined ? "Requer recuperação" : doc.pending_operation ? "Operação pendente" :
                doc.owner ? "Reservada" : doc.blocked_users.length ? "Acesso suspenso" : "Sem reserva",
                doc.quarantined || doc.pending_operation || doc.blocked_users?.length ? "warning" : "neutral");
            if (doc.quarantined) {
                node("p", "Esta folha requer recuperação. Os dados guardados foram preservados; a edição e o desbloqueio estão suspensos.", card);
                continue;
            }
            node("p", "Proprietário: " + (doc.owner?.owner_name || "Sem proprietário") + " · Sessões: " + doc.session_count, card);
            node("p", "Última atividade: " + date(doc.last_seen_at) + " · Gravação automática: " + date(doc.autosaved_at), card).classList.add("item-meta");
            const controls = node("div", "", card);
            controls.classList.add("toolbar");
            const release = action(controls, "Expulsar e libertar", "/api/admin/documents/" + encodeURIComponent(doc.document_id) + "/release",
                { lock_version: doc.lock_version }, "Terminar as sessões desta folha e libertar a reserva. Os técnicos expulsos ficam sem acesso de edição nesta folha até à reposição. As gravações guardadas são preservadas; alterações apenas no dispositivo não são transferidas. Peça ao técnico para as guardar antes de intervir.", true);
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
        if (!jobs.length) empty(root, "Não existem trabalhos pendentes ou falhados.");
        for (const job of jobs) {
            const card = node("article", "", root);
            heading(card, kinds[job.kind] || job.kind, statuses[job.status] || job.status,
                job.status === "failed" || job.status === "held" ? "warning" : "neutral");
            node("p", "Operação: " + job.id + " · Tentativas: " + job.attempts, card).classList.add("operation-reference");
            node("p", jobErrors[job.error_code] || (job.will_retry ? "Nova tentativa automática agendada." :
                job.last_error || (phases[job.phase] ? "Último estado registado: " + phases[job.phase] + "." : "A aguardar processamento.")), card);
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
            const controls = node("div", "", card);
            controls.classList.add("toolbar");
            controls.hidden = !job.can_retry && !(job.status === "failed" && job.kind !== "upload_active" && (job.reconciliation_required || job.invalid_state));
            if (job.can_retry) action(controls, "Repetir trabalho", "/api/admin/jobs/" + encodeURIComponent(job.id) + "/retry", {},
                "Repetir apenas esta operação. Se incluir um envio recusado, pode originar um envio de e-mail.");
            if (job.status === "failed" && job.kind !== "upload_active" && (job.reconciliation_required || job.invalid_state)) {
                const review = node("button", "Consultar reconciliação", controls);
                review.type = "button";
                review.addEventListener("click", () => openReconciliation(job.id));
            }
        }
    };
    const openReconciliation = async (id) => {
        $("admin-lookup-status").textContent = "";
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
        } catch (error) {
            $("admin-status").textContent = error.message;
            $("admin-status").dataset.state = "error";
            $("admin-lookup-status").textContent = error.message;
        }
    };
    const renderWorker = (data) => {
        const root = $("admin-worker");
        root.replaceChildren();
        const worker = data.worker || {state: "not_configured"};
        const workerState = $("admin-worker-state");
        const healthy = ["idle", "cycling", "waiting"].includes(worker.state);
        workerState.textContent = healthy ? "Ativo" : worker.state === "starting" ? "A iniciar" :
            worker.state === "not_configured" ? "Sem diagnóstico" : "Verificar estado";
        workerState.dataset.tone = healthy ? "good" : "warning";
        const labels = {not_configured: "Diagnóstico não configurado.", configuration_invalid: "Configuração do diagnóstico inválida.",
            never_seen: "Sem arranque registado neste canal; o worker pode nunca ter arrancado.",
            unavailable: "Diagnóstico indisponível ou inválido.", wrong_storage: "O diagnóstico pertence a outro armazenamento.",
            starting: "Arranque em curso.", cycling: "Ciclo em curso.", idle: "Ativo, entre ciclos.",
            waiting: "Ativo, mas o ciclo ficou adiado.", refused: "Arranque recusado.", failed: "Terminou com erro.",
            stopped: "Processo terminado.", stale: "Sem sinal recente; o worker pode estar parado.",
            version_mismatch: "A versão do worker difere da versão do site.", clock_invalid: "Relógio do diagnóstico incoerente."};
        const codes = {cycle_completed: "Ciclo concluído", worker_busy: "Fila ocupada por outro processador",
            contract_busy: "Contrato temporariamente ocupado", identity_refused: "Identidade do código recusada",
            contract_refused: "Contrato de execução recusado", storage_refused: "Armazenamento recusado ou inacessível",
            worker_error: "Erro do worker", interrupted: "Interrupção do processo"};
        node("p", labels[worker.state] || labels.unavailable, root);
        if (worker.observed_at) node("p", "Observado em " + date(worker.observed_at) + ". Use «Atualizar diagnóstico» para obter um novo estado.", root);
        if (worker.heartbeat_at) node("p", "Último sinal: " + date(worker.heartbeat_at) +
            " · Início do ciclo: " + date(worker.cycle_started_at) + " · Fim do ciclo: " + date(worker.cycle_completed_at), root);
        if (worker.last_result_code) node("p", "Último resultado: " + (codes[worker.last_result_code] || "Indisponível") +
            " · " + date(worker.last_result_at) + ". Um ciclo concluído não confirma entrega de mensagens.", root);
        if (worker.release) node("p", "Versão: " + worker.release.revision + " · SHA256: " + worker.release.source_sha256 +
            (worker.version_mismatch ? " · Diferente do site" : "") +
            (worker.release.verified ? " · Manifesto conferido" : " · Perfil sintético sem manifesto"), root);
        if (data.queue_backlog) {
            const age = data.queue_backlog.oldest_unfinished_age_seconds;
            node("p", age == null ? "Sem idade válida de pendências." :
                "Trabalho não concluído mais antigo: " + Math.floor(age / 60) + " min (inclui retenções e falhas).", root);
            node("p", "Teams por concluir: " + data.queue_backlog.teams_unfinished +
                " · Avisos em falta: " + (data.queue_summary?.teams_missing || 0) +
                " · Conflitos Teams: " + (data.queue_summary?.teams_conflicts || 0), root);
        }
    };
    const load = async () => {
        $("admin-reload").disabled = true;
        $("admin-status").textContent = "A atualizar o diagnóstico…";
        $("admin-status").dataset.state = "loading";
        try {
            const data = await request("/api/admin/overview");
            renderDocuments(data.documents);
            renderJobs(data.jobs);
            renderWorker(data);
            $("admin-count-documents").textContent = data.documents.length;
            // Totals come from the queue summary, never from the capped job list.
            $("admin-count-pending").textContent = data.queue_summary
                ? (data.queue_summary.pending || 0) + (data.queue_summary.running || 0) : "—";
            $("admin-count-failed").textContent = data.queue_summary ? (data.queue_summary.failed || 0) : "—";
            const history = $("admin-audit");
            history.replaceChildren();
            if (!data.audit.length) empty(history, "Ainda não existem intervenções administrativas.");
            for (const entry of data.audit) {
                const card = node("article", "", history);
                node("h3", actions[entry.action] || entry.action, card);
                node("p", date(entry.created_at) + " · " + (entry.actor_label || "Administrador"), card).classList.add("item-meta");
                node("p", entry.target, card).classList.add("operation-reference");
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
            $("admin-status").dataset.state = "ready";
            if (activeTab === "audit") await loadActivity();
        } catch (error) {
            $("admin-status").textContent = "Não foi possível atualizar. A informação apresentada pode estar desatualizada. " + error.message;
            $("admin-status").dataset.state = "error";
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
