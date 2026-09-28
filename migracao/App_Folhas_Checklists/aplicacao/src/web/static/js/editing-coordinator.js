const startEditingCoordinator = () => {
    if (window.__EDITING_COORDINATOR_STARTED__) {
        return true;
    }

    const filesApp = window.__FILES_APP__ || {};
    const editor = window.__FILES_EDITOR__;
    const activeFileName = filesApp.selectedFileName || null;
    const selectedFileIsDraft = filesApp.selectedFileIsDraft === true;
    const form = document.getElementById("service-form");
    const formWrapper = document.getElementById("form-wrapper");
    const actionButtons = document.getElementById("action-buttons");
    const busyPanel = document.getElementById("busy-panel");
    const busyTitle = document.getElementById("busy-title");
    const busyMessage = document.getElementById("busy-message");
    const autosaveStatus = document.getElementById("autosave-status");
    const lockBanner = document.getElementById("editing-lock-banner");
    const lockTitle = document.getElementById("editing-lock-title");
    const lockMessage = document.getElementById("editing-lock-message");
    const retryButton = document.getElementById("btn-retry-lease");
    const recoveryPanel = document.getElementById("recovery-panel");
    const recoveryEyebrow = document.getElementById("recovery-eyebrow");
    const recoveryTitle = document.getElementById("recovery-title");
    const recoveryMessage = document.getElementById("recovery-message");
    const recoveryTimestamp = document.getElementById("recovery-timestamp");
    const ignoreRecoveryButton = document.getElementById("btn-ignore-recovery");
    const restoreRecoveryButton = document.getElementById("btn-restore-recovery");
    const logoutForm = document.querySelector("[data-logout-form]");

    if (!form || !activeFileName) {
        if (autosaveStatus) autosaveStatus.hidden = true;
        return true;
    }
    if (!editor) {
        if (autosaveStatus) autosaveStatus.textContent = "A carregar o formulário…";
        return false;
    }
    window.__EDITING_COORDINATOR_STARTED__ = true;

    const DB_NAME = "sensorpoint-service-recovery-v1";
    const DB_VERSION = 1;
    const STORE_NAME = "recoveries";
    const LOCAL_SAVE_DELAY = 150;
    const SERVER_SAVE_DELAY = 750;
    const MAX_RETRY_DELAY = 30000;
    const recoveryMaxAgeMs = Math.max(Number(filesApp.recoveryMaxAgeDays || 30), 1) * 86400000;
    const editorUser = filesApp.editorUser || { id: "local", display_name: "Técnico local" };

    const makeId = () => window.crypto?.randomUUID?.()
        || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const tabInstanceId = makeId();

    let clientId = "";
    try {
        clientId = sessionStorage.getItem("sensorpoint-editor-client-id-v2") || "";
        if (!clientId) {
            clientId = makeId();
            sessionStorage.setItem("sensorpoint-editor-client-id-v2", clientId);
        }
    } catch (error) {
        clientId = makeId();
    }

    let editing = {};
    let sessionToken = "";
    let dirty = false;
    let hydrating = false;
    let readOnly = true;
    let initialized = false;
    let suppressBeforeUnload = false;
    let localSaveTimer = null;
    let serverSaveTimer = null;
    let retryTimer = null;
    let retryAttempt = 0;
    let autosaveInFlight = false;
    let autosaveQueued = false;
    let pendingAutosave = null;
    let pendingPanelAction = null;
    let serverPayload = null;
    let sourceSignatures = {};
    let sourcePhotos = [];
    let otherTabActive = false;
    let resumeConflictPending = false;
    let resumePromise = null;
    const operationKeys = new Map();

    const showToast = (message, variant = "info") => editor.showToast?.(message, variant);

    const setAutosaveStatus = (message, state = "idle") => {
        if (!autosaveStatus) return;
        autosaveStatus.hidden = false;
        autosaveStatus.textContent = message;
        autosaveStatus.dataset.state = state;
    };

    const setBooting = (booting, message = "A obter a versão mais recente…") => {
        document.body.classList.toggle("editor-booting", booting);
        if (busyPanel) {
            busyPanel.hidden = !booting;
            if (busyTitle) busyTitle.textContent = "A abrir a folha";
            if (busyMessage) busyMessage.textContent = message;
        }
        if (formWrapper) formWrapper.hidden = booting;
        if (actionButtons) actionButtons.hidden = booting;
    };

    const setCommitActionsEnabled = (enabled) => {
        const draftButton = document.getElementById("btn-save-draft");
        const sendButton = document.getElementById("btn-save-send");
        const cancelButton = document.getElementById("btn-cancel-file");
        if (draftButton) draftButton.disabled = !enabled;
        if (sendButton) sendButton.disabled = !enabled || !selectedFileIsDraft;
        if (cancelButton) cancelButton.disabled = !enabled || !selectedFileIsDraft;
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
        editor.renderAttachments?.();
        if (lockBanner) lockBanner.hidden = !readOnly;
        if (retryButton) retryButton.hidden = true;
        if (readOnly) {
            if (lockTitle) lockTitle.textContent = "Rascunho em modo de consulta";
            if (lockMessage) {
                const owner = ownerName || editing.owner?.owner_name || "outro técnico";
                lockMessage.textContent = `Este rascunho pertence a ${owner}. Abra a folha original para criar uma cópia privada.`;
            }
        }
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
        if (!database) return null;
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

    const readLatestRecoveryForFile = async () => {
        let latest = null;
        await withStore("readonly", (store) => {
            const index = store.index("userId");
            const request = index.openCursor(IDBKeyRange.only(editorUser.id));
            request.onsuccess = () => {
                const cursor = request.result;
                if (!cursor) return;
                const record = cursor.value;
                if (
                    record?.fileName === activeFileName
                    && Number(record.updatedAt || 0) > Number(latest?.updatedAt || 0)
                ) {
                    latest = record;
                }
                cursor.continue();
            };
            return request;
        });
        return latest;
    };

    const deleteRecovery = async (key = recoveryKey()) => {
        await withStore("readwrite", (store) => store.delete(key));
    };

    const persistLocalRecovery = async ({ announce = false, force = false } = {}) => {
        if ((!dirty && !force) || !editing.document_id) return null;
        try {
            const record = {
                key: recoveryKey(),
                userId: editorUser.id,
                documentId: editing.document_id,
                fileName: activeFileName,
                clientId,
                baseRevision: Number(editing.revision || 1),
                payload: editor.collectFormData(),
                updatedAt: Date.now(),
                expiresAt: Date.now() + recoveryMaxAgeMs,
            };
            await withStore("readwrite", (store) => store.put(record));
            if (announce && dirty && !autosaveInFlight) {
                setAutosaveStatus("Guardado neste dispositivo — a guardar no servidor…", "saving");
            }
            return record;
        } catch (error) {
            setAutosaveStatus("Não foi possível guardar neste dispositivo", "conflict");
            return null;
        }
    };

    const pruneExpiredRecoveries = async () => {
        const threshold = Date.now() - recoveryMaxAgeMs;
        await withStore("readwrite", (store) => {
            const request = store.openCursor();
            request.onsuccess = () => {
                const cursor = request.result;
                if (!cursor) return;
                if (Number(cursor.value?.updatedAt || 0) < threshold) cursor.delete();
                cursor.continue();
            };
            return request;
        });
    };

    const payloadHasSignatures = (payload) => Boolean(
        payload?.["Assinatura Cliente"] || payload?.["Assinatura Técnico"]
    );

    const comparablePayload = (payload) => {
        const value = { ...(payload || {}) };
        delete value["Assinatura Cliente"];
        delete value["Assinatura Técnico"];
        const sort = (item) => {
            if (Array.isArray(item)) return item.map(sort);
            if (item && typeof item === "object") {
                return Object.keys(item).sort().reduce((result, key) => {
                    result[key] = sort(item[key]);
                    return result;
                }, {});
            }
            return item;
        };
        return JSON.stringify(sort(value));
    };

    const editMetadata = (idempotencyKey = "") => ({
        document_id: editing.document_id || "",
        client_id: clientId,
        lease_token: sessionToken,
        base_revision: Number(editing.revision || 1),
        idempotency_key: idempotencyKey,
    });

    const hasActiveSession = () => Boolean(
        initialized
        && !readOnly
        && !otherTabActive
        && !resumeConflictPending
        && editing.document_id
        && clientId
        && sessionToken
    );

    const syncCommitActions = () => {
        setCommitActionsEnabled(hasActiveSession() && navigator.onLine);
    };

    const operationMetadata = (kind) => {
        if (!hasActiveSession()) {
            syncCommitActions();
            throw new Error(
                "A sessão de edição ainda não está pronta. Aguarde alguns segundos e tente novamente."
            );
        }
        if (!operationKeys.has(kind)) operationKeys.set(kind, makeId());
        return editMetadata(operationKeys.get(kind));
    };

    const hideRecoveryPanel = () => {
        pendingPanelAction = null;
        if (recoveryPanel) {
            recoveryPanel.hidden = true;
            recoveryPanel.classList.remove("is-conflict");
        }
    };

    const showConflictPanel = (localRecord, remotePayload, title = "Existem duas versões da folha") => {
        pendingPanelAction = { kind: "conflict", localRecord, remotePayload };
        recoveryPanel?.classList.add("is-conflict");
        if (recoveryPanel) recoveryPanel.hidden = false;
        if (recoveryEyebrow) recoveryEyebrow.textContent = "Conflito protegido";
        if (recoveryTitle) recoveryTitle.textContent = title;
        if (recoveryMessage) recoveryMessage.textContent = "Nenhuma versão foi sobrescrita. Escolha qual deve continuar.";
        if (recoveryTimestamp) recoveryTimestamp.textContent = "A cópia deste dispositivo continua guardada.";
        if (ignoreRecoveryButton) ignoreRecoveryButton.textContent = "Usar servidor";
        if (restoreRecoveryButton) restoreRecoveryButton.textContent = "Usar este dispositivo";
    };

    const handleConflict = (result) => {
        if (["editing_session_required", "lease_required"].includes(result?.code)) {
            editing = { ...editing, ...(result.editing || {}) };
            sessionToken = "";
            syncCommitActions();
            resumeEditingSession();
            return true;
        }
        if (!result || !["revision_conflict", "graph_conflict"].includes(result.code)) return false;
        editing = { ...editing, ...(result.editing || {}) };
        serverPayload = result.editing?.server_document || serverPayload;
        const localRecord = {
            payload: editor.collectFormData(),
            updatedAt: Date.now(),
            baseRevision: Number(editing.revision || 1),
        };
        dirty = true;
        resumeConflictPending = true;
        syncCommitActions();
        persistLocalRecovery({ force: true });
        setAutosaveStatus("Conflito — escolha a versão a manter", "conflict");
        showConflictPanel(localRecord, serverPayload, "A folha foi alterada noutra sessão");
        return true;
    };

    const scheduleRetry = () => {
        window.clearTimeout(retryTimer);
        const delay = Math.min(MAX_RETRY_DELAY, 1000 * (2 ** retryAttempt));
        retryAttempt += 1;
        retryTimer = window.setTimeout(() => runAutosave(), delay);
    };

    const runAutosave = async ({ keepalive = false } = {}) => {
        if (autosaveInFlight) {
            autosaveQueued = true;
            return false;
        }
        if (!dirty || readOnly || !sessionToken || !editing.document_id) return true;
        if (!navigator.onLine) {
            setCommitActionsEnabled(false);
            setAutosaveStatus("Sem ligação — alterações protegidas neste dispositivo", "offline");
            scheduleRetry();
            return false;
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
        setAutosaveStatus("A guardar no servidor…", "saving");
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
                if (handleConflict(result)) return false;
                if ([401, 423].includes(response.status)) {
                    setFormReadOnly(true, result.editing?.owner?.owner_name);
                }
                throw new Error(result.error || "Falha ao guardar no servidor.");
            }

            editing = { ...editing, ...(result.editing || {}) };
            sessionToken = result.editing?.lease?.token || sessionToken;
            serverPayload = result.editing?.server_document || payload;
            retryAttempt = 0;
            window.clearTimeout(retryTimer);
            setCommitActionsEnabled(true);
            if (pendingAutosave?.key === requestState.key) pendingAutosave = null;

            const currentPayload = editor.collectFormData();
            if (JSON.stringify(currentPayload) === requestState.serialized) {
                const attachmentsPending = Boolean(editor.hasPendingPhotoChanges?.());
                dirty = attachmentsPending;
                window.clearTimeout(localSaveTimer);
                window.clearTimeout(serverSaveTimer);
                if (payloadHasSignatures(currentPayload)) {
                    await persistLocalRecovery({ force: true });
                } else {
                    await deleteRecovery().catch(() => {});
                }
                if (attachmentsPending) {
                    setAutosaveStatus("Fotografias por guardar — use Guardar rascunho", "saving");
                } else {
                    const savedTime = new Date().toLocaleTimeString("pt-PT", {
                        hour: "2-digit",
                        minute: "2-digit",
                    });
                    setAutosaveStatus(`Guardado no servidor às ${savedTime}`, "saved");
                }
            } else {
                dirty = true;
                await persistLocalRecovery();
                autosaveQueued = true;
            }
            return true;
        } catch (error) {
            await persistLocalRecovery({ force: true });
            setCommitActionsEnabled(false);
            setAutosaveStatus(
                navigator.onLine
                    ? "Servidor indisponível — alterações protegidas neste dispositivo"
                    : "Sem ligação — alterações protegidas neste dispositivo",
                "offline",
            );
            scheduleRetry();
            return false;
        } finally {
            autosaveInFlight = false;
            if (autosaveQueued && dirty && !readOnly) {
                autosaveQueued = false;
                window.clearTimeout(serverSaveTimer);
                serverSaveTimer = window.setTimeout(() => runAutosave(), 100);
            }
        }
    };

    const schedulePersistence = () => {
        window.clearTimeout(localSaveTimer);
        window.clearTimeout(serverSaveTimer);
        localSaveTimer = window.setTimeout(
            () => persistLocalRecovery({ announce: true }),
            LOCAL_SAVE_DELAY,
        );
        serverSaveTimer = window.setTimeout(() => runAutosave(), SERVER_SAVE_DELAY);
    };

    const markDirty = (event = null) => {
        if (event?.target?.closest?.("#photos-section")) return;
        if (hydrating || readOnly || !initialized) return;
        dirty = true;
        setAutosaveStatus("A guardar neste dispositivo…", "saving");
        schedulePersistence();
    };

    const markAttachmentsDirty = () => {
        if (hydrating || readOnly || !initialized) return;
        dirty = true;
        setAutosaveStatus("Fotografias por guardar — use Guardar rascunho", "saving");
    };

    const applyPayload = (payload, signatures = {}, { shouldSave = false } = {}) => {
        hydrating = true;
        editor.populateForm(activeFileName, payload || {}, signatures || {}, sourcePhotos);
        hydrating = false;
        dirty = Boolean(shouldSave);
    };

    const loadBootstrap = async () => {
        const response = await fetch(
            `/api/file/${encodeURIComponent(activeFileName)}/bootstrap?client_id=${encodeURIComponent(clientId)}`,
            { headers: { "Accept": "application/json" } },
        );
        const result = await response.json();
        if (!response.ok || !result.success) {
            const error = new Error(result.error || "Não foi possível abrir a folha.");
            error.result = result;
            error.status = response.status;
            throw error;
        }
        return result;
    };

    const resumeEditingSession = async () => {
        if (hasActiveSession()) return true;
        if (
            !initialized
            || otherTabActive
            || resumeConflictPending
            || !navigator.onLine
        ) return false;
        if (resumePromise) return resumePromise;

        const previousRevision = Number(editing.revision || 1);
        const localPayload = editor.collectFormData();
        setFormReadOnly(true);
        setCommitActionsEnabled(false);
        setAutosaveStatus("A retomar a sessão de edição…", "saving");

        resumePromise = (async () => {
            try {
                const result = await loadBootstrap();
                const resumedEditing = { ...(result.editing || {}) };
                const remotePayload = result.document || result.source_document || {};
                const revisionChanged = Number(resumedEditing.revision || 1) !== previousRevision;
                const contentChanged = comparablePayload(remotePayload) !== comparablePayload(localPayload);

                editing = { ...editing, ...resumedEditing };
                sessionToken = resumedEditing.lease?.token || "";
                serverPayload = resumedEditing.server_document || remotePayload;
                sourceSignatures = result.signatures || sourceSignatures;
                sourcePhotos = result.photos || sourcePhotos;

                if (!sessionToken || !editing.document_id) {
                    throw new Error("Não foi possível retomar a sessão de edição.");
                }

                setFormReadOnly(false);
                if (revisionChanged && contentChanged) {
                    resumeConflictPending = true;
                    dirty = true;
                    await persistLocalRecovery({ force: true });
                    showConflictPanel(
                        {
                            payload: localPayload,
                            updatedAt: Date.now(),
                            baseRevision: previousRevision,
                        },
                        remotePayload,
                        "A folha foi alterada enquanto esta página esteve suspensa",
                    );
                    syncCommitActions();
                    setAutosaveStatus("Conflito — escolha a versão a manter", "conflict");
                    return false;
                }

                resumeConflictPending = false;
                syncCommitActions();
                setAutosaveStatus("Sessão de edição retomada", "saved");
                if (dirty) await runAutosave();
                return hasActiveSession();
            } catch (error) {
                const result = error.result || {};
                editing = { ...editing, ...(result.editing || {}) };
                sessionToken = "";
                if (error.status === 423) {
                    setFormReadOnly(true, result.editing?.owner?.owner_name);
                    setAutosaveStatus("Modo de consulta — rascunho aberto noutra sessão", "conflict");
                } else {
                    setFormReadOnly(false);
                    setAutosaveStatus("Não foi possível retomar a sessão — tente novamente", "offline");
                }
                setCommitActionsEnabled(false);
                showToast(error.message, "error");
                return false;
            } finally {
                resumePromise = null;
            }
        })();

        return resumePromise;
    };

    const initialize = async () => {
        setBooting(true);
        setFormReadOnly(true);
        setAutosaveStatus("A abrir a área de trabalho…", "saving");
        const localRecoveryPromise = readLatestRecoveryForFile().catch(() => null);
        try {
            const result = await loadBootstrap();
            const localRecovery = await localRecoveryPromise;
            editing = { ...(result.editing || {}) };
            sessionToken = result.editing?.lease?.token || "";
            serverPayload = result.editing?.server_document || result.document || null;
            sourceSignatures = result.signatures || {};
            sourcePhotos = result.photos || [];
            if (selectedFileIsDraft && tabChannel) {
                tabChannel.postMessage({
                    type: "hello",
                    fileName: activeFileName,
                    userId: editorUser.id,
                    instanceId: tabInstanceId,
                });
                await new Promise((resolve) => window.setTimeout(resolve, 100));
            }


            let initialPayload = result.document || result.source_document || {};
            let initialSignatures = sourceSignatures;
            let shouldSave = false;

            if (localRecovery?.payload) {
                const remoteComparable = comparablePayload(initialPayload);
                const localComparable = comparablePayload(localRecovery.payload);
                if (remoteComparable === localComparable) {
                    if (payloadHasSignatures(localRecovery.payload)) {
                        initialPayload = localRecovery.payload;
                        initialSignatures = localRecovery.payload;
                    } else {
                        deleteRecovery(localRecovery.key).catch(() => {});
                    }
                } else if (
                    result.editing?.server_document
                    && Number(localRecovery.baseRevision || 1) < Number(editing.revision || 1)
                ) {
                    resumeConflictPending = true;
                    showConflictPanel(localRecovery, initialPayload);
                } else {
                    initialPayload = localRecovery.payload;
                    initialSignatures = localRecovery.payload;
                    shouldSave = true;
                }
            }

            applyPayload(initialPayload, initialSignatures, { shouldSave });
            initialized = true;
            setFormReadOnly(otherTabActive, otherTabActive ? "outra aba deste técnico" : "");
            syncCommitActions();
            setBooting(false);
            if (otherTabActive) {
                setAutosaveStatus("Modo de consulta — rascunho aberto noutra aba", "conflict");
            } else if (shouldSave) {
                setAutosaveStatus("Alterações recuperadas — a guardar no servidor…", "saving");
                schedulePersistence();
            } else {
                setAutosaveStatus(
                    result.is_draft
                        ? "Rascunho pronto — guardado no servidor"
                        : "Área privada pronta — só este técnico vê as alterações",
                    "saved",
                );
            }
            pruneExpiredRecoveries().catch(() => {});
        } catch (error) {
            const result = error.result || {};
            editing = { ...editing, ...(result.editing || {}) };
            const localRecovery = await localRecoveryPromise;
            if (result.document) {
                sourcePhotos = result.photos || sourcePhotos;
                applyPayload(result.document, result.signatures || {}, { shouldSave: false });
            } else if (localRecovery?.payload) {
                applyPayload(localRecovery.payload, localRecovery.payload, { shouldSave: false });
            }
            initialized = true;
            setBooting(false);
            if (error.status === 423) {
                setFormReadOnly(true, result.editing?.owner?.owner_name);
                setAutosaveStatus("Modo de consulta — rascunho de outro técnico", "conflict");
            } else {
                setFormReadOnly(false);
                setCommitActionsEnabled(false);
                setAutosaveStatus("Servidor indisponível — pode consultar a cópia local", "offline");
                showToast(error.message, "error");
            }
        }
    };

    ignoreRecoveryButton?.addEventListener("click", async () => {
        const action = pendingPanelAction;
        if (!action) return hideRecoveryPanel();
        if (action.kind === "conflict") {
            if (action.remotePayload) applyPayload(action.remotePayload, sourceSignatures);
            dirty = false;
            resumeConflictPending = false;
            await deleteRecovery(action.localRecord?.key).catch(() => {});
            hideRecoveryPanel();
            syncCommitActions();
            setAutosaveStatus("Versão do servidor carregada", "saved");
        }
    });

    restoreRecoveryButton?.addEventListener("click", async () => {
        const action = pendingPanelAction;
        if (!action) return hideRecoveryPanel();
        if (action.kind === "conflict" && action.localRecord?.payload) {
            resumeConflictPending = false;
            applyPayload(action.localRecord.payload, action.localRecord.payload, { shouldSave: true });
            hideRecoveryPanel();
            await persistLocalRecovery({ force: true });
            await runAutosave();
        }
    });

    form.addEventListener("input", markDirty, true);
    form.addEventListener("change", markDirty, true);
    form.addEventListener("pointerup", (event) => {
        if (event.target.closest?.(".signature-canvas")) window.setTimeout(markDirty, 0);
    }, true);
    form.addEventListener("click", (event) => {
        if (event.target.closest?.("[data-row-remove], #btn-add-material, #btn-add-technician, [data-duration-reset], [data-signature-clear]")) {
            window.setTimeout(markDirty, 0);
        }
    }, true);

    const releaseSession = async ({ background = false } = {}) => {
        if (!sessionToken || !editing.document_id) return;
        const token = sessionToken;
        sessionToken = "";
        setCommitActionsEnabled(false);
        const payload = JSON.stringify({
            document: dirty ? {
                ...editor.collectFormData(),
                "Assinatura Cliente": "",
                "Assinatura Técnico": "",
            } : null,
            _edit: {
                ...editMetadata(makeId()),
                lease_token: token,
            },
        });
        const url = `/api/file/${encodeURIComponent(activeFileName)}/editing/close`;
        if (background && navigator.sendBeacon) {
            if (navigator.sendBeacon(url, new Blob([payload], { type: "application/json" }))) return;
        }
        try {
            await fetch(url, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: payload,
                keepalive: background,
            });
        } catch (error) {
            // The IndexedDB copy remains the recovery source.
        }
    };

    const leavePage = async (callback) => {
        window.clearTimeout(localSaveTimer);
        window.clearTimeout(serverSaveTimer);
        if (dirty) {
            await persistLocalRecovery({ force: true });
            await runAutosave({ keepalive: true });
        }
        await releaseSession();
        suppressBeforeUnload = true;
        callback?.();
    };

    document.addEventListener("click", (event) => {
        const link = event.target.closest?.(".file-card-link[data-name]");
        if (link && link.dataset.name !== activeFileName) {
            event.preventDefault();
            event.stopImmediatePropagation();
            leavePage(() => { window.location.href = link.href; });
            return;
        }
        const reloadButton = event.target.closest?.("#btn-cancel-edit");
        if (reloadButton) {
            event.preventDefault();
            event.stopImmediatePropagation();
            leavePage(() => window.location.reload());
        }
    }, true);

    logoutForm?.addEventListener("submit", (event) => {
        if (suppressBeforeUnload) return;
        event.preventDefault();
        leavePage(() => logoutForm.submit());
    });

    const releaseOnPageExit = () => {
        if (tabChannel && !otherTabActive) {
            tabChannel.postMessage({
                type: "closing",
                fileName: activeFileName,
                userId: editorUser.id,
                instanceId: tabInstanceId,
            });
        }
        window.clearTimeout(localSaveTimer);
        window.clearTimeout(serverSaveTimer);
        window.clearTimeout(retryTimer);
        setCommitActionsEnabled(false);
        if (dirty) persistLocalRecovery({ force: true });
        releaseSession({ background: true });
    };

    window.addEventListener("pagehide", releaseOnPageExit);
    document.addEventListener("freeze", releaseOnPageExit);
    const resumeAfterPageRestore = () => {
        if (initialized && !sessionToken && !otherTabActive) resumeEditingSession();
    };
    window.addEventListener("pageshow", resumeAfterPageRestore);
    document.addEventListener("resume", resumeAfterPageRestore);
    document.addEventListener("visibilitychange", () => {
        if (document.hidden && dirty) persistLocalRecovery({ force: true });
    });
    window.addEventListener("offline", () => {
        setCommitActionsEnabled(false);
        if (dirty) setAutosaveStatus("Sem ligação — alterações protegidas neste dispositivo", "offline");
    });
    window.addEventListener("online", async () => {
        retryAttempt = 0;
        if (!sessionToken) await resumeEditingSession();
        syncCommitActions();
        if (dirty && !readOnly && !otherTabActive) runAutosave();
    });

    let tabChannel = null;
    if (selectedFileIsDraft && "BroadcastChannel" in window) {
        tabChannel = new BroadcastChannel("sensorpoint-editing-v2");
        tabChannel.onmessage = (event) => {
            const message = event.data || {};
            if (
                message.fileName !== activeFileName
                || message.userId !== editorUser.id
                || message.instanceId === tabInstanceId
            ) return;
            if (message.type === "hello" && sessionToken && !otherTabActive) {
                tabChannel.postMessage({
                    type: "active",
                    fileName: activeFileName,
                    userId: editorUser.id,
                    instanceId: tabInstanceId,
                });
            }
            if (message.type === "active") {
                otherTabActive = true;
                setFormReadOnly(true, "outra aba deste técnico");
                setAutosaveStatus("Modo de consulta — rascunho aberto noutra aba", "conflict");
            }
            if (message.type === "closing" && otherTabActive && sessionToken) {
                otherTabActive = false;
                setFormReadOnly(false);
                syncCommitActions();
                setAutosaveStatus("Rascunho pronto — esta aba pode editar", "saved");
                if (dirty && navigator.onLine) runAutosave();
            }
        };
    }

    window.__EDITING_COORDINATOR__ = {
        operationMetadata,
        async prepareOperation(kind) {
            if (!hasActiveSession()) {
                const resumed = await resumeEditingSession();
                if (!resumed) {
                    throw new Error(
                        "A sessão de edição ainda não está pronta. Aguarde alguns segundos e tente novamente."
                    );
                }
            }
            return operationMetadata(kind);
        },
        syncActionState: syncCommitActions,
        async markCommitted(result, kind) {
            const committedKey = recoveryKey();
            editing = { ...editing, ...result };
            operationKeys.delete(kind);
            dirty = false;
            pendingAutosave = null;
            window.clearTimeout(localSaveTimer);
            window.clearTimeout(serverSaveTimer);
            await deleteRecovery(committedKey).catch(() => {});
            if (result.publication_status === "pending") {
                setAutosaveStatus("Guardado no servidor — publicação no SharePoint pendente", "saving");
            } else {
                setAutosaveStatus("Alterações consolidadas", "saved");
            }
            resumeConflictPending = false;
            if (["send", "cancel"].includes(kind) || result.created_copy) sessionToken = "";
            syncCommitActions();
        },
        handleConflict,
        markDirty,
        markAttachmentsDirty,
        isDirty: () => dirty,
    };

    initialize();
    return true;
};

const bootEditingCoordinator = () => {
    let attempts = 0;
    let retryTimer = null;
    const retry = () => {
        attempts += 1;
        if (startEditingCoordinator()) {
            window.clearInterval(retryTimer);
            return;
        }
        if (attempts > 100) window.clearInterval(retryTimer);
    };
    retry();
    retryTimer = window.setInterval(retry, 100);
    document.addEventListener("files-editor-ready", retry);
};

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootEditingCoordinator, { once: true });
} else {
    bootEditingCoordinator();
}
