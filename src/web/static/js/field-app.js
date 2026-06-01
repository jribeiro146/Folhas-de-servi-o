document.addEventListener("DOMContentLoaded", () => {
    let activeFileName = null;
    let pendingConfirmation = null;

    const filesApp = window.__FILES_APP__ || { requiredFields: [] };
    const fileCards = Array.from(document.querySelectorAll(".file-card-link[data-name]"));
    const fileList = document.getElementById("file-list");
    const fileSearch = document.getElementById("file-search");
    const fileCounter = document.getElementById("file-counter");
    const refreshBtn = document.getElementById("btn-refresh");
    const toggleSidebarBtn = document.getElementById("btn-toggle-sidebar");
    const sidebar = document.getElementById("sidebar");

    const form = document.getElementById("service-form");
    const formWrapper = document.getElementById("form-wrapper");
    const emptyStatePanel = document.getElementById("empty-state-panel");
    const busyPanel = document.getElementById("busy-panel");
    const busyTitle = document.getElementById("busy-title");
    const busyMessage = document.getElementById("busy-message");
    const titleHeader = document.getElementById("active-file-title");
    const statusMessage = document.getElementById("status-message");
    const actionButtons = document.getElementById("action-buttons");
    const toastZone = document.getElementById("toast-zone");
    const confirmPanel = document.getElementById("confirm-panel");
    const confirmEyebrow = document.getElementById("confirm-eyebrow");
    const confirmTitle = document.getElementById("confirm-title");
    const confirmMessage = document.getElementById("confirm-message");
    const confirmCancel = document.getElementById("confirm-cancel");
    const confirmAccept = document.getElementById("confirm-accept");

    const btnCancelEdit = document.getElementById("btn-cancel-edit");
    const btnSaveDraft = document.getElementById("btn-save-draft");
    const btnSaveSend = document.getElementById("btn-save-send");
    const btnCancelFile = document.getElementById("btn-cancel-file");
    const selectedFileName = filesApp.selectedFileName || null;
    const selectedFileData = filesApp.selectedFileData || null;
    const selectedSignatures = filesApp.selectedSignatures || {};
    const selectedFileError = filesApp.selectedFileError || null;
    const signaturePads = [
        {
            label: "Assinatura Cliente",
            canvas: document.getElementById("signature-client-canvas"),
            input: document.getElementById("signature-client-input"),
            clearButton: document.querySelector('[data-signature-clear="Assinatura Cliente"]')
        },
        {
            label: "Assinatura Técnico",
            canvas: document.getElementById("signature-tech-canvas"),
            input: document.getElementById("signature-tech-input"),
            clearButton: document.querySelector('[data-signature-clear="Assinatura Técnico"]')
        }
    ];

    const showToast = (message, variant = "info") => {
        const toast = document.createElement("div");
        toast.className = `toast toast-${variant}`;
        toast.textContent = message;
        toastZone.appendChild(toast);
        window.setTimeout(() => toast.remove(), 3500);
    };

    const setStatusMessage = (message = "") => {
        if (!statusMessage) {
            return;
        }

        statusMessage.textContent = message;
        statusMessage.hidden = !message;
    };

    const getSignatureContext = (pad) => {
        if (!pad.canvas) {
            return null;
        }

        const context = pad.canvas.getContext("2d");
        if (!context) {
            return null;
        }

        const dpr = Math.max(window.devicePixelRatio || 1, 1);
        const bounds = pad.canvas.getBoundingClientRect();
        const width = Math.max(Math.round(bounds.width * dpr), 1);
        const height = Math.max(Math.round(bounds.height * dpr), 1);

        if (pad.canvas.width !== width || pad.canvas.height !== height) {
            const currentValue = pad.input?.value || "";
            pad.canvas.width = width;
            pad.canvas.height = height;
            context.setTransform(dpr, 0, 0, dpr, 0, 0);
            context.lineCap = "round";
            context.lineJoin = "round";
            context.strokeStyle = "#16312d";
            context.lineWidth = 2.5;
            context.clearRect(0, 0, bounds.width || width / dpr, bounds.height || height / dpr);
            if (currentValue) {
                drawSignatureImage(pad, currentValue);
            }
        }

        return context;
    };

    const clearSignaturePad = (pad, syncInput = true) => {
        const context = getSignatureContext(pad);
        if (!context || !pad.canvas) {
            return;
        }

        const bounds = pad.canvas.getBoundingClientRect();
        context.clearRect(0, 0, bounds.width || pad.canvas.width, bounds.height || pad.canvas.height);
        pad.isEmpty = true;
        if (pad.input && syncInput) {
            pad.input.value = "";
        }
    };

    const syncSignatureValue = (pad) => {
        if (!pad.canvas || !pad.input) {
            return;
        }

        pad.input.value = pad.isEmpty ? "" : pad.canvas.toDataURL("image/png");
    };

    const drawSignatureImage = (pad, dataUrl) => {
        if (!pad.canvas || !dataUrl) {
            clearSignaturePad(pad);
            return;
        }

        const context = getSignatureContext(pad);
        if (!context) {
            return;
        }

        const image = new Image();
        image.onload = () => {
            const bounds = pad.canvas.getBoundingClientRect();
            clearSignaturePad(pad, false);

            const canvasWidth = bounds.width || pad.canvas.width;
            const canvasHeight = bounds.height || pad.canvas.height;
            const scale = Math.min(canvasWidth / image.width, canvasHeight / image.height, 1);
            const drawWidth = image.width * scale;
            const drawHeight = image.height * scale;
            const offsetX = (canvasWidth - drawWidth) / 2;
            const offsetY = (canvasHeight - drawHeight) / 2;

            context.drawImage(image, offsetX, offsetY, drawWidth, drawHeight);
            pad.isEmpty = false;
            if (pad.input) {
                pad.input.value = dataUrl;
            }
        };
        image.src = dataUrl;
    };

    const applySignatures = (signatures = {}) => {
        signaturePads.forEach((pad) => {
            if (!pad.canvas || !pad.input) {
                return;
            }

            const value = signatures[pad.label] || "";
            if (value) {
                drawSignatureImage(pad, value);
            } else {
                clearSignaturePad(pad);
            }
        });
    };

    const getCanvasPoint = (canvas, event) => {
        const rect = canvas.getBoundingClientRect();
        return {
            x: event.clientX - rect.left,
            y: event.clientY - rect.top,
        };
    };

    const resetConfirmButton = () => {
        confirmAccept.textContent = "Confirmar";
        confirmAccept.className = "btn btn-primary";
    };

    const hideConfirm = () => {
        pendingConfirmation = null;
        confirmPanel.hidden = true;
        resetConfirmButton();
    };

    const showConfirm = ({
        eyebrow = "Confirmar ação",
        title = "Confirmar ação",
        message = "Tem a certeza que pretende continuar?",
        confirmLabel = "Confirmar",
        confirmVariant = "primary",
        onAccept = null
    }) => {
        pendingConfirmation = typeof onAccept === "function" ? onAccept : null;
        confirmEyebrow.textContent = eyebrow;
        confirmTitle.textContent = title;
        confirmMessage.textContent = message;
        confirmAccept.textContent = confirmLabel;
        confirmAccept.className = `btn btn-${confirmVariant}`;
        confirmPanel.hidden = false;
        confirmPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
        confirmAccept.focus();
    };

    const setBusy = (busy, title = "A guardar a folha", message = "Estamos a atualizar o LINK. Pode demorar alguns segundos.") => {
        [btnCancelEdit, btnSaveDraft, btnSaveSend, btnCancelFile, confirmCancel, confirmAccept].forEach((button) => {
            if (button) {
                button.disabled = busy;
            }
        });
        if (busyPanel) {
            busyTitle.textContent = title;
            busyMessage.textContent = message;
            busyPanel.hidden = !busy;
        }
    };

    const updateWorkspaceVisibility = (hasActiveFile) => {
        formWrapper.hidden = !hasActiveFile;
        if (emptyStatePanel) {
            emptyStatePanel.hidden = hasActiveFile;
        }
        actionButtons.hidden = !hasActiveFile;
        if (!hasActiveFile) {
            hideConfirm();
        }
    };

    const collectFormData = () => {
        const payload = {};
        const elements = form.querySelectorAll("input, textarea");

        elements.forEach((element) => {
            const label = element.dataset.label;
            if (!label) {
                return;
            }

            if (element.type === "checkbox") {
                payload[label] = element.checked;
                return;
            }

            if (element.type === "number") {
                payload[label] = element.value === "" ? null : Number(element.value);
                return;
            }

            payload[label] = element.value.trim() === "" ? null : element.value.trim();
        });

        return payload;
    };

    const getMissingRequiredFields = (payload) => {
        return filesApp.requiredFields.filter((label) => {
            const value = payload[label];
            return value === null || value === "" || value === undefined;
        });
    };

    const initializeSignaturePads = () => {
        signaturePads.forEach((pad) => {
            if (!pad.canvas || !pad.input) {
                return;
            }

            pad.isEmpty = !pad.input.value;
            pad.drawing = false;

            getSignatureContext(pad);
            if (pad.input.value) {
                drawSignatureImage(pad, pad.input.value);
            } else {
                clearSignaturePad(pad, false);
            }

            pad.canvas.addEventListener("pointerdown", (event) => {
                if (formWrapper.hidden) {
                    return;
                }

                const context = getSignatureContext(pad);
                if (!context) {
                    return;
                }

                const point = getCanvasPoint(pad.canvas, event);
                context.beginPath();
                context.moveTo(point.x, point.y);
                context.lineTo(point.x + 0.01, point.y + 0.01);
                context.stroke();
                pad.drawing = true;
                pad.isEmpty = false;
                pad.canvas.setPointerCapture?.(event.pointerId);
                syncSignatureValue(pad);
                event.preventDefault();
            });

            pad.canvas.addEventListener("pointermove", (event) => {
                if (!pad.drawing) {
                    return;
                }

                const context = getSignatureContext(pad);
                if (!context) {
                    return;
                }

                const point = getCanvasPoint(pad.canvas, event);
                context.lineTo(point.x, point.y);
                context.stroke();
                pad.isEmpty = false;
                syncSignatureValue(pad);
                event.preventDefault();
            });

            const stopDrawing = (event) => {
                if (!pad.drawing) {
                    return;
                }

                pad.drawing = false;
                pad.canvas.releasePointerCapture?.(event.pointerId);
                syncSignatureValue(pad);
            };

            pad.canvas.addEventListener("pointerup", stopDrawing);
            pad.canvas.addEventListener("pointerleave", stopDrawing);
            pad.canvas.addEventListener("pointercancel", stopDrawing);

            pad.clearButton?.addEventListener("click", () => {
                clearSignaturePad(pad);
            });
        });

        window.addEventListener("resize", () => {
            signaturePads.forEach((pad) => {
                if (!pad.canvas || !pad.input) {
                    return;
                }

                const currentValue = pad.input.value;
                getSignatureContext(pad);
                if (currentValue) {
                    drawSignatureImage(pad, currentValue);
                }
            });
        });
    };

    const setActiveCard = (fileName) => {
        fileCards.forEach((link) => {
            const card = link.closest(".file-card");
            if (!card) {
                return;
            }

            card.classList.toggle("active", link.dataset.name === fileName);
        });
    };

    const populateForm = (fileName, data, signatures = {}) => {
        activeFileName = fileName;
        form.reset();

        Object.entries(data).forEach(([label, value]) => {
            const element = form.querySelector(`[data-label="${label}"]`);
            if (!element) {
                return;
            }

            if (element.type === "checkbox") {
                element.checked = value === true;
                return;
            }

            element.value = value ?? "";
        });

        setActiveCard(fileName);
        applySignatures(signatures);
        titleHeader.textContent = `A editar: ${fileName}.xlsx`;
        setStatusMessage("Rascunhos escrevem no LINK. Enviar arquiva o Excel.");
        updateWorkspaceVisibility(true);

        if (window.innerWidth < 1280) {
            formWrapper.scrollIntoView({ behavior: "smooth", block: "start" });
        }
    };

    const resetActiveState = () => {
        activeFileName = null;
        form.reset();
        updateWorkspaceVisibility(false);
        titleHeader.textContent = "Selecione uma folha ativa";
        setStatusMessage("");
        setActiveCard(null);
        applySignatures({});
    };

    const loadExcelData = async (fileName) => {
        hideConfirm();

        try {
            const response = await fetch(`/api/file/${encodeURIComponent(fileName)}`);
            const result = await response.json();

            if (!response.ok || !result.success) {
                throw new Error(result.error || "Falha ao carregar a folha.");
            }

            populateForm(fileName, result.data, result.signatures || {});
            if (window.innerWidth < 960) {
                sidebar.classList.remove("is-open");
            }
        } catch (error) {
            showToast(error.message, "error");
        }
    };

    const saveDraft = async () => {
        if (!activeFileName) {
            return;
        }

        hideConfirm();
        setBusy(true, "A guardar rascunho", "Estamos a escrever os dados no LINK.");

        try {
            const payload = collectFormData();
            const response = await fetch(`/api/file/${encodeURIComponent(activeFileName)}/draft`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });
            const result = await response.json();

            if (!response.ok || !result.success) {
                throw new Error(result.error || "Falha ao guardar rascunho.");
            }

            const nextFileName = result.file || activeFileName;
            const successMessage = result.created_copy
                ? "Rascunho guardado e nova folha em execução criada."
                : "Rascunho guardado com sucesso.";

            showToast(successMessage, "success");
            window.setTimeout(() => {
                window.location.href = `/?file=${encodeURIComponent(nextFileName)}`;
            }, 800);
        } catch (error) {
            showToast(error.message, "error");
        } finally {
            setBusy(false);
        }
    };

    const sendFile = async (payload) => {
        hideConfirm();
        setBusy(true, "A arquivar a folha", "Estamos a gravar e a mover o ficheiro para Arquivadas.");

        try {
            const response = await fetch(`/api/file/${encodeURIComponent(activeFileName)}/send`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });
            const result = await response.json();

            if (!response.ok || !result.success) {
                const suffix = result.missing_fields ? ` (${result.missing_fields.join(", ")})` : "";
                throw new Error((result.error || "Falha ao fechar a folha.") + suffix);
            }

            showToast("Folha concluída e arquivada com sucesso.", "success");
            window.setTimeout(() => {
                window.location.href = "/";
            }, 1200);
        } catch (error) {
            showToast(error.message, "error");
        } finally {
            setBusy(false);
        }
    };

    const cancelFile = async () => {
        hideConfirm();
        setBusy(true, "A cancelar a folha", "Estamos a mover o ficheiro para a pasta Canceladas.");

        try {
            const response = await fetch(`/api/file/${encodeURIComponent(activeFileName)}/cancel`, {
                method: "POST",
                headers: { "Content-Type": "application/json" }
            });
            const result = await response.json();

            if (!response.ok || !result.success) {
                throw new Error(result.error || "Falha ao cancelar a folha.");
            }

            showToast("Folha cancelada com sucesso.", "success");
            window.setTimeout(() => {
                window.location.href = "/";
            }, 1200);
        } catch (error) {
            showToast(error.message, "error");
        } finally {
            setBusy(false);
        }
    };

    fileSearch?.addEventListener("input", () => {
        const search = fileSearch.value.trim().toLowerCase();
        let visibleCount = 0;

        Array.from(fileList.children).forEach((item) => {
            if (!item.classList.contains("file-card")) {
                return;
            }

            const matches = item.dataset.search.includes(search);
            item.hidden = !matches;
            if (matches) {
                visibleCount += 1;
            }
        });

        fileCounter.textContent = `${visibleCount} folha(s)`;
    });

    refreshBtn?.addEventListener("click", () => window.location.reload());
    toggleSidebarBtn?.addEventListener("click", () => sidebar.classList.toggle("is-open"));

    confirmCancel?.addEventListener("click", () => {
        hideConfirm();
    });

    confirmAccept?.addEventListener("click", async () => {
        const action = pendingConfirmation;
        if (!action) {
            hideConfirm();
            return;
        }

        await action();
    });

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && !confirmPanel.hidden) {
            hideConfirm();
        }
    });

    btnCancelEdit?.addEventListener("click", async () => {
        if (!activeFileName) {
            return;
        }

        await loadExcelData(activeFileName);
        showToast("A folha foi recarregada.", "info");
    });

    btnSaveDraft?.addEventListener("click", async () => {
        await saveDraft();
    });

    btnSaveSend?.addEventListener("click", () => {
        if (!activeFileName) {
            return;
        }

        const payload = collectFormData();
        const missingFields = getMissingRequiredFields(payload);
        if (missingFields.length > 0) {
            showToast(`Campos obrigatórios em falta: ${missingFields.join(", ")}`, "error");
            return;
        }

        showConfirm({
            eyebrow: "Finalizar folha",
            title: "Guardar e arquivar",
            message: "A folha vai ser gravada no LINK e o ficheiro Excel será movido para Arquivadas.",
            confirmLabel: "Guardar e arquivar",
            confirmVariant: "success",
            onAccept: async () => {
                await sendFile(payload);
            }
        });
    });

    btnCancelFile?.addEventListener("click", () => {
        if (!activeFileName) {
            return;
        }

        showConfirm({
            eyebrow: "Cancelar folha",
            title: "Mover para canceladas",
            message: "Esta ação vai retirar a folha das ativas e mover o Excel para a pasta Canceladas.",
            confirmLabel: "Cancelar folha",
            confirmVariant: "danger",
            onAccept: async () => {
                await cancelFile();
            }
        });
    });

    initializeSignaturePads();
    resetActiveState();

    if (selectedFileName && selectedFileData) {
        populateForm(selectedFileName, selectedFileData, selectedSignatures);
    } else if (selectedFileError) {
        setStatusMessage(selectedFileError);
        showToast(selectedFileError, "error");
    }
});
