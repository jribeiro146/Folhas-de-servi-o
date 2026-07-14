document.addEventListener("DOMContentLoaded", () => {
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

    if (!form || !editor || !activeFileName) {
        if (autosaveStatus) {
            autosaveStatus.hidden = true;
        }
        return;
    }

    const DB_NAME = "sensorpoint-service-recovery-v1";
    const DB_VERSION = 1;
    const STORE_NAME = "recoveries";
    const LOCAL_SAVE_DELAY = 350;
    const SERVER_SAVE_DELAY = 1600;
    const HEARTBEAT_DELAY = 40000;
    const recoveryMaxAgeMs = Math.max(Number(filesApp.recoveryMaxAgeDays || 30), 1) * 86400000;
    const editorUser = filesApp.editorUser || { id: "local", display_name: "Técnico local" };

    const makeId = () => {
        if (window.crypto?.randomUUID) {
            return window.crypto.randomUUID();
        }
        return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    };

    let clientId = sessionStorage.getItem("sensorpoint-editor-client-id");
    if (!clientId) {
        clientId = makeId();
        sessionStorage.setItem("sensorpoint-editor-client-id", clientId);
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
    let pendingPanelAction = null;
    let pendingAutosave = null;
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

    const persistLocalRecovery = async () => {
        if (!dirty || !editing.document_id) {
            return;
        }
        try {
            const payload = editor.collectFormData();
            await withStore("readwrite", (store) => store.put({
                key: recoveryKey(),
                userId: editorUser.id,
                documentId: editing.document_id,
                fileName: activeFileName,
                baseRevision: Number(editing.revision || 1),
                payload,
                updatedAt: Date.now(),
                expiresAt: Date.now() + recoveryMaxAgeMs,
            }));
        } catch (error) {
            setAutosaveStatus("Não foi possível guardar neste dispositivo", "conflict");
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
                lockMessage.textContent = `Em edição por ${owner}. A reserva pode ser retomada quando expirar.`;
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
        if (!dirty || readOnly || !leaseToken || !editing.document_id) {
            return;
        }
        if (!navigator.onLine) {
            setAutosaveStatus("Sem ligação — guardado neste dispositivo", "offline");
            return;
        }

        const payload = editor.collectFormData();
        const serialized = JSON.stringify(payload);
        if (!pendingAutosave || pendingAutosave.serialized !== serialized) {
            pendingAutosave = {
                key: makeId(),
                serialized,
                baseRevision: Number(editing.revision || 1),
            };
        }
        setAutosaveStatus("A guardar…", "saving");
        try {
            const response = await fetch(`/api/file/${encodeURIComponent(activeFileName)}/autosave`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    document: payload,
                    _edit: {
                        ...editMetadata(pendingAutosave.key),
                        base_revision: pendingAutosave.baseRevision,
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
            pendingAutosave = null;
            setCommitActionsEnabled(true);
            const savedTime = new Date().toLocaleTimeString("pt-PT", {
                hour: "2-digit",
                minute: "2-digit",
            });
            setAutosaveStatus(`Guardado às ${savedTime}`, "saved");
            await persistLocalRecovery();
        } catch (error) {
            if (!navigator.onLine || error instanceof TypeError) {
                setCommitActionsEnabled(false);
                setAutosaveStatus("Sem ligação — guardado neste dispositivo", "offline");
                return;
            }
            setAutosaveStatus("Não foi possível sincronizar — cópia local mantida", "conflict");
        }
    };

    const schedulePersistence = () => {
        window.clearTimeout(localSaveTimer);
        window.clearTimeout(serverSaveTimer);
        localSaveTimer = window.setTimeout(() => persistLocalRecovery(), LOCAL_SAVE_DELAY);
        serverSaveTimer = window.setTimeout(() => runAutosave(), SERVER_SAVE_DELAY);
    };

    const markDirty = () => {
        if (hydrating || readOnly) {
            return;
        }
        dirty = true;
        setAutosaveStatus("Alterações por guardar…", "saving");
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
            ? "Existe um autosave no servidor que ainda não foi consolidado no Excel."
            : "Existe uma cópia neste dispositivo que ainda não foi consolidada.";
        const timestamp = Number(record.updatedAt || 0);
        recoveryTimestamp.textContent = timestamp
            ? `Última alteração: ${new Date(timestamp).toLocaleString("pt-PT")}`
            : "";
        ignoreRecoveryButton.textContent = "Ignorar";
        restoreRecoveryButton.textContent = "Restaurar";
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

    const releaseLease = async () => {
        if (!leaseToken || !editing.document_id) {
            return;
        }
        try {
            await fetch(`/api/file/${encodeURIComponent(activeFileName)}/lease/release`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    document_id: editing.document_id,
                    client_id: clientId,
                    lease_token: leaseToken,
                }),
                keepalive: true,
            });
        } catch (error) {
            // A lease expira automaticamente se a libertação não chegar ao servidor.
        }
        leaseToken = "";
    };

    const startHeartbeat = () => {
        window.clearInterval(heartbeatTimer);
        heartbeatTimer = window.setInterval(async () => {
            if (!leaseToken || document.hidden) {
                return;
            }
            try {
                const response = await fetch(
                    `/api/file/${encodeURIComponent(activeFileName)}/lease/heartbeat`,
                    {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            document_id: editing.document_id,
                            client_id: clientId,
                            lease_token: leaseToken,
                        }),
                    },
                );
                const result = await response.json();
                if (!response.ok || !result.success) {
                    setFormReadOnly(true, result.editing?.lease?.owner_name);
                    return;
                }
                editing = { ...editing, ...(result.editing || {}) };
                leaseToken = result.editing?.lease?.token || leaseToken;
                setCommitActionsEnabled(true);
            } catch (error) {
                setCommitActionsEnabled(false);
                setAutosaveStatus("Sem ligação — guardado neste dispositivo", "offline");
            }
        }, HEARTBEAT_DELAY);
    };

    const acquireLease = async () => {
        setFormReadOnly(true);
        setAutosaveStatus("A obter reserva de edição…", "saving");
        try {
            const response = await fetch(`/api/file/${encodeURIComponent(activeFileName)}/lease`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ client_id: clientId }),
            });
            const result = await response.json();
            if (!response.ok || !result.success) {
                editing = { ...editing, ...(result.editing || {}) };
                setFormReadOnly(true, result.editing?.lease?.owner_name);
                setAutosaveStatus("Modo de consulta — folha reservada", "offline");
                return false;
            }
            editing = { ...editing, ...(result.editing || {}) };
            leaseToken = result.editing?.lease?.token || "";
            setFormReadOnly(false);
            setCommitActionsEnabled(true);
            setAutosaveStatus("Edição protegida", "saved");
            startHeartbeat();
            return true;
        } catch (error) {
            leaseToken = "";
            setFormReadOnly(false);
            setCommitActionsEnabled(false);
            setAutosaveStatus("Sem ligação — edição guardada neste dispositivo", "offline");
            return false;
        }
    };

    ignoreRecoveryButton?.addEventListener("click", async () => {
        const action = pendingPanelAction;
        if (!action) {
            hideRecoveryPanel();
            return;
        }
        if (action.kind === "recovery") {
            await deleteRecovery();
            try {
                await discardServerAutosave();
            } catch (error) {
                showToast(error.message, "error");
                return;
            }
            hideRecoveryPanel();
            setAutosaveStatus("Autosave ignorado", "saved");
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
        if (action.kind === "recovery") {
            const payload = action.record.payload;
            hydrating = true;
            editor.populateForm(activeFileName, payload, {
                "Assinatura Cliente": payload["Assinatura Cliente"] || "",
            });
            hydrating = false;
            dirty = true;
            hideRecoveryPanel();
            schedulePersistence();
            showToast("Alterações recuperadas neste dispositivo.", "success");
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

    retryLeaseButton?.addEventListener("click", () => acquireLease());

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

    document.addEventListener("click", (event) => {
        const link = event.target.closest?.(".file-card-link[data-name]");
        if (link && link.dataset.name !== activeFileName) {
            event.preventDefault();
            event.stopImmediatePropagation();
            const navigate = () => {
                window.location.href = link.href;
            };
            if (dirty) {
                showNavigationGuard("navigate", navigate);
            } else {
                releaseLease().finally(() => {
                    suppressBeforeUnload = true;
                    navigate();
                });
            }
            return;
        }

        const reloadButton = event.target.closest?.("#btn-refresh, #btn-cancel-edit");
        if (!reloadButton) {
            return;
        }
        event.preventDefault();
        event.stopImmediatePropagation();
        const reload = () => window.location.reload();
        if (dirty) {
            showNavigationGuard("reload", reload);
        } else {
            suppressBeforeUnload = true;
            reload();
        }
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

    window.addEventListener("beforeunload", (event) => {
        if (!dirty || suppressBeforeUnload) {
            return;
        }
        event.preventDefault();
        event.returnValue = "";
    });

    document.addEventListener("visibilitychange", () => {
        if (document.hidden && dirty) {
            persistLocalRecovery();
            runAutosave({ keepalive: true });
        }
    });

    window.addEventListener("offline", () => {
        setCommitActionsEnabled(false);
        if (dirty) {
            setAutosaveStatus("Sem ligação — guardado neste dispositivo", "offline");
        }
    });
    window.addEventListener("online", async () => {
        const acquired = await acquireLease();
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

    const initialize = async () => {
        await pruneExpiredRecoveries();
        await acquireLease();
        const localRecovery = await readRecovery();
        if (localRecovery?.payload) {
            showRecovery(localRecovery, "device");
            return;
        }
        if (editing.server_document) {
            showRecovery({
                payload: editing.server_document,
                updatedAt: Number(editing.autosaved_at || 0) * 1000,
            }, "server");
        }
    };

    initialize();
});
