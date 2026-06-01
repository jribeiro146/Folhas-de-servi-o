document.addEventListener("DOMContentLoaded", () => {
    let activeFileName = null;
    let pendingConfirmation = null;

    const filesApp = window.__FILES_APP__ || {};
    const requiredFields = Array.isArray(filesApp.requiredFields) ? filesApp.requiredFields : [];
    const selectedFileName = filesApp.selectedFileName || null;
    const selectedDocumentData = filesApp.selectedDocumentData || null;
    const selectedSignatures = filesApp.selectedSignatures || {};
    const selectedFileError = filesApp.selectedFileError || null;

    const fileCards = Array.from(document.querySelectorAll(".file-card-link[data-name]"));
    const fileList = document.getElementById("file-list");
    const fileSearch = document.getElementById("file-search");
    const fileCounter = document.getElementById("file-counter");
    const refreshBtn = document.getElementById("btn-refresh");
    const toggleSidebarBtn = document.getElementById("btn-toggle-sidebar");
    const sidebar = document.getElementById("sidebar");
    const sidebarScrim = document.getElementById("sidebar-scrim");

    const form = document.getElementById("service-form");
    const formWrapper = document.getElementById("form-wrapper");
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
    const confirmInternalNotesField = document.getElementById("confirm-internal-notes-field");
    const confirmInternalNotes = document.getElementById("confirm-internal-notes");
    const confirmCancel = document.getElementById("confirm-cancel");
    const confirmAccept = document.getElementById("confirm-accept");

    const btnPreviewDocument = document.getElementById("btn-preview-document");
    const btnExportPdf = document.getElementById("btn-export-pdf");
    const btnCancelEdit = document.getElementById("btn-cancel-edit");
    const btnSaveDraft = document.getElementById("btn-save-draft");
    const btnSaveSend = document.getElementById("btn-save-send");
    const btnCancelFile = document.getElementById("btn-cancel-file");
    const btnAddMaterial = document.getElementById("btn-add-material");
    const btnAddTechnician = document.getElementById("btn-add-technician");
    const languageSelect = document.getElementById("document-language-select");
    const documentLanguageInput = document.getElementById("document-language");

    const materialsList = document.getElementById("materials-list");
    const techniciansList = document.getElementById("technicians-list");
    const materialTemplate = document.getElementById("material-row-template");
    const technicianTemplate = document.getElementById("technician-row-template");

    const simpleFields = Array.from(form.querySelectorAll("[data-field]"));
    const serviceTypeInputs = Array.from(form.querySelectorAll('[data-group="service_types"]'));
    const equipmentInputs = Array.from(form.querySelectorAll('[data-group="equipments"]'));
    let currentLanguage = "pt";
    const MAX_TECHNICIANS = 4;

    const translations = {
        pt: {
            sheets_nav: "Folhas",
            topbar_eyebrow: "Folha de serviço",
            preview: "Pré-visualizar",
            export_pdf: "Exportar PDF",
            reload: "Recarregar",
            language_label: "Idioma",
            header_eyebrow: "Cabeçalho",
            sheet_identification: "Identificação da folha",
            service_number: "Nº da folha de serviço",
            customer_eyebrow: "Cliente",
            customer_data: "Dados do cliente",
            customer: "Cliente",
            requested_by: "Pedido por",
            request_date: "Em",
            customer_number: "Nº de cliente",
            phone: "Telefone",
            site_eyebrow: "Instalação",
            site_data: "Dados da instalação",
            contact: "Contacto",
            local_store: "Local / loja",
            contract: "Contrato",
            address: "Morada",
            store_number: "Loja n.º",
            service_eyebrow: "Serviço",
            service_type: "Tipo de serviço",
            equipment_eyebrow: "Equipamentos",
            equipment_systems: "Sistemas intervencionados",
            planning_eyebrow: "Planeamento",
            requested_tasks_title: "Trabalhos a efectuar",
            requested_tasks: "Descrição dos trabalhos",
            intervention_eyebrow: "Intervenção",
            intervention_report_title: "Relatório técnico da intervenção",
            intervention_report: "Relatório técnico",
            materials_eyebrow: "Materiais",
            materials_title: "Materiais / artigos / peças",
            add_line: "Adicionar linha",
            description: "Descrição",
            technicians_eyebrow: "Técnicos",
            technicians_title: "Registo de técnicos e horas",
            add_technician: "Adicionar técnico",
            technician: "Técnico",
            start_time: "Hora Início",
            end_time: "Hora Fim",
            total_hours: "Total Horas",
            closing_eyebrow: "Fecho",
            customer_signature_title: "Assinatura do cliente",
            clear: "Limpar",
            customer_signature_date: "Cliente - data",
            save_send: "Guardar e enviar",
            cancel_sheet: "Cancelar folha",
            save_draft: "Guardar rascunho",
            select_technician: "Selecionar técnico",
            ready_title: "Selecione uma folha ativa",
            editing_title: "A editar",
            preparing_document: "A preparar documento..."
        },
        en: {
            sheets_nav: "Sheets",
            topbar_eyebrow: "Service sheet",
            preview: "Preview",
            export_pdf: "Export PDF",
            reload: "Reload",
            language_label: "Language",
            header_eyebrow: "Header",
            sheet_identification: "Sheet identification",
            service_number: "Service sheet number",
            customer_eyebrow: "Customer",
            customer_data: "Customer details",
            customer: "Customer",
            requested_by: "Requested by",
            request_date: "Date",
            customer_number: "Customer number",
            phone: "Phone",
            site_eyebrow: "Site",
            site_data: "Site information",
            contact: "Contact",
            local_store: "Local / store",
            contract: "Contract",
            address: "Address",
            store_number: "Store no.",
            service_eyebrow: "Service",
            service_type: "Service type",
            equipment_eyebrow: "Equipment",
            equipment_systems: "Systems serviced",
            planning_eyebrow: "Planning",
            requested_tasks_title: "Requested tasks",
            requested_tasks: "Task description",
            intervention_eyebrow: "Intervention",
            intervention_report_title: "Technical intervention report",
            intervention_report: "Technical report",
            materials_eyebrow: "Materials",
            materials_title: "Materials / items / parts",
            add_line: "Add line",
            description: "Description",
            technicians_eyebrow: "Technicians",
            technicians_title: "Technician time records",
            add_technician: "Add technician",
            technician: "Technician",
            start_time: "Start time",
            end_time: "End time",
            total_hours: "Total time",
            closing_eyebrow: "Close",
            customer_signature_title: "Customer signature",
            clear: "Clear",
            customer_signature_date: "Customer - date",
            save_send: "Save and send",
            cancel_sheet: "Cancel sheet",
            save_draft: "Save draft",
            select_technician: "Select technician",
            ready_title: "Select an active sheet",
            editing_title: "Editing",
            preparing_document: "Preparing document..."
        }
    };

    const serviceLabels = {
        pt: {
            piquete: "Piquete",
            assistencia: "Assistência",
            manutencao: "Manutenção",
            formacao: "Formação",
            colocacao_servico: "Colocação de serviço",
            reparacao_oficina: "Reparação oficina",
            garantia: "Garantia",
            instalacao: "Instalação"
        },
        en: {
            piquete: "Emergency call",
            assistencia: "Assistance",
            manutencao: "Maintenance",
            formacao: "Training",
            colocacao_servico: "Service commissioning",
            reparacao_oficina: "Workshop repair",
            garantia: "Warranty",
            instalacao: "Installation"
        }
    };

    const t = (key) => translations[currentLanguage]?.[key] || translations.pt[key] || key;

    const normalizeLanguage = (value) => (value === "en" ? "en" : "pt");

    const applyLanguage = (language, root = document) => {
        currentLanguage = normalizeLanguage(language);

        if (languageSelect) {
            languageSelect.value = currentLanguage;
        }
        if (documentLanguageInput) {
            documentLanguageInput.value = currentLanguage;
        }

        root.querySelectorAll("[data-i18n]").forEach((element) => {
            const key = element.dataset.i18n;
            element.textContent = t(key);
        });

        root.querySelectorAll("[data-i18n-placeholder]").forEach((element) => {
            const key = element.dataset.i18nPlaceholder;
            element.setAttribute("placeholder", t(key));
        });

        root.querySelectorAll("[data-service-label]").forEach((element) => {
            const key = element.dataset.serviceLabel;
            element.textContent = serviceLabels[currentLanguage]?.[key] || serviceLabels.pt[key] || element.textContent;
        });

        if (activeFileName) {
            titleHeader.textContent = `${t("editing_title")}: ${activeFileName}.xlsx`;
        } else {
            titleHeader.textContent = t("ready_title");
        }
    };

    const signaturePads = [
        {
            label: "Assinatura Cliente",
            canvas: document.getElementById("signature-client-canvas"),
            input: document.getElementById("signature-client-input"),
            clearButton: document.querySelector('[data-signature-clear="Assinatura Cliente"]')
        },
    ];

    const createEmptyDocument = () => ({
        service_number: "",
        customer_name: "",
        requested_by: "",
        request_date: "",
        customer_number: "",
        nif_number: "",
        vat_number: "",
        customer_email: "",
        customer_phone: "",
        site_contact: "",
        site_phone: "",
        local_store: "",
        contract_number: "",
        address: "",
        store_number: "",
        service_types: {},
        equipments: {},
        requested_tasks: "",
        intervention_report: "",
        materials: [{ ref: "", description: "", qty: "" }],
        technician_records: [{ technician: "", start_time: "", end_time: "", total_hours: "", date: "" }],
        document_language: "pt",
        customer_signature_date: ""
    });

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
            context.strokeStyle = "#10263e";
            context.lineWidth = 3.2;
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
                pad.input.value = getCroppedSignatureDataUrl(pad.canvas) || dataUrl;
            }
        };
        image.src = dataUrl;
    };

    const getCroppedSignatureDataUrl = (canvas) => {
        const context = canvas.getContext("2d");
        if (!context || !canvas.width || !canvas.height) {
            return "";
        }

        const imageData = context.getImageData(0, 0, canvas.width, canvas.height);
        const pixels = imageData.data;
        let minX = canvas.width;
        let minY = canvas.height;
        let maxX = -1;
        let maxY = -1;

        for (let y = 0; y < canvas.height; y += 1) {
            for (let x = 0; x < canvas.width; x += 1) {
                const alpha = pixels[((y * canvas.width) + x) * 4 + 3];
                if (alpha <= 16) {
                    continue;
                }

                minX = Math.min(minX, x);
                minY = Math.min(minY, y);
                maxX = Math.max(maxX, x);
                maxY = Math.max(maxY, y);
            }
        }

        if (maxX < minX || maxY < minY) {
            return "";
        }

        const padding = Math.max(12, Math.round(Math.min(canvas.width, canvas.height) * 0.08));
        const sourceX = Math.max(minX - padding, 0);
        const sourceY = Math.max(minY - padding, 0);
        const sourceWidth = Math.min(maxX + padding, canvas.width - 1) - sourceX + 1;
        const sourceHeight = Math.min(maxY + padding, canvas.height - 1) - sourceY + 1;

        const outputCanvas = document.createElement("canvas");
        outputCanvas.width = sourceWidth;
        outputCanvas.height = sourceHeight;

        const outputContext = outputCanvas.getContext("2d");
        if (!outputContext) {
            return "";
        }

        outputContext.drawImage(
            canvas,
            sourceX,
            sourceY,
            sourceWidth,
            sourceHeight,
            0,
            0,
            sourceWidth,
            sourceHeight
        );

        return outputCanvas.toDataURL("image/png");
    };

    const syncSignatureValue = (pad) => {
        if (!pad.canvas || !pad.input) {
            return;
        }

        pad.input.value = pad.isEmpty ? "" : getCroppedSignatureDataUrl(pad.canvas);
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
            y: event.clientY - rect.top
        };
    };

    const resetConfirmButton = () => {
        confirmAccept.textContent = "Confirmar";
        confirmAccept.className = "btn btn-primary";
    };

    const hideConfirm = () => {
        pendingConfirmation = null;
        if (confirmInternalNotes) {
            confirmInternalNotes.value = "";
        }
        if (confirmInternalNotesField) {
            confirmInternalNotesField.hidden = true;
        }
        confirmPanel.hidden = true;
        resetConfirmButton();
    };

    const showConfirm = ({
        eyebrow = "Confirmar ação",
        title = "Confirmar ação",
        message = "Tem a certeza que pretende continuar?",
        confirmLabel = "Confirmar",
        confirmVariant = "primary",
        showInternalNotes = false,
        onAccept = null
    }) => {
        pendingConfirmation = typeof onAccept === "function" ? onAccept : null;
        confirmEyebrow.textContent = eyebrow;
        confirmTitle.textContent = title;
        confirmMessage.textContent = message;
        if (confirmInternalNotes) {
            confirmInternalNotes.value = "";
        }
        if (confirmInternalNotesField) {
            confirmInternalNotesField.hidden = !showInternalNotes;
        }
        confirmAccept.textContent = confirmLabel;
        confirmAccept.className = `btn btn-${confirmVariant}`;
        confirmPanel.hidden = false;
        confirmPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
        (showInternalNotes ? confirmInternalNotes : confirmAccept)?.focus();
    };

    const setBusy = (
        busy,
        title = "A guardar a folha",
        message = "Estamos a atualizar os dados da folha. Pode demorar alguns segundos."
    ) => {
        [
            btnPreviewDocument,
            btnExportPdf,
            btnCancelEdit,
            btnSaveDraft,
            btnSaveSend,
            btnCancelFile,
            btnAddMaterial,
            btnAddTechnician,
            languageSelect,
            confirmInternalNotes,
            confirmCancel,
            confirmAccept
        ].forEach((button) => {
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

    const setSidebarOpen = (open) => {
        const shouldOpen = Boolean(open && activeFileName);

        sidebar?.classList.toggle("is-open", shouldOpen);
        document.body.classList.toggle("sidebar-open", shouldOpen);

        if (sidebarScrim) {
            sidebarScrim.hidden = !shouldOpen;
        }

        toggleSidebarBtn?.setAttribute("aria-expanded", String(shouldOpen));
    };

    const syncMobileNavigation = () => {
        const hasActiveFile = Boolean(activeFileName);
        document.body.classList.toggle("has-active-file", hasActiveFile);

        if (!hasActiveFile) {
            setSidebarOpen(false);
        }
    };

    const updateWorkspaceVisibility = (hasActiveFile) => {
        formWrapper.hidden = !hasActiveFile;
        actionButtons.hidden = !hasActiveFile;
        syncMobileNavigation();
        if (!hasActiveFile) {
            hideConfirm();
        }
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

    const createMaterialRow = (row = {}) => {
        const element = materialTemplate.content.firstElementChild.cloneNode(true);
        element.querySelector('[data-repeat-field="ref"]').value = row.ref || "";
        element.querySelector('[data-repeat-field="description"]').value = row.description || "";
        element.querySelector('[data-repeat-field="qty"]').value = row.qty ?? "";
        element.querySelector("[data-row-remove]").addEventListener("click", () => {
            if (materialsList.children.length === 1) {
                materialsList.innerHTML = "";
                materialsList.appendChild(createMaterialRow());
                return;
            }
            element.remove();
        });
        return element;
    };

    const formatDurationMinutes = (totalMinutes) => {
        if (!Number.isFinite(totalMinutes)) {
            return "";
        }

        const normalizedMinutes = Math.max(Math.round(totalMinutes), 0);
        const hours = Math.floor(normalizedMinutes / 60);
        const minutes = normalizedMinutes % 60;

        if (hours && minutes) {
            return `${hours} h ${String(minutes).padStart(2, "0")} min`;
        }

        if (hours) {
            return `${hours} h`;
        }

        return `${minutes} min`;
    };

    const normalizeTotalHoursValue = (value) => {
        const raw = String(value ?? "").trim();
        if (!raw) {
            return "";
        }

        if (raw.includes("h") || raw.toLowerCase().includes("min") || raw.includes(":")) {
            return raw;
        }

        const decimalHours = Number(raw.replace(",", "."));
        if (!Number.isFinite(decimalHours)) {
            return raw;
        }

        return formatDurationMinutes(decimalHours * 60);
    };

    const calculateTotalHours = (startValue, endValue) => {
        if (!startValue || !endValue) {
            return "";
        }

        const [startHour, startMinute] = startValue.split(":").map(Number);
        const [endHour, endMinute] = endValue.split(":").map(Number);
        if (Number.isNaN(startHour) || Number.isNaN(startMinute) || Number.isNaN(endHour) || Number.isNaN(endMinute)) {
            return "";
        }

        let startTotal = (startHour * 60) + startMinute;
        let endTotal = (endHour * 60) + endMinute;
        if (endTotal < startTotal) {
            endTotal += 24 * 60;
        }

        return formatDurationMinutes(endTotal - startTotal);
    };

    const bindTechnicianRow = (element) => {
        const startInput = element.querySelector('[data-repeat-field="start_time"]');
        const endInput = element.querySelector('[data-repeat-field="end_time"]');
        const totalInput = element.querySelector('[data-repeat-field="total_hours"]');

        const syncTotal = () => {
            const total = calculateTotalHours(startInput.value, endInput.value);
            totalInput.value = total;
        };

        startInput.addEventListener("change", syncTotal);
        endInput.addEventListener("change", syncTotal);

        element.querySelector("[data-row-remove]").addEventListener("click", () => {
            if (techniciansList.children.length === 1) {
                techniciansList.innerHTML = "";
                techniciansList.appendChild(createTechnicianRow());
                updateTechnicianControls();
                return;
            }
            element.remove();
            updateTechnicianControls();
        });
    };

    const createTechnicianRow = (row = {}) => {
        const element = technicianTemplate.content.firstElementChild.cloneNode(true);
        const technicianSelect = element.querySelector('[data-repeat-field="technician"]');
        if (row.technician) {
            const hasOption = Array.from(technicianSelect.options).some((option) => option.value === row.technician);
            if (!hasOption) {
                const extraOption = document.createElement("option");
                extraOption.value = row.technician;
                extraOption.textContent = row.technician;
                technicianSelect.appendChild(extraOption);
            }
        }
        technicianSelect.value = row.technician || "";
        element.querySelector('[data-repeat-field="start_time"]').value = row.start_time || "";
        element.querySelector('[data-repeat-field="end_time"]').value = row.end_time || "";
        element.querySelector('[data-repeat-field="total_hours"]').value = normalizeTotalHoursValue(row.total_hours);
        element.querySelector('[data-repeat-field="date"]').value = row.date || "";
        bindTechnicianRow(element);
        return element;
    };

    const renderMaterials = (rows = []) => {
        materialsList.innerHTML = "";
        const safeRows = Array.isArray(rows) && rows.length > 0 ? rows : [{ ref: "", description: "", qty: "" }];
        safeRows.forEach((row) => materialsList.appendChild(createMaterialRow(row)));
    };

    const updateTechnicianControls = () => {
        if (btnAddTechnician) {
            btnAddTechnician.disabled = techniciansList.children.length >= MAX_TECHNICIANS;
        }
    };

    const renderTechnicians = (rows = []) => {
        techniciansList.innerHTML = "";
        const safeRows = Array.isArray(rows) && rows.length > 0
            ? rows.slice(0, MAX_TECHNICIANS)
            : [{ technician: "", start_time: "", end_time: "", total_hours: "", date: "" }];

        safeRows.forEach((row) => techniciansList.appendChild(createTechnicianRow(row)));
        updateTechnicianControls();
    };

    const applyOptionGroup = (inputs, values = {}) => {
        inputs.forEach((input) => {
            input.checked = values[input.dataset.option] === true;
        });
    };

    const populateForm = (fileName, documentData, signatures = {}) => {
        const safeDocument = { ...createEmptyDocument(), ...(documentData || {}) };
        activeFileName = fileName;

        simpleFields.forEach((field) => {
            const key = field.dataset.field;
            const mirrorKey = field.dataset.mirrorField;
            field.value = safeDocument[key] || (mirrorKey ? safeDocument[mirrorKey] : "") || "";
        });

        applyOptionGroup(serviceTypeInputs, safeDocument.service_types || {});
        applyOptionGroup(equipmentInputs, safeDocument.equipments || {});
        renderMaterials(safeDocument.materials);
        renderTechnicians(safeDocument.technician_records);
        applySignatures(signatures);
        applyLanguage(safeDocument.document_language || "pt");

        setActiveCard(fileName);
        setStatusMessage("");
        updateWorkspaceVisibility(true);

        if (window.innerWidth < 1280) {
            formWrapper.scrollIntoView({ behavior: "smooth", block: "start" });
        }
    };

    const resetActiveState = () => {
        activeFileName = null;
        form.reset();
        renderMaterials();
        renderTechnicians();
        applySignatures({});
        applyLanguage("pt");
        updateWorkspaceVisibility(false);
        setStatusMessage("");
        setActiveCard(null);
    };

    const collectOptionGroup = (inputs) => {
        const values = {};
        inputs.forEach((input) => {
            values[input.dataset.option] = input.checked;
        });
        return values;
    };

    const collectRepeatRows = (container, fields) => {
        return Array.from(container.querySelectorAll(".repeat-row")).map((row) => {
            const record = {};
            fields.forEach((field) => {
                const element = row.querySelector(`[data-repeat-field="${field}"]`);
                record[field] = element ? element.value.trim() : "";
            });
            return record;
        }).filter((record) => Object.values(record).some((value) => value !== ""));
    };

    const collectFormData = () => {
        const payload = createEmptyDocument();

        simpleFields.forEach((field) => {
            const value = field.value.trim();
            payload[field.dataset.field] = value;
            if (field.dataset.mirrorField) {
                payload[field.dataset.mirrorField] = value;
            }
        });

        payload.service_types = collectOptionGroup(serviceTypeInputs);
        payload.equipments = collectOptionGroup(equipmentInputs);
        payload.materials = collectRepeatRows(materialsList, ["ref", "description", "qty"]);
        payload.technician_records = collectRepeatRows(
            techniciansList,
            ["technician", "start_time", "end_time", "total_hours", "date"]
        );

        signaturePads.forEach((pad) => {
            payload[pad.label] = pad.input?.value || "";
        });

        return payload;
    };

    const getMissingRequiredFields = (payload) => {
        return requiredFields
            .filter((field) => !String(payload[field.key] || "").trim())
            .map((field) => field.label);
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

    const loadFileData = async (fileName) => {
        hideConfirm();

        try {
            const response = await fetch(`/api/file/${encodeURIComponent(fileName)}`);
            const result = await response.json();

            if (!response.ok || !result.success) {
                throw new Error(result.error || "Falha ao carregar a folha.");
            }

            populateForm(fileName, result.document || {}, result.signatures || {});
            setSidebarOpen(false);
            return true;
        } catch (error) {
            showToast(error.message, "error");
            return false;
        }
    };

    const saveDraft = async () => {
        if (!activeFileName) {
            return;
        }

        hideConfirm();
        setBusy(true, "A guardar rascunho", "Estamos a guardar a folha.");

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
        setBusy(true, "A arquivar a folha", "Estamos a mover a folha para Arquivadas.");

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
        setBusy(true, "A cancelar a folha", "Estamos a mover a folha para Canceladas.");

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

    const openDocumentPreview = async (autoPrint = false) => {
        if (!activeFileName) {
            return;
        }

        const previewWindow = window.open("", "_blank");
        if (!previewWindow) {
            showToast("O browser bloqueou a pré-visualização. Permita abrir uma nova aba.", "error");
            return;
        }

        previewWindow.document.write(`<p style="font-family:Segoe UI,sans-serif;padding:24px;">${t("preparing_document")}</p>`);

        try {
            const payload = collectFormData();
            payload._auto_print = autoPrint;
            const response = await fetch(`/api/file/${encodeURIComponent(activeFileName)}/document-preview`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });
            const result = await response.json();

            if (!response.ok || !result.success) {
                throw new Error(result.error || "Falha ao gerar a pré-visualização.");
            }

            previewWindow.document.open();
            previewWindow.document.write(result.html);
            previewWindow.document.close();
        } catch (error) {
            previewWindow.close();
            showToast(error.message, "error");
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

    fileCards.forEach((link) => {
        link.addEventListener("click", async (event) => {
            if (
                event.defaultPrevented
                || event.button !== 0
                || event.metaKey
                || event.ctrlKey
                || event.shiftKey
                || event.altKey
            ) {
                return;
            }

            event.preventDefault();
            const fileName = link.dataset.name;
            if (!fileName || fileName === activeFileName) {
                setSidebarOpen(false);
                return;
            }

            const loaded = await loadFileData(fileName);
            if (!loaded) {
                return;
            }

            const nextUrl = new URL(window.location.href);
            nextUrl.searchParams.set("file", fileName);
            window.history.pushState({ file: fileName }, "", nextUrl);
        });
    });

    window.addEventListener("popstate", async () => {
        const fileName = new URLSearchParams(window.location.search).get("file");
        if (fileName) {
            await loadFileData(fileName);
            return;
        }

        resetActiveState();
    });

    refreshBtn?.addEventListener("click", () => window.location.reload());
    toggleSidebarBtn?.addEventListener("click", () => {
        setSidebarOpen(!sidebar?.classList.contains("is-open"));
    });
    sidebarScrim?.addEventListener("click", () => setSidebarOpen(false));
    window.addEventListener("resize", syncMobileNavigation);
    languageSelect?.addEventListener("change", () => {
        applyLanguage(languageSelect.value);
    });

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
        if (event.key !== "Escape") {
            return;
        }

        if (sidebar?.classList.contains("is-open")) {
            setSidebarOpen(false);
            return;
        }

        if (!confirmPanel.hidden) {
            hideConfirm();
        }
    });

    btnAddMaterial?.addEventListener("click", () => {
        const row = createMaterialRow();
        materialsList.appendChild(row);
        applyLanguage(currentLanguage, row);
    });

    btnAddTechnician?.addEventListener("click", () => {
        if (techniciansList.children.length >= MAX_TECHNICIANS) {
            showToast(`Máximo de ${MAX_TECHNICIANS} técnicos por folha.`, "info");
            return;
        }

        const row = createTechnicianRow();
        techniciansList.appendChild(row);
        applyLanguage(currentLanguage, row);
        updateTechnicianControls();
    });

    btnPreviewDocument?.addEventListener("click", async () => {
        await openDocumentPreview(false);
    });

    btnExportPdf?.addEventListener("click", async () => {
        await openDocumentPreview(true);
    });

    btnCancelEdit?.addEventListener("click", async () => {
        if (!activeFileName) {
            return;
        }

        await loadFileData(activeFileName);
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
            message: "A folha será guardada e movida para Arquivadas.",
            confirmLabel: "Guardar e arquivar",
            confirmVariant: "success",
            showInternalNotes: true,
            onAccept: async () => {
                payload._internal_observations = confirmInternalNotes?.value.trim() || "";
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
            message: "Esta ação vai retirar a folha das ativas e movê-la para Canceladas.",
            confirmLabel: "Cancelar folha",
            confirmVariant: "danger",
            onAccept: async () => {
                await cancelFile();
            }
        });
    });

    initializeSignaturePads();
    resetActiveState();

    if (selectedFileName && selectedDocumentData) {
        populateForm(selectedFileName, selectedDocumentData, selectedSignatures);
    } else if (selectedFileError) {
        setStatusMessage(selectedFileError);
        showToast(selectedFileError, "error");
    }
});
