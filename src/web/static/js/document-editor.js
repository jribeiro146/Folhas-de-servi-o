document.addEventListener("DOMContentLoaded", () => {
    let activeFileName = null;
    let pendingConfirmation = null;

    const filesApp = window.__FILES_APP__ || {};
    const requiredFields = Array.isArray(filesApp.requiredFields) ? filesApp.requiredFields : [];
    const mailEnabled = Boolean(filesApp.mailEnabled);
    const mailTestRecipient = String(filesApp.mailTestRecipient || "").trim();
    const teamsEnabled = Boolean(filesApp.teamsEnabled);
    const selectedFileName = filesApp.selectedFileName || null;
    const selectedFileIsDraft = filesApp.selectedFileIsDraft;
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
    const btnAddPhotos = document.getElementById("btn-add-photos");
    const languageSelect = document.getElementById("document-language-select");
    const documentLanguageInput = document.getElementById("document-language");
    const workNumberInput = document.getElementById("work-number");
    const workNumberLink = document.getElementById("work-number-link");
    const workNumberHelp = document.getElementById("work-number-help");

    const materialsList = document.getElementById("materials-list");
    const techniciansList = document.getElementById("technicians-list");
    const photosInput = document.getElementById("photos-input");
    const photosList = document.getElementById("photos-list");
    const photosEmpty = document.getElementById("photos-empty");
    const photosCount = document.getElementById("photos-count");
    const materialTemplate = document.getElementById("material-row-template");
    const technicianTemplate = document.getElementById("technician-row-template");
    const materialsSection = document.getElementById("materials-section");
    const materialsPanel = document.getElementById("materials-panel");
    const materialsUsedYes = document.getElementById("materials-used-yes");
    const materialsUsedNo = document.getElementById("materials-used-no");
    const clientNotPresentInput = document.getElementById("client-not-present");
    const clientAbsenceNotice = document.getElementById("client-absence-notice");
    const signatureClientCapture = document.getElementById("signature-client-capture");
    const signatureClientCard = document.getElementById("signature-client-card");

    const simpleFields = Array.from(form.querySelectorAll("[data-field]"));
    const serviceTypeInputs = Array.from(form.querySelectorAll('[data-group="service_types"]'));
    const equipmentInputs = Array.from(form.querySelectorAll('[data-group="equipments"]'));
    let currentLanguage = "pt";
    const MAX_TECHNICIANS = 4;
    const MAX_PHOTO_BYTES = 10 * 1024 * 1024;
    const MAX_TOTAL_PHOTO_BYTES = 50 * 1024 * 1024;
    let storedPhotos = [];
    let pendingPhotos = [];
    const removedPhotoIds = new Set();

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
            work_number: "N.º de obra",
            work_number_unavailable: "Número de obra indisponível ou inválido.",
            work_number_open: "Abrir pasta da obra {number} no SharePoint",
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
            materials_question: "Foram utilizados materiais?",
            materials_help: "Registe apenas os materiais aplicados nesta intervenção.",
            yes: "Sim",
            no: "Não",
            add_line: "Adicionar linha",
            description: "Descrição",
            technicians_eyebrow: "Técnicos",
            technicians_title: "Registo de técnicos e horas",
            add_technician: "Adicionar técnico",
            photos_eyebrow: "Fotografias",
            photos_title: "Fotografias da intervenção",
            photos_internal_help: "Uso interno: não são incluídas no relatório nem enviadas ao cliente.",
            add_photos: "Adicionar fotografias",
            no_photos: "Ainda não foram adicionadas fotografias.",
            photos_limits: "fotografias · 10 MB por ficheiro · 50 MB no total",
            photo_label: "Fotografia",
            remove_photo: "Remover fotografia",
            photo_too_large: "Cada fotografia pode ter no máximo 10 MB.",
            photo_total_limit: "As fotografias podem ocupar no máximo 50 MB no total.",
            technician: "Técnico",
            start_time: "Hora Início",
            end_time: "Hora Fim",
            total_hours: "Total efetivo",
            duration_placeholder: "0 h",
            adjusted: "Ajustado",
            calculated: "Calculado",
            manual_value: "Valor manual",
            use_calculation: "Usar cálculo",
            invalid_duration: "Indique uma duração válida: 8, 8:00, 8 h ou 7 h 30 min.",
            closing_eyebrow: "Fecho",
            customer_signature_title: "Assinatura do cliente",
            clear: "Limpar",
            client_not_present: "Cliente não presente na obra",
            client_not_present_help: "A assinatura deixa de ser obrigatória e a exceção fica registada.",
            signature_waived: "Assinatura dispensada por ausência do cliente.",
            signature_help: "Assine no espaço acima com o dedo ou com uma caneta digital.",
            customer_signer_name: "Primeiro e último nome",
            customer_signer_name_placeholder: "Primeiro e último nome",
            customer_signature_date: "Cliente - data",
            save_send: "Guardar e enviar",
            cancel_sheet: "Cancelar folha",
            save_draft: "Guardar rascunho",
            select_technician: "Selecionar técnico",
            ready_title: "Selecione uma folha ativa",
            editing_title: "A editar",
            private_editing_title: "Novo rascunho privado",
            preparing_document: "A preparar documento...",
            remove_materials_eyebrow: "Materiais",
            remove_materials_title: "Remover materiais registados?",
            remove_materials_message: "As linhas preenchidas serão apagadas desta folha.",
            remove_materials_confirm: "Remover materiais",
            discard_signature_eyebrow: "Assinatura",
            discard_signature_title: "Dispensar a assinatura?",
            discard_signature_message: "A assinatura já recolhida será apagada e a ausência do cliente ficará registada.",
            discard_signature_confirm: "Dispensar assinatura"
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
            work_number: "Work no.",
            work_number_unavailable: "Work number unavailable or invalid.",
            work_number_open: "Open work folder {number} in SharePoint",
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
            materials_question: "Were materials used?",
            materials_help: "Record only the materials used in this intervention.",
            yes: "Yes",
            no: "No",
            add_line: "Add line",
            description: "Description",
            technicians_eyebrow: "Technicians",
            technicians_title: "Technician time records",
            add_technician: "Add technician",
            photos_eyebrow: "Photographs",
            photos_title: "Intervention photographs",
            photos_internal_help: "Internal use: they are not included in the report or sent to the customer.",
            add_photos: "Add photographs",
            no_photos: "No photographs have been added yet.",
            photos_limits: "photographs · 10 MB per file · 50 MB total",
            photo_label: "Photograph",
            remove_photo: "Remove photograph",
            photo_too_large: "Each photograph can be up to 10 MB.",
            photo_total_limit: "Photographs can use up to 50 MB in total.",
            technician: "Technician",
            start_time: "Start time",
            end_time: "End time",
            total_hours: "Effective total",
            duration_placeholder: "0 h",
            adjusted: "Adjusted",
            calculated: "Calculated",
            manual_value: "Manual value",
            use_calculation: "Use calculation",
            invalid_duration: "Enter a valid duration: 8, 8:00, 8 h or 7 h 30 min.",
            closing_eyebrow: "Close",
            customer_signature_title: "Customer signature",
            clear: "Clear",
            client_not_present: "Customer not present on site",
            client_not_present_help: "The signature is no longer required and the exception is recorded.",
            signature_waived: "Signature waived because the customer was absent.",
            signature_help: "Sign in the area above using a finger or digital pen.",
            customer_signer_name: "First and last name",
            customer_signer_name_placeholder: "First and last name",
            customer_signature_date: "Customer - date",
            save_send: "Save and send",
            cancel_sheet: "Cancel sheet",
            save_draft: "Save draft",
            select_technician: "Select technician",
            ready_title: "Select an active sheet",
            editing_title: "Editing",
            private_editing_title: "New private draft",
            preparing_document: "Preparing document...",
            remove_materials_eyebrow: "Materials",
            remove_materials_title: "Remove recorded materials?",
            remove_materials_message: "The completed material rows will be deleted from this sheet.",
            remove_materials_confirm: "Remove materials",
            discard_signature_eyebrow: "Signature",
            discard_signature_title: "Waive the signature?",
            discard_signature_message: "The captured signature will be deleted and the customer absence will be recorded.",
            discard_signature_confirm: "Waive signature"
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

    const normalizeWorkNumber = (value) => {
        const text = String(value ?? "").trim();
        return /^\d{1,4}$/.test(text) ? text.padStart(4, "0") : "";
    };

    const renderWorkNumberLink = () => {
        if (!workNumberInput || !workNumberLink || !workNumberHelp) {
            return;
        }

        const number = normalizeWorkNumber(workNumberInput.value);
        workNumberInput.value = number;

        if (number) {
            workNumberLink.textContent = number;
            workNumberLink.href = `/work-folder/${encodeURIComponent(number)}`;
            workNumberLink.setAttribute("aria-disabled", "false");
            workNumberLink.setAttribute(
                "aria-label",
                t("work_number_open").replace("{number}", number)
            );
            workNumberLink.tabIndex = 0;
            workNumberHelp.hidden = true;
            return;
        }

        workNumberLink.textContent = "----";
        workNumberLink.removeAttribute("href");
        workNumberLink.setAttribute("aria-disabled", "true");
        workNumberLink.setAttribute("aria-label", t("work_number_unavailable"));
        workNumberLink.tabIndex = -1;
        workNumberHelp.hidden = false;
    };

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

        const durationRows = [
            ...(root.matches?.('[data-repeat="technician_records"]') ? [root] : []),
            ...root.querySelectorAll('[data-repeat="technician_records"]')
        ];
        durationRows.forEach((row) => updateTechnicianDurationMeta(row));
        if (root === document) {
            renderPhotos();
            renderWorkNumberLink();
        }

        if (activeFileName) {
            const titleKey = selectedFileIsDraft === false
                ? "private_editing_title"
                : "editing_title";
            titleHeader.textContent = `${t(titleKey)}: ${activeFileName}.xlsx`;
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
        work_number: "",
        contract_number: "",
        address: "",
        store_number: "",
        service_types: {},
        equipments: {},
        requested_tasks: "",
        intervention_report: "",
        materials_used: false,
        materials: [{ ref: "", description: "", qty: "" }],
        technician_records: [{
            technician: "",
            start_time: "",
            end_time: "",
            total_hours: "",
            total_hours_overridden: false,
            date: ""
        }],
        document_language: "pt",
        customer_signer_name: "",
        customer_signature_date: "",
        client_not_present: false
    });

    const showToast = (message, variant = "info") => {
        const toast = document.createElement("div");
        toast.className = `toast toast-${variant}`;
        toast.textContent = message;
        toastZone.appendChild(toast);
        window.setTimeout(() => toast.remove(), 3500);
    };

    const formatPhotoSize = (size) => {
        const bytes = Math.max(Number(size || 0), 0);
        if (bytes < 1024 * 1024) {
            return `${Math.max(bytes / 1024, 0.1).toFixed(1)} KB`;
        }
        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    };

    const releasePendingPhotoUrls = () => {
        pendingPhotos.forEach((photo) => {
            if (photo.url) {
                URL.revokeObjectURL(photo.url);
            }
        });
    };

    const visibleStoredPhotos = () => (
        storedPhotos.filter((photo) => !removedPhotoIds.has(String(photo.id || "")))
    );

    const hasPendingPhotoChanges = () => (
        pendingPhotos.length > 0 || removedPhotoIds.size > 0
    );

    const renderPhotos = () => {
        if (!photosList || !photosEmpty || !photosCount) {
            return;
        }

        const entries = [
            ...visibleStoredPhotos().map((photo) => ({ kind: "stored", ...photo })),
            ...pendingPhotos.map((photo) => ({
                kind: "pending",
                filename: photo.file.name,
                content_type: photo.file.type,
                size: photo.file.size,
                url: photo.url,
                key: photo.key
            }))
        ];
        const readOnly = form.classList.contains("is-readonly");
        photosList.replaceChildren();

        entries.forEach((entry, index) => {
            const card = document.createElement("article");
            card.className = "photo-card";

            const preview = document.createElement("div");
            preview.className = "photo-preview";
            const fallback = document.createElement("span");
            fallback.className = "photo-format-fallback";
            const extension = String(entry.filename || "").split(".").pop() || "IMG";
            fallback.textContent = extension.toUpperCase();
            fallback.hidden = Boolean(entry.url);

            if (entry.url) {
                const image = document.createElement("img");
                image.src = entry.url;
                image.alt = `${t("photo_label")} ${index + 1}`;
                image.loading = "lazy";
                image.addEventListener("error", () => {
                    image.hidden = true;
                    fallback.hidden = false;
                }, { once: true });
                preview.appendChild(image);
            }
            preview.appendChild(fallback);

            const details = document.createElement("div");
            details.className = "photo-card-details";
            const title = document.createElement("strong");
            title.textContent = `${t("photo_label")} ${index + 1}`;
            const metadata = document.createElement("small");
            metadata.textContent = `${extension.toUpperCase()} · ${formatPhotoSize(entry.size)}`;
            details.append(title, metadata);

            const removeButton = document.createElement("button");
            removeButton.className = "btn btn-secondary btn-icon photo-remove";
            removeButton.type = "button";
            removeButton.textContent = "×";
            removeButton.title = t("remove_photo");
            removeButton.setAttribute("aria-label", `${t("remove_photo")} ${index + 1}`);
            removeButton.disabled = readOnly;
            removeButton.addEventListener("click", () => {
                if (entry.kind === "stored") {
                    removedPhotoIds.add(String(entry.id));
                } else {
                    const pendingIndex = pendingPhotos.findIndex(
                        (photo) => photo.key === entry.key
                    );
                    if (pendingIndex >= 0) {
                        const [removed] = pendingPhotos.splice(pendingIndex, 1);
                        if (removed.url) {
                            URL.revokeObjectURL(removed.url);
                        }
                    }
                }
                renderPhotos();
                window.__EDITING_COORDINATOR__?.markAttachmentsDirty?.();
            });

            card.append(preview, details, removeButton);
            photosList.appendChild(card);
        });

        photosEmpty.hidden = entries.length > 0;
        photosList.hidden = entries.length === 0;
        photosCount.textContent = String(entries.length);
        if (btnAddPhotos) {
            btnAddPhotos.disabled = readOnly;
        }
    };

    const resetPhotoState = (photos = []) => {
        releasePendingPhotoUrls();
        pendingPhotos = [];
        removedPhotoIds.clear();
        storedPhotos = Array.isArray(photos)
            ? photos.filter((photo) => photo && photo.id && photo.url)
            : [];
        renderPhotos();
    };

    const addPhotoFiles = (files) => {
        const selected = Array.from(files || []).filter((file) => file && file.size > 0);
        if (!selected.length) {
            return;
        }

        const fingerprints = new Set(pendingPhotos.map((photo) => photo.key));
        let totalBytes = visibleStoredPhotos().reduce(
            (total, photo) => total + Number(photo.size || 0),
            0
        ) + pendingPhotos.reduce((total, photo) => total + photo.file.size, 0);
        let changed = false;

        for (const file of selected) {
            if (file.size > MAX_PHOTO_BYTES) {
                showToast(t("photo_too_large"), "error");
                continue;
            }
            if (totalBytes + file.size > MAX_TOTAL_PHOTO_BYTES) {
                showToast(t("photo_total_limit"), "error");
                break;
            }

            const key = `${file.name}:${file.size}:${file.lastModified}`;
            if (fingerprints.has(key)) {
                continue;
            }
            fingerprints.add(key);
            pendingPhotos.push({
                file,
                key,
                url: URL.createObjectURL(file)
            });
            totalBytes += file.size;
            changed = true;
        }

        if (changed) {
            renderPhotos();
            window.__EDITING_COORDINATOR__?.markAttachmentsDirty?.();
        }
    };

    const buildCommitRequest = (payload) => {
        if (!hasPendingPhotoChanges()) {
            return {
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            };
        }

        const formData = new FormData();
        formData.append("document", JSON.stringify(payload));
        formData.append("removed_photo_ids", JSON.stringify(Array.from(removedPhotoIds)));
        pendingPhotos.forEach((photo) => {
            formData.append("photos", photo.file, photo.file.name);
        });
        return { body: formData };
    };

    const commitPhotoState = (photos = []) => {
        resetPhotoState(Array.isArray(photos) ? photos : []);
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
            btnAddPhotos,
            photosInput,
            ...form.querySelectorAll(".photo-remove"),
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

    const materialRowsHaveValues = () => (
        Array.from(materialsList.querySelectorAll(".repeat-row")).some((row) => (
            Array.from(row.querySelectorAll("[data-repeat-field]")).some((field) => field.value.trim())
        ))
    );

    const setMaterialsUsed = (used, { clearRows = false, focusFirst = false } = {}) => {
        const shouldShow = Boolean(used);
        materialsUsedYes.checked = shouldShow;
        materialsUsedNo.checked = !shouldShow;
        materialsUsedYes.setAttribute("aria-expanded", String(shouldShow));
        materialsPanel.hidden = !shouldShow;
        materialsSection.classList.toggle("is-expanded", shouldShow);

        if (!shouldShow && clearRows) {
            renderMaterials();
        }
        if (shouldShow && focusFirst) {
            window.requestAnimationFrame(() => {
                materialsList.querySelector('[data-repeat-field="ref"]')?.focus();
            });
        }
    };

    const formatDurationMinutes = (totalMinutes) => {
        if (!Number.isFinite(totalMinutes)) {
            return "";
        }
        const normalizedMinutes = Math.max(Math.round(totalMinutes), 0);
        const hours = Math.floor(normalizedMinutes / 60);
        const minutes = normalizedMinutes % 60;
        if (hours && minutes) {
            return hours + " h " + String(minutes).padStart(2, "0") + " min";
        }
        if (hours) {
            return hours + " h";
        }
        return minutes + " min";
    };

    const parseDurationMinutes = (value) => {
        const raw = String(value ?? "").trim().toLowerCase().replace(",", ".");
        if (!raw) {
            return null;
        }
        const clockMatch = raw.match(/^(\d{1,3}):([0-5]\d)$/);
        if (clockMatch) {
            return (Number(clockMatch[1]) * 60) + Number(clockMatch[2]);
        }
        if (/^\d+(?:\.\d+)?$/.test(raw)) {
            return Math.round(Number(raw) * 60);
        }
        const hourMatch = raw.match(/(\d+(?:\.\d+)?)\s*h/);
        const minuteMatch = raw.match(/(\d+)\s*(?:min|m)/);
        const remainder = raw
            .replace(/\d+(?:\.\d+)?\s*h/, "")
            .replace(/\d+\s*(?:min|m)/, "")
            .trim();
        const explicitMinutes = Number(minuteMatch?.[1] || 0);
        if ((!hourMatch && !minuteMatch) || remainder || (hourMatch && explicitMinutes >= 60)) {
            return null;
        }
        return Math.round((Number(hourMatch?.[1] || 0) * 60) + explicitMinutes);
    };

    const normalizeTotalHoursValue = (value) => {
        const raw = String(value ?? "").trim();
        if (!raw) {
            return "";
        }
        const totalMinutes = parseDurationMinutes(raw);
        return totalMinutes === null ? raw : formatDurationMinutes(totalMinutes);
    };

    const calculateTotalHours = (startValue, endValue) => {
        if (!startValue || !endValue) {
            return "";
        }
        const [startHour, startMinute] = startValue.split(":").map(Number);
        const [endHour, endMinute] = endValue.split(":").map(Number);
        if ([startHour, startMinute, endHour, endMinute].some(Number.isNaN)) {
            return "";
        }
        const startTotal = (startHour * 60) + startMinute;
        let endTotal = (endHour * 60) + endMinute;
        if (endTotal < startTotal) {
            endTotal += 24 * 60;
        }
        return formatDurationMinutes(endTotal - startTotal);
    };

    const validateDurationInput = (input) => {
        const raw = input.value.trim();
        const valid = !raw || parseDurationMinutes(raw) !== null;
        input.setCustomValidity(valid ? "" : t("invalid_duration"));
        input.setAttribute("aria-invalid", String(!valid));
        return valid;
    };

    const updateTechnicianDurationMeta = (element) => {
        const durationField = element.querySelector(".duration-field");
        const meta = element.querySelector("[data-duration-meta]");
        const calculatedCopy = element.querySelector("[data-duration-calculated]");
        const resetButton = element.querySelector("[data-duration-reset]");
        if (!durationField || !meta || !calculatedCopy || !resetButton) {
            return;
        }
        const overridden = element.dataset.totalHoursOverridden === "true";
        const calculated = calculateTotalHours(
            element.querySelector('[data-repeat-field="start_time"]').value,
            element.querySelector('[data-repeat-field="end_time"]').value
        );
        durationField.classList.toggle("is-overridden", overridden);
        meta.hidden = !overridden;
        calculatedCopy.textContent = calculated ? t("calculated") + ": " + calculated : t("manual_value");
        resetButton.hidden = !calculated;
    };

    const bindTechnicianRow = (element) => {
        const startInput = element.querySelector('[data-repeat-field="start_time"]');
        const endInput = element.querySelector('[data-repeat-field="end_time"]');
        const totalInput = element.querySelector('[data-repeat-field="total_hours"]');
        const resetButton = element.querySelector("[data-duration-reset]");

        const syncTotal = () => {
            if (element.dataset.totalHoursOverridden !== "true") {
                totalInput.value = calculateTotalHours(startInput.value, endInput.value);
            }
            validateDurationInput(totalInput);
            updateTechnicianDurationMeta(element);
        };

        startInput.addEventListener("change", syncTotal);
        endInput.addEventListener("change", syncTotal);
        totalInput.addEventListener("input", () => {
            element.dataset.totalHoursOverridden = totalInput.value.trim() ? "true" : "false";
            if (!totalInput.value.trim()) {
                syncTotal();
                return;
            }
            validateDurationInput(totalInput);
            updateTechnicianDurationMeta(element);
        });
        totalInput.addEventListener("blur", () => {
            if (validateDurationInput(totalInput)) {
                totalInput.value = normalizeTotalHoursValue(totalInput.value);
            }
            updateTechnicianDurationMeta(element);
        });
        resetButton.addEventListener("click", () => {
            element.dataset.totalHoursOverridden = "false";
            syncTotal();
            totalInput.focus();
        });

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

        validateDurationInput(totalInput);
        updateTechnicianDurationMeta(element);
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
        element.dataset.totalHoursOverridden = row.total_hours_overridden === true ? "true" : "false";
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
            : [{
                technician: "",
                start_time: "",
                end_time: "",
                total_hours: "",
                total_hours_overridden: false,
                date: ""
            }];

        safeRows.forEach((row) => techniciansList.appendChild(createTechnicianRow(row)));
        updateTechnicianControls();
    };

    const applyOptionGroup = (inputs, values = {}) => {
        inputs.forEach((input) => {
            input.checked = values[input.dataset.option] === true;
        });
    };

    const setClientNotPresent = (notPresent, { clearSignature = false } = {}) => {
        const shouldWaive = Boolean(notPresent);
        const clientPad = signaturePads[0];
        if (shouldWaive && clearSignature) {
            clearSignaturePad(clientPad);
        }
        clientNotPresentInput.checked = shouldWaive;
        clientAbsenceNotice.hidden = !shouldWaive;
        signatureClientCapture.hidden = shouldWaive;
        signatureClientCard.classList.toggle("is-client-absent", shouldWaive);
        clientPad.clearButton.hidden = shouldWaive;
        clientPad.clearButton.disabled = shouldWaive;
        clientPad.canvas.setAttribute("aria-disabled", String(shouldWaive));
    };

    const populateForm = (fileName, documentData, signatures = {}, photos = []) => {
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
        const hasSavedMaterials = Array.isArray(safeDocument.materials)
            && safeDocument.materials.some((row) => Object.values(row || {}).some((value) => String(value || "").trim()));
        setMaterialsUsed(Boolean(safeDocument.materials_used || hasSavedMaterials));
        renderTechnicians(safeDocument.technician_records);
        applySignatures(signatures);
        resetPhotoState(photos);
        setClientNotPresent(Boolean(safeDocument.client_not_present), {
            clearSignature: Boolean(safeDocument.client_not_present)
        });
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
        setMaterialsUsed(false);
        renderTechnicians();
        applySignatures({});
        resetPhotoState();
        setClientNotPresent(false);
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

    const collectTechnicianRows = () => (
        Array.from(techniciansList.querySelectorAll(".repeat-row")).map((row) => {
            const record = {};
            ["technician", "start_time", "end_time", "total_hours", "date"].forEach((field) => {
                const element = row.querySelector('[data-repeat-field="' + field + '"]');
                record[field] = element ? element.value.trim() : "";
            });
            record.total_hours_overridden = row.dataset.totalHoursOverridden === "true";
            return record;
        }).filter((record) => (
            ["technician", "start_time", "end_time", "total_hours", "date"].some((field) => record[field] !== "")
        ))
    );

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
        payload.materials_used = materialsUsedYes.checked;
        payload.materials = payload.materials_used
            ? collectRepeatRows(materialsList, ["ref", "description", "qty"])
            : [];
        payload.technician_records = collectTechnicianRows();
        payload.client_not_present = clientNotPresentInput.checked;

        signaturePads.forEach((pad) => {
            payload[pad.label] = payload.client_not_present ? "" : (pad.input?.value || "");
        });

        return payload;
    };

    const getMissingRequiredFields = (payload) => {
        const missing = requiredFields
            .filter((field) => !String(payload[field.key] || "").trim())
            .map((field) => field.label);
        if (!payload.client_not_present && !String(payload["Assinatura Cliente"] || "").trim()) {
            missing.push(t("customer_signature_title"));
        }
        return missing;
    };

    const getInvalidDurationInputs = () => (
        Array.from(techniciansList.querySelectorAll('[data-repeat-field="total_hours"]'))
            .filter((input) => !validateDurationInput(input))
    );

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
                if (formWrapper.hidden || clientNotPresentInput.checked) {
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


    const saveDraft = async () => {
        if (!activeFileName) {
            return;
        }

        hideConfirm();
        setBusy(true, "A guardar rascunho", "Estamos a guardar a folha.");

        try {
            const payload = collectFormData();
            payload._edit = window.__EDITING_COORDINATOR__?.operationMetadata("draft") || {};
            const requestOptions = buildCommitRequest(payload);
            const response = await fetch(`/api/file/${encodeURIComponent(activeFileName)}/draft`, {
                method: "POST",
                ...requestOptions
            });
            const result = await response.json();

            if (!response.ok || !result.success) {
                window.__EDITING_COORDINATOR__?.handleConflict(result);
                throw new Error(result.error || "Falha ao guardar rascunho.");
            }

            commitPhotoState(result.photos || []);
            await window.__EDITING_COORDINATOR__?.markCommitted(result, "draft");
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
        setBusy(
            true,
            "A guardar e enviar",
            mailEnabled
                ? "Estamos a arquivar a folha e a preparar o envio por e-mail."
                : "Estamos a mover a folha para Arquivadas."
        );

        try {
            payload._edit = window.__EDITING_COORDINATOR__?.operationMetadata("send") || {};
            const requestOptions = buildCommitRequest(payload);
            const response = await fetch(`/api/file/${encodeURIComponent(activeFileName)}/send`, {
                method: "POST",
                ...requestOptions
            });
            const result = await response.json();

            if (!response.ok || !result.success) {
                window.__EDITING_COORDINATOR__?.handleConflict(result);
                const fields = [
                    ...(result.missing_fields || []),
                    ...(result.invalid_fields || [])
                ];
                const suffix = fields.length ? " (" + fields.join(", ") + ")" : "";
                throw new Error((result.error || "Falha ao fechar a folha.") + suffix);
            }

            commitPhotoState();
            await window.__EDITING_COORDINATOR__?.markCommitted(result, "send");
            showToast(result.message || "Folha concluída com sucesso.", "success");
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
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    _edit: window.__EDITING_COORDINATOR__?.operationMetadata("cancel") || {}
                })
            });
            const result = await response.json();

            if (!response.ok || !result.success) {
                window.__EDITING_COORDINATOR__?.handleConflict(result);
                throw new Error(result.error || "Falha ao cancelar a folha.");
            }

            commitPhotoState();
            await window.__EDITING_COORDINATOR__?.markCommitted(result, "cancel");
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


    const refreshFileList = async () => {
        if (!refreshBtn || refreshBtn.disabled) return;
        refreshBtn.disabled = true;
        showToast("A atualizar a lista em segundo plano…", "info");
        try {
            const response = await fetch("/api/files?refresh=1", {
                headers: { "Accept": "application/json" },
            });
            const result = await response.json();
            if (!response.ok || !result.success) {
                throw new Error(result.error || "N??o foi poss??vel atualizar a lista.");
            }
            if (result.refresh) {
                let refresh = result.refresh;
                for (let attempt = 0; refresh?.in_progress && attempt < 40; attempt += 1) {
                    await new Promise((resolve) => window.setTimeout(resolve, 500));
                    const statusResponse = await fetch("/api/graph/status", {
                        headers: { "Accept": "application/json" },
                    });
                    const statusResult = await statusResponse.json();
                    refresh = statusResult.refresh;
                }
                if (refresh?.last_error) throw new Error(refresh.last_error);
            }
            if (activeFileName) {
                showToast("Lista atualizada. A edição atual foi mantida.", "success");
            } else {
                window.location.reload();
            }
        } catch (error) {
            showToast(error.message, "error");
        } finally {
            refreshBtn.disabled = false;
        }
    };

    refreshBtn?.addEventListener("click", refreshFileList);
    toggleSidebarBtn?.addEventListener("click", () => {
        setSidebarOpen(!sidebar?.classList.contains("is-open"));
    });
    sidebarScrim?.addEventListener("click", () => setSidebarOpen(false));
    window.addEventListener("resize", syncMobileNavigation);
    workNumberLink?.addEventListener("click", (event) => {
        if (workNumberLink.getAttribute("aria-disabled") === "true") {
            event.preventDefault();
        }
    });
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

    materialsUsedYes?.addEventListener("change", () => {
        if (materialsUsedYes.checked) {
            setMaterialsUsed(true, { focusFirst: true });
        }
    });

    materialsUsedNo?.addEventListener("change", () => {
        if (!materialsUsedNo.checked) {
            return;
        }
        if (!materialRowsHaveValues()) {
            setMaterialsUsed(false, { clearRows: true });
            return;
        }
        setMaterialsUsed(true);
        showConfirm({
            eyebrow: t("remove_materials_eyebrow"),
            title: t("remove_materials_title"),
            message: t("remove_materials_message"),
            confirmLabel: t("remove_materials_confirm"),
            confirmVariant: "danger",
            onAccept: async () => {
                hideConfirm();
                setMaterialsUsed(false, { clearRows: true });
            }
        });
    });

    clientNotPresentInput?.addEventListener("change", () => {
        if (!clientNotPresentInput.checked) {
            setClientNotPresent(false);
            return;
        }
        const hasSignature = Boolean(signaturePads[0].input?.value);
        if (!hasSignature) {
            setClientNotPresent(true);
            return;
        }
        setClientNotPresent(false);
        showConfirm({
            eyebrow: t("discard_signature_eyebrow"),
            title: t("discard_signature_title"),
            message: t("discard_signature_message"),
            confirmLabel: t("discard_signature_confirm"),
            confirmVariant: "danger",
            onAccept: async () => {
                hideConfirm();
                setClientNotPresent(true, { clearSignature: true });
            }
        });
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

    btnAddPhotos?.addEventListener("click", () => {
        if (!btnAddPhotos.disabled) {
            photosInput?.click();
        }
    });

    photosInput?.addEventListener("change", () => {
        addPhotoFiles(photosInput.files);
        photosInput.value = "";
    });

    window.addEventListener("beforeunload", (event) => {
        if (!hasPendingPhotoChanges()) {
            return;
        }
        event.preventDefault();
        event.returnValue = "";
    });

    btnPreviewDocument?.addEventListener("click", async () => {
        await openDocumentPreview(false);
    });

    btnExportPdf?.addEventListener("click", async () => {
        await openDocumentPreview(true);
    });

    btnCancelEdit?.addEventListener("click", () => {
        if (!activeFileName) {
            return;
        }
        window.location.reload();
    });

    btnSaveDraft?.addEventListener("click", async () => {
        await saveDraft();
    });

    btnSaveSend?.addEventListener("click", () => {
        if (!activeFileName) {
            return;
        }

        const payload = collectFormData();
        const invalidDurationInputs = getInvalidDurationInputs();
        if (invalidDurationInputs.length > 0) {
            showToast(t("invalid_duration"), "error");
            invalidDurationInputs[0].focus();
            return;
        }

        const missingFields = getMissingRequiredFields(payload);
        if (missingFields.length > 0) {
            showToast(`Campos obrigatórios em falta: ${missingFields.join(", ")}`, "error");
            return;
        }

        showConfirm({
            eyebrow: "Finalizar folha",
            title: "Guardar e enviar",
            message: mailEnabled
                ? (
                    mailTestRecipient
                        ? `MODO DE TESTE: o PDF será enviado para ${mailTestRecipient}. O email do cliente não será utilizado.`
                        : (
                            teamsEnabled
                                ? "A folha será arquivada e enviada em PDF para o e-mail do cliente, com o técnico em CC. Depois será publicado um aviso no Teams."
                                : "A folha será arquivada e enviada em PDF para o e-mail do cliente, com o técnico em CC."
                        )
                )
                : "A folha será guardada e movida para Arquivadas.",
            confirmLabel: "Guardar e enviar",
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

    window.__FILES_EDITOR__ = {
        collectFormData,
        populateForm,
        showToast,
        hasPendingPhotoChanges,
        renderAttachments: renderPhotos,
        getActiveFileName: () => activeFileName
    };

    initializeSignaturePads();
    resetActiveState();

    if (!filesApp.bootstrapEnabled && selectedFileName && selectedDocumentData) {
        populateForm(selectedFileName, selectedDocumentData, selectedSignatures);
    } else if (selectedFileError) {
        setStatusMessage(selectedFileError);
        showToast(selectedFileError, "error");
    }

    window.__FILES_EDITOR_READY__ = true;
    document.dispatchEvent(new CustomEvent("files-editor-ready"));
});
