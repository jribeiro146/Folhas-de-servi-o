const startEditingCoordinator = () => {
    if (window.__EDITING_COORDINATOR_STARTED__) {
        return true;
    }
    const filesApp = window.__FILES_APP__ || {};
    const editor = window.__FILES_EDITOR__;
    const activeFileName = filesApp.selectedFileName || null;
    const form = document.getElementById("service-form");
    const autosaveStatus = document.getElementById("autosave-status");
    const lockBanner = document.getElementById("editing-lock-banner");
    const lockTitle = document.getElementById("editing-lock-title");
    const lockMessage = document.getElementById("editing-lock-message");
    const retryLeaseButton = document.getElementById("btn-retry-lease");
    const recoveryPanel = document.getElementById("recovery-panel");
    const recoveryEyebrow = document.getElementById("recovery-eyebrow");
    const recoveryTitle = document.getElementById("recovery-title");
    const recoveryMessage = document.getElementById("recovery-message");
    const recoveryTimestamp = document.getElementById("recovery-timestamp");
    const ignoreRecoveryButton = document.getElementById("btn-ignore-recovery");
    const restoreRecoveryButton = document.getElementById("btn-restore-recovery");
    const logoutForm = document.querySelector("[data-logout-form]");

    if (!form || !activeFileName) {
        if (autosaveStatus) {
            autosaveStatus.hidden = true;
        }
        return true;
    }
    if (!editor) {
        if (autosaveStatus) {
            autosaveStatus.textContent = "A carregar o formulário…";
        }
        return false;
    }
    window.__EDITING_COORDINATOR_STARTED__ = true;

    const DB_NAME = "sensorpoint-service-recovery-v1";
    const DB_VERSION = 1;
    const STORE_NAME = "recoveries";
    const LOCAL_SAVE_DELAY = 250;
    const SERVER_SAVE_DELAY = 900;
    const HEARTBEAT_DELAY = 3000;
    const LEASE_RETRY_DELAY = 1000;
    const RECOVERY_READ_TIMEOUT = 1200;
    const recoveryMaxAgeMs = Math.max(Number(filesApp.recoveryMaxAgeDays || 30), 1) * 86400000;
    const editorUser = filesApp.editorUser || { id: "local", display_name: "Técnico local" };

    const makeId = () => {
        if (window.crypto?.randomUUID) {
            return window.crypto.randomUUID();
        }
        return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    };

    let clientId = "";
    try {
        clientId = sessionStorage.getItem("sensorpoint-editor-client-id") || "";
        if (!clientId) {
            clientId = makeId();
            sessionStorage.setItem("sensorpoint-editor-client-id", clientId);
        }
    } catch (error) {
        clientId = makeId();
    }

    let editing = { ...(filesApp.selectedEditingState || {}) };
    let leaseToken = "";
    let dirty = false;
    let hydrating = false;
    let readOnly = true;
    let suppressBeforeUnload = false;
    let localSaveTimer = null;
    let serverSaveTimer = null;
    let heartbeatTimer = null;
    let leaseRetryTimer = null;
    let pendingPanelAction = null;
    let pendingAutosave = null;
    let autosaveInFlight = false;
    let autosaveQueued = false;
    let leaseAcquireInFlight = false;
    const operationKeys = new Map();

    const showToast = (message, variant = "info") => {
        if (typeof editor.showToast === "function") {
            editor.showToast(message, variant);
        }
    };

    const setAutosaveStatus = (message, state = "idle") => {
        if (!autosaveStatus) {
            return;
        }
        autosaveStatus.hidden = false;
        autosaveStatus.textContent = message;
        autosaveStatus.dataset.state = state;
    };

    const openDatabase = () => new Promise((resolve, reject) => {
        if (!("indexedDB" in window)) {
            resolve(null);
            return;
        }
        const request = indexedDB.open(DB_NAME, DB_VERSION);
        request.onupgradeneeded = () => {
            const database = request.result;
            if (!database.objectStoreNames.contains(STORE_NAME)) {
                const store = database.createObjectStore(STORE_NAME, { keyPath: "key" });
                store.createIndex("userId", "userId", { unique: false });
            }
        };
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
    });

    const withStore = async (mode, callback) => {
        const database = await openDatabase();
        if (!database) {
            return null;
        }
        return new Promise((resolve, reject) => {
            const transaction = database.transaction(STORE_NAME, mode);
            const store = transaction.objectStore(STORE_NAME);
            let result;
            try {
                result = callback(store);
            } catch (error) {
                database.close();
                reject(error);
                return;
            }
            transaction.oncomplete = () => {
                database.close();
                resolve(result?.result ?? result ?? null);
            };
            transaction.onerror = () => {
                database.close();
                reject(transaction.error);
            };
        });
    };

    const recoveryKey = () => `${editorUser.id}:${editing.document_id || activeFileName}`;

    const readRecovery = async () => {
        const key = recoveryKey();
        return withStore("readonly", (store) => store.get(key));
    };

    const deleteRecovery = async () => {
        const key = recoveryKey();
        await withStore("readwrite", (store) => store.delete(key));
    };

    const discardServerAutosave = async () => {
        if (!editing.server_document || !leaseToken) {
            return;
        }
        const response = await fetch(
            `/api/file/${encodeURIComponent(activeFileName)}/autosave/discard`,
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ _edit: editMetadata() }),
            },
        );
        const result = await response.json();
        if (!response.ok || !result.success) {
            if (handleConflict(result)) {
                return;
            }
            throw new Error(result.error || "Não foi possível ignorar o autosave.");
        }
        editing = { ...editing, ...(result.editing || {}) };
        leaseToken = result.editing?.lease?.token || leaseToken;
    };

    const clearUserRecoveries = async () => {
        await withStore("readwrite", (store) => {
            const index = store.index("userId");
            const cursorRequest = index.openCursor(IDBKeyRange.only(editorUser.id));
            cursorRequest.onsuccess = () => {
                const cursor = cursorRequest.result;
                if (!cursor) {
                    return;
                }
                cursor.delete();
                cursor.continue();
            };
            return cursorRequest;
        });
    };

    const pruneExpiredRecoveries = async () => {
        const threshold = Date.now() - recoveryMaxAgeMs;
        await withStore("readwrite", (store) => {
            const cursorRequest = store.openCursor();
            cursorRequest.onsuccess = () => {
                const cursor = cursorRequest.result;
                if (!cursor) {
                    return;
                }
                if (Number(cursor.value?.updatedAt || 0) < threshold) {
                    cursor.delete();
                }
                cursor.continue();
            };
            return cursorRequest;
        });
    };

    const payloadHasSignatures = (payload) => Boolean(
        payload?.["Assinatura Cliente"] || payload?.["Assinatura Técnico"]
    );

    const persistLocalRecovery = async ({ announce = false, force = false } = {}) => {
        if ((!dirty && !force) || !editing.document_id) {
            return null;
        }
        try {
            const payload = editor.collectFormData();
            const serialized = JSON.stringify(payload);
            const record = {
                key: recoveryKey(),
                userId: editorUser.id,
                documentId: editing.document_id,
                fileName: activeFileName,
                baseRevision: Number(editing.revision || 1),
                payload,
                updatedAt: Date.now(),
                expiresAt: Date.now() + recoveryMaxAgeMs,
            };
            await withStore("readwrite", (store) => store.put(record));
            if (
                announce
                && dirty
                && !autosaveInFlight
                && JSON.stringify(editor.collectFormData()) === serialized
            ) {
                setAutosaveStatus("Guardado neste dispositivo — a sincronizar…", "saving");
            }
            return record;
        } catch (error) {
            setAutosaveStatus("Não foi possível guardar neste dispositivo", "conflict");
            return null;
        }
    };

    const setFormReadOnly = (value, ownerName = "") => {
        readOnly = Boolean(value);
        form.classList.toggle("is-readonly", readOnly);
        form.querySelectorAll("input, select, textarea, button").forEach((control) => {
            if (!control.dataset.editingInitialDisabled) {
                control.dataset.editingInitialDisabled = control.disabled ? "true" : "false";
            }
            control.disabled = readOnly || control.dataset.editingInitialDisabled === "true";
        });
        if (lockBanner) {
            lockBanner.hidden = !readOnly;
        }
        if (readOnly) {
            const owner = ownerName || editing.lease?.owner_name || "outro utilizador";
            if (lockTitle) {
                lockTitle.textContent = "Folha em modo de consulta";
            }
            if (lockMessage) {
                lockMessage.textContent = `Em edição por ${owner}. A edição será retomada automaticamente assim que ficar livre.`;
            }
        }
    };

    const setCommitActionsEnabled = (enabled) => {
        ["btn-save-draft", "btn-save-send", "btn-cancel-file"].forEach((id) => {
            const control = document.getElementById(id);
            if (control) {
                control.disabled = !enabled;
            }
        });
    };

    const editMetadata = (idempotencyKey = "") => ({
        document_id: editing.document_id || "",
        client_id: clientId,
        lease_token: leaseToken,
        base_revision: Number(editing.revision || 1),
        idempotency_key: idempotencyKey,
    });

    const handleConflict = (result) => {
        if (!result || !["revision_conflict", "graph_conflict"].includes(result.code)) {
            return false;
        }
        editing = { ...editing, ...(result.editing || {}) };
        pendingAutosave = null;
        setAutosaveStatus("Conflito — é necessária uma decisão", "conflict");
        pendingPanelAction = {
            kind: "conflict",
            localPayload: editor.collectFormData(),
            serverPayload: result.editing?.server_document || null,
        };
        recoveryPanel.classList.add("is-conflict");
        recoveryPanel.hidden = false;
        recoveryEyebrow.textContent = "Conflito de edição";
        recoveryTitle.textContent = "A folha foi alterada noutro local";
        recoveryMessage.textContent = "Mantivemos a sua versão neste dispositivo. Escolha qual deve continuar.";
        recoveryTimestamp.textContent = "Nenhuma versão foi sobrescrita automaticamente.";
        ignoreRecoveryButton.textContent = "Usar servidor";
        restoreRecoveryButton.textContent = "Manter a minha cópia";
        recoveryPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
        return true;
    };

    const runAutosave = async ({ keepalive = false } = {}) => {
        if (autosaveInFlight) {
            autosaveQueued = true;
            return;
        }
        if (!dirty || readOnly || !leaseToken || !editing.document_id) {
            return;
        }
        if (!navigator.onLine) {
            setAutosaveStatus("Sem ligação — guardado neste dispositivo", "offline");
            return;
        }

        autosaveInFlight = true;
        autosaveQueued = false;
        const payload = editor.collectFormData();
        const serialized = JSON.stringify(payload);
        if (!pendingAutosave || pendingAutosave.serialized !== serialized) {
            pendingAutosave = {
                key: makeId(),
                serialized,
                baseRevision: Number(editing.revision || 1),
            };
        }
        const requestState = { ...pendingAutosave };
        setAutosaveStatus("A sincronizar com o servidor…", "saving");
        try {
            const response = await fetch(`/api/file/${encodeURIComponent(activeFileName)}/autosave`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    document: payload,
                    _edit: {
                        ...editMetadata(requestState.key),
                        base_revision: requestState.baseRevision,
                    },
                }),
                keepalive,
            });
            const result = await response.json();
            if (!response.ok || !result.success) {
                if (handleConflict(result)) {
                    return;
                }
                if ([401, 423].includes(response.status)) {
                    setFormReadOnly(true, result.editing?.lease?.owner_name);
                }
                throw new Error(result.error || "Falha no autosave.");
            }
            editing = { ...editing, ...(result.editing || {}) };
            leaseToken = result.editing?.lease?.token || leaseToken;
            if (pendingAutosave?.key === requestState.key) {
                pendingAutosave = null;
            }
            setCommitActionsEnabled(true);
            const currentPayload = editor.collectFormData();
            const currentSerialized = JSON.stringify(currentPayload);
            if (currentSerialized === requestState.serialized) {
                dirty = false;
                window.clearTimeout(localSaveTimer);
                window.clearTimeout(serverSaveTimer);
                if (payloadHasSignatures(currentPayload)) {
                    persistLocalRecovery({ force: true });
                } else {
                    deleteRecovery().catch(() => {});
                }
                const savedTime = new Date().toLocaleTimeString("pt-PT", {
                    hour: "2-digit",
                    minute: "2-digit",
                });
                setAutosaveStatus(`Sincronizado às ${savedTime}`, "saved");
            } else {
                dirty = true;
                await persistLocalRecovery();
                autosaveQueued = true;
                setAutosaveStatus("Novas alterações por sincronizar…", "saving");
            }
        } catch (error) {
            if (!navigator.onLine || error instanceof TypeError) {
                setCommitActionsEnabled(false);
                setAutosaveStatus("Sem ligação — alterações guardadas neste dispositivo", "offline");
                return;
            }
            setAutosaveStatus("Não foi possível sincronizar — cópia local mantida", "conflict");
        } finally {
            autosaveInFlight = false;
            if (autosaveQueued && dirty && navigator.onLine && !readOnly) {
                autosaveQueued = false;
                window.clearTimeout(serverSaveTimer);
                serverSaveTimer = window.setTimeout(() => runAutosave(), 150);
            }
        }
    };

    const schedulePersistence = () => {
        window.clearTimeout(localSaveTimer);
        window.clearTimeout(serverSaveTimer);
        localSaveTimer = window.setTimeout(
            () => persistLocalRecovery({ announce: true }), LOCAL_SAVE_DELAY
        );
        serverSaveTimer = window.setTimeout(() => runAutosave(), SERVER_SAVE_DELAY);
    };

    const markDirty = () => {
        if (hydrating || readOnly) {
            return;
        }
        dirty = true;
        setAutosaveStatus("A guardar neste dispositivo…", "saving");
        schedulePersistence();
    };

    const showRecovery = (record, source = "device") => {
        if (!record?.payload) {
            return;
        }
        pendingPanelAction = { kind: "recovery", record, source };
        recoveryPanel.classList.remove("is-conflict");
        recoveryPanel.hidden = false;
        recoveryEyebrow.textContent = "Recuperação automática";
        recoveryTitle.textContent = "Encontrámos alterações recuperáveis";
        recoveryMessage.textContent = source === "server"
            ? "A versão sincronizada no servidor é mais recente do que a folha consolidada no Excel."
            : "Existem alterações neste dispositivo que ainda não chegaram ao servidor.";
        const timestamp = Number(record.updatedAt || 0);
        recoveryTimestamp.textContent = timestamp
            ? `Última alteração: ${new Date(timestamp).toLocaleString("pt-PT")}`
            : "";
        ignoreRecoveryButton.textContent = "Ignorar";
        restoreRecoveryButton.textContent = "Restaurar";
    };

    const showStartupRecoveryConflict = (localRecord, serverRecord) => {
        pendingPanelAction = {
            kind: "startup-conflict",
            localRecord,
            serverRecord,
        };
        recoveryPanel.classList.add("is-conflict");
        recoveryPanel.hidden = false;
        recoveryEyebrow.textContent = "Duas versões recuperáveis";
        recoveryTitle.textContent = "Escolha a versão que pretende repor";
        recoveryMessage.textContent = "A cópia deste dispositivo é diferente da versão sincronizada no servidor.";
        const localTime = Number(localRecord.updatedAt || 0);
        const serverTime = Number(serverRecord.updatedAt || 0);
        const localLabel = localTime ? new Date(localTime).toLocaleString("pt-PT") : "hora desconhecida";
        const serverLabel = serverTime ? new Date(serverTime).toLocaleString("pt-PT") : "hora desconhecida";
        recoveryTimestamp.textContent = `Dispositivo: ${localLabel} · Servidor: ${serverLabel}`;
        ignoreRecoveryButton.textContent = "Usar servidor";
        restoreRecoveryButton.textContent = "Usar este dispositivo";
    };

    const showNavigationGuard = (kind, callback) => {
        persistLocalRecovery();
        pendingPanelAction = { kind, callback };
        recoveryPanel.classList.remove("is-conflict");
        recoveryPanel.hidden = false;
        recoveryEyebrow.textContent = "Alterações pendentes";
        recoveryTitle.textContent = kind === "logout"
            ? "Sair e limpar a recuperação local?"
            : "Continuar sem consolidar a folha?";
        recoveryMessage.textContent = kind === "logout"
            ? "Ao sair, as cópias deste utilizador serão removidas deste dispositivo partilhado."
            : "A cópia de recuperação fica guardada e será apresentada quando voltar a abrir esta folha.";
        recoveryTimestamp.textContent = "";
        ignoreRecoveryButton.textContent = "Ficar";
        restoreRecoveryButton.textContent = kind === "logout" ? "Sair" : "Continuar";
        recoveryPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    };

    const hideRecoveryPanel = () => {
        pendingPanelAction = null;
        recoveryPanel.hidden = true;
        recoveryPanel.classList.remove("is-conflict");
    };

    const setRecoveryActionsDisabled = (disabled) => {
        if (ignoreRecoveryButton) {
            ignoreRecoveryButton.disabled = disabled;
        }
        if (restoreRecoveryButton) {
            restoreRecoveryButton.disabled = disabled;
        }
    };

    const signaturesFromPayload = (payload) => ({
        "Assinatura Cliente": payload?.["Assinatura Cliente"] || "",
        "Assinatura Técnico": payload?.["Assinatura Técnico"] || "",
    });

    const applyRecovery = async (record, source) => {
        if (!record?.payload) {
            return;
        }
        setRecoveryActionsDisabled(true);
        setAutosaveStatus("A repor alterações…", "saving");
        try {
            hydrating = true;
            editor.populateForm(
                activeFileName,
                record.payload,
                signaturesFromPayload(record.payload),
            );
            await new Promise((resolve) => window.requestAnimationFrame(resolve));
            hydrating = false;
            hideRecoveryPanel();

            if (["server", "synced-device"].includes(source)) {
                dirty = false;
                window.clearTimeout(localSaveTimer);
                window.clearTimeout(serverSaveTimer);
                if (source === "server") {
                    deleteRecovery().catch(() => {});
                }
                setAutosaveStatus(
                    readOnly || !leaseToken ? "Alterações recuperadas — modo de consulta" : "Alterações recuperadas — sincronizadas",
                    readOnly || !leaseToken ? "offline" : "saved",
                );
                showToast("Alterações recuperadas automaticamente.", "success");
                return;
            }

            dirty = true;
            await persistLocalRecovery();
            if (readOnly || !leaseToken) {
                setAutosaveStatus("Alterações recuperadas — aguardam edição", "offline");
                showToast("Alterações recuperadas automaticamente.", "success");
                return;
            }
            setAutosaveStatus("Alterações repostas — a sincronizar…", "saving");
            showToast("Alterações deste dispositivo repostas.", "success");
            await runAutosave();
        } finally {
            hydrating = false;
            setRecoveryActionsDisabled(false);
        }
    };

    const clearLeaseRetry = () => {
        window.clearTimeout(leaseRetryTimer);
        leaseRetryTimer = null;
    };

    const releaseLease = async ({ background = false } = {}) => {
        if (!leaseToken || !editing.document_id) {
            return;
        }
        const tokenToRelease = leaseToken;
        leaseToken = "";
        window.clearInterval(heartbeatTimer);
        clearLeaseRetry();

        if (background) {
            const closeDocument = dirty ? {
                ...editor.collectFormData(),
                "Assinatura Cliente": "",
                "Assinatura Técnico": "",
            } : null;
            const closePayload = JSON.stringify({
                document: closeDocument,
                _edit: {
                    document_id: editing.document_id,
                    client_id: clientId,
                    lease_token: tokenToRelease,
                    base_revision: Number(editing.revision || 1),
                    idempotency_key: makeId(),
                },
            });
            const closeUrl = `/api/file/${encodeURIComponent(activeFileName)}/editing/close`;
            if (navigator.sendBeacon) {
                const queued = navigator.sendBeacon(
                    closeUrl,
                    new Blob([closePayload], { type: "application/json" }),
                );
                if (queued) {
                    return;
                }
            }
            try {
                await fetch(closeUrl, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: closePayload,
                    keepalive: true,
                });
            } catch (error) {
                // A reserva curta continua a garantir a libertação de emergência.
            }
            return;
        }

        try {
            await fetch(`/api/file/${encodeURIComponent(activeFileName)}/lease/release`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    document_id: editing.document_id,
                    client_id: clientId,
                    lease_token: tokenToRelease,
                }),
                keepalive: true,
            });
        } catch (error) {
            // A reserva curta continua a garantir a libertação de emergência.
        }
    };

    const scheduleLeaseRetry = () => {
        if (leaseToken || !navigator.onLine) {
            return;
        }
        clearLeaseRetry();
        leaseRetryTimer = window.setTimeout(
            () => acquireLease({ quiet: true }),
            LEASE_RETRY_DELAY,
        );
    };

    const startHeartbeat = () => {
        window.clearInterval(heartbeatTimer);
        heartbeatTimer = window.setInterval(async () => {
            if (!leaseToken || document.hidden) {
                return;
            }
            const heartbeatToken = leaseToken;
            try {
                const response = await fetch(
                    `/api/file/${encodeURIComponent(activeFileName)}/lease/heartbeat`,
                    {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            document_id: editing.document_id,
                            client_id: clientId,
                            lease_token: heartbeatToken,
                        }),
                    },
                );
                const result = await response.json();
                if (!response.ok || !result.success) {
                    leaseToken = "";
                    editing = { ...editing, ...(result.editing || {}) };
                    setFormReadOnly(true, result.editing?.lease?.owner_name);
                    setAutosaveStatus("Em consulta — retoma automática", "offline");
                    scheduleLeaseRetry();
                    return;
                }
                editing = { ...editing, ...(result.editing || {}) };
                leaseToken = result.editing?.lease?.token || heartbeatToken;
                setCommitActionsEnabled(true);
                if (autosaveStatus?.dataset.state === "offline") {
                    if (dirty) {
                        runAutosave();
                    } else {
                        setAutosaveStatus("Ligação restabelecida — edição disponível", "saved");
                    }
                }
            } catch (error) {
                setCommitActionsEnabled(false);
                setAutosaveStatus("Sem ligação — guardado neste dispositivo", "offline");
            }
        }, HEARTBEAT_DELAY);
    };

    const acquireLease = async ({ quiet = false } = {}) => {
        if (leaseAcquireInFlight) {
            return false;
        }
        leaseAcquireInFlight = true;
        setFormReadOnly(true);
        if (!quiet) {
            setAutosaveStatus("A preparar edição…", "saving");
        }
        try {
            const response = await fetch(`/api/file/${encodeURIComponent(activeFileName)}/lease`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ client_id: clientId }),
            });
            const result = await response.json();
            if (!response.ok || !result.success) {
                editing = { ...editing, ...(result.editing || {}) };
                leaseToken = "";
                setFormReadOnly(true, result.editing?.lease?.owner_name);
                setAutosaveStatus("Em consulta — retoma automática", "offline");
                scheduleLeaseRetry();
                return false;
            }
            editing = { ...editing, ...(result.editing || {}) };
            leaseToken = result.editing?.lease?.token || "";
            clearLeaseRetry();
            setFormReadOnly(false);
            setCommitActionsEnabled(true);
            setAutosaveStatus("Edição disponível", "saved");
            startHeartbeat();
            if (dirty) {
                window.setTimeout(() => runAutosave(), 0);
            }
            return true;
        } catch (error) {
            leaseToken = "";
            setFormReadOnly(false);
            setCommitActionsEnabled(false);
            setAutosaveStatus("Sem ligação — edição guardada neste dispositivo", "offline");
            return false;
        } finally {
            leaseAcquireInFlight = false;
        }
    };

    ignoreRecoveryButton?.addEventListener("click", async () => {
        const action = pendingPanelAction;
        if (!action) {
            hideRecoveryPanel();
            return;
        }
        if (action.kind === "startup-conflict") {
            await applyRecovery(action.serverRecord, "server");
            return;
        }
        if (action.kind === "recovery") {
            setRecoveryActionsDisabled(true);
            try {
                await deleteRecovery();
                await discardServerAutosave();
                hideRecoveryPanel();
                setAutosaveStatus("Autosave ignorado", "saved");
            } catch (error) {
                showToast(error.message, "error");
            } finally {
                setRecoveryActionsDisabled(false);
            }
            return;
        }
        if (action.kind === "conflict") {
            await deleteRecovery();
            if (action.serverPayload) {
                hydrating = true;
                editor.populateForm(activeFileName, action.serverPayload, {});
                hydrating = false;
            } else {
                suppressBeforeUnload = true;
                window.location.reload();
                return;
            }
            dirty = false;
            hideRecoveryPanel();
            setAutosaveStatus("Versão do servidor carregada", "saved");
            return;
        }
        hideRecoveryPanel();
    });

    restoreRecoveryButton?.addEventListener("click", async () => {
        const action = pendingPanelAction;
        if (!action) {
            hideRecoveryPanel();
            return;
        }
        if (action.kind === "startup-conflict") {
            await applyRecovery(action.localRecord, "device");
            return;
        }
        if (action.kind === "recovery") {
            await applyRecovery(action.record, action.source);
            return;
        }
        if (action.kind === "conflict") {
            editing = { ...editing, revision: Number(editing.revision || 1) };
            dirty = true;
            hideRecoveryPanel();
            schedulePersistence();
            showToast("A sua cópia foi mantida e será sincronizada como nova revisão.", "info");
            return;
        }
        if (["navigate", "reload", "logout"].includes(action.kind)) {
            if (action.kind === "logout") {
                await clearUserRecoveries();
            } else {
                await persistLocalRecovery();
            }
            await releaseLease();
            suppressBeforeUnload = true;
            const callback = action.callback;
            hideRecoveryPanel();
            callback?.();
        }
    });

    retryLeaseButton?.addEventListener("click", () => {
        clearLeaseRetry();
        acquireLease({ quiet: false });
    });

    form.addEventListener("input", markDirty, true);
    form.addEventListener("change", markDirty, true);
    form.addEventListener("pointerup", (event) => {
        if (event.target.closest?.(".signature-canvas")) {
            window.setTimeout(markDirty, 0);
        }
    }, true);
    form.addEventListener("click", (event) => {
        if (event.target.closest?.("[data-row-remove], #btn-add-material, #btn-add-technician, [data-duration-reset], [data-signature-clear]")) {
            window.setTimeout(markDirty, 0);
        }
    }, true);

    const leavePage = async (callback) => {
        if (dirty) {
            await persistLocalRecovery();
            await runAutosave({ keepalive: true });
        }
        await releaseLease();
        suppressBeforeUnload = true;
        callback?.();
    };

    document.addEventListener("click", (event) => {
        const link = event.target.closest?.(".file-card-link[data-name]");
        if (link && link.dataset.name !== activeFileName) {
            event.preventDefault();
            event.stopImmediatePropagation();
            const navigate = () => {
                window.location.href = link.href;
            };
            leavePage(navigate);
            return;
        }

        const reloadButton = event.target.closest?.("#btn-refresh, #btn-cancel-edit");
        if (!reloadButton) {
            return;
        }
        event.preventDefault();
        event.stopImmediatePropagation();
        const reload = () => window.location.reload();
        leavePage(reload);
    }, true);

    logoutForm?.addEventListener("submit", (event) => {
        if (suppressBeforeUnload) {
            return;
        }
        event.preventDefault();
        const submitLogout = () => logoutForm.submit();
        if (dirty) {
            showNavigationGuard("logout", submitLogout);
            return;
        }
        clearUserRecoveries()
            .then(() => releaseLease())
            .finally(() => {
                suppressBeforeUnload = true;
                submitLogout();
            });
    });

    const releaseOnPageExit = () => {
        window.clearTimeout(localSaveTimer);
        window.clearTimeout(serverSaveTimer);
        window.clearInterval(heartbeatTimer);
        clearLeaseRetry();
        if (dirty) {
            persistLocalRecovery();
        }
        releaseLease({ background: true });
    };

    window.addEventListener("pagehide", releaseOnPageExit);
    window.addEventListener("beforeunload", releaseOnPageExit);
    document.addEventListener("freeze", releaseOnPageExit);

    document.addEventListener("visibilitychange", async () => {
        if (document.hidden) {
            if (dirty) {
                persistLocalRecovery();
            }
            setFormReadOnly(true);
            releaseLease({ background: true });
            return;
        }
        const acquired = await acquireLease({ quiet: false });
        if (acquired && dirty) {
            runAutosave();
        }
    });

    window.addEventListener("offline", () => {
        setCommitActionsEnabled(false);
        if (dirty) {
            setAutosaveStatus("Sem ligação — guardado neste dispositivo", "offline");
        }
    });
    window.addEventListener("online", async () => {
        const acquired = await acquireLease({ quiet: false });
        if (acquired && dirty) {
            runAutosave();
        }
    });

    window.__EDITING_COORDINATOR__ = {
        operationMetadata(kind) {
            if (!operationKeys.has(kind)) {
                operationKeys.set(kind, makeId());
            }
            return editMetadata(operationKeys.get(kind));
        },
        async markCommitted(result, kind) {
            editing = { ...editing, ...result };
            operationKeys.delete(kind);
            dirty = false;
            pendingAutosave = null;
            window.clearTimeout(localSaveTimer);
            window.clearTimeout(serverSaveTimer);
            await deleteRecovery();
            setAutosaveStatus("Alterações consolidadas", "saved");
            if (["send", "cancel"].includes(kind)) {
                leaseToken = "";
            }
        },
        handleConflict,
        markDirty,
        isDirty: () => dirty,
    };

    const comparableRecovery = (payload) => {
        const withoutSignatures = { ...(payload || {}) };
        delete withoutSignatures["Assinatura Cliente"];
        delete withoutSignatures["Assinatura Técnico"];
        const sortValue = (value) => {
            if (Array.isArray(value)) {
                return value.map(sortValue);
            }
            if (value && typeof value === "object") {
                return Object.keys(value).sort().reduce((sorted, key) => {
                    sorted[key] = sortValue(value[key]);
                    return sorted;
                }, {});
            }
            return value;
        };
        return JSON.stringify(sortValue(withoutSignatures));
    };

    const readRecoveryWithoutBlocking = async () => {
        try {
            return await Promise.race([
                readRecovery(),
                new Promise((resolve) => window.setTimeout(
                    () => resolve(null), RECOVERY_READ_TIMEOUT
                )),
            ]);
        } catch (error) {
            return null;
        }
    };

    const initialize = async () => {
        await acquireLease();
        const localRecovery = await readRecoveryWithoutBlocking();
        pruneExpiredRecoveries().catch(() => {});
        const serverRecovery = editing.server_document ? {
            payload: editing.server_document,
            updatedAt: Number(editing.autosaved_at || 0) * 1000,
            baseRevision: Number(editing.revision || 1),
        } : null;

        if (localRecovery?.payload && serverRecovery?.payload) {
            const localComparable = comparableRecovery(localRecovery.payload);
            const serverComparable = comparableRecovery(serverRecovery.payload);
            if (localComparable === serverComparable) {
                const source = payloadHasSignatures(localRecovery.payload)
                    ? "synced-device"
                    : "server";
                const record = source === "synced-device" ? localRecovery : serverRecovery;
                await applyRecovery(record, source);
                return;
            }
            if (Number(localRecovery.baseRevision || 1) < Number(serverRecovery.baseRevision || 1)) {
                showStartupRecoveryConflict(localRecovery, serverRecovery);
                return;
            }
            const localIsNewer = Number(localRecovery.updatedAt || 0) >= Number(serverRecovery.updatedAt || 0);
            await applyRecovery(
                localIsNewer ? localRecovery : serverRecovery,
                localIsNewer ? "device" : "server",
            );
            return;
        }
        if (localRecovery?.payload) {
            await applyRecovery(localRecovery, "device");
            return;
        }
        if (serverRecovery?.payload) {
            await applyRecovery(serverRecovery, "server");
        }
    };

    initialize().catch(() => {
        setFormReadOnly(false);
        setCommitActionsEnabled(false);
        setAutosaveStatus("Edição local disponível — sincronização pendente", "offline");
    });
    return true;
};

const bootEditingCoordinator = () => {
    if (startEditingCoordinator()) {
        return;
    }
    const status = document.getElementById("autosave-status");
    let attempts = 0;
    let retryTimer = null;
    const retry = () => {
        attempts += 1;
        if (startEditingCoordinator()) {
            window.clearInterval(retryTimer);
            document.removeEventListener("files-editor-ready", retry);
            return;
        }
        if (attempts >= 40) {
            window.clearInterval(retryTimer);
            if (status) {
                status.hidden = false;
                status.textContent = "Não foi possível iniciar a edição";
                status.dataset.state = "conflict";
            }
        }
    };
    document.addEventListener("files-editor-ready", retry);
    retryTimer = window.setInterval(retry, 100);
};

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootEditingCoordinator, { once: true });
} else {
    bootEditingCoordinator();
}
