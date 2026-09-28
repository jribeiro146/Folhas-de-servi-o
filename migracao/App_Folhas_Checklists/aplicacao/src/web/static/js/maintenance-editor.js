/* Local SADI instances participate in the existing document save/recovery lifecycle. */
(() => {
    "use strict";
    const config = window.__FILES_APP__;
    if (!config.maintenanceEnabled) return;
    const def = config.maintenanceDefinition;
    // Jinja serializes object keys alphabetically; retain the workbook's section order.
    def.groups = Object.fromEntries(["conventional", "addressable", "repeater"].map(key => [key, def.groups[key]]));
    const model = window.MaintenanceModel;
    let sites = [], active = 0, form, panel, tabs, servicePart, host, nav, notice, validationSummary, signatureNotice;
    let loaded = false, common = "", pendingPhotos = 0, validationVisible = false;
    const signatureJobs = new Map(), signatureMessages = new Map(), invalidatedSignatures = new Map();
    const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[ch]));
    const uid = () => crypto.randomUUID();
    const documentData = () => window.__FILES_EDITOR__?.collectFormData() || {};
    const readOnly = () => form?.classList.contains("is-readonly") || document.body.classList.contains("editor-booting");
    const dirty = () => window.__EDITING_COORDINATOR__?.markDirty();
    const current = () => sites[active];
    const binding = (path, value, label, type = "text") => `<label class="mc-field">${esc(label)}<input data-mc-path="${esc(path)}" type="${type}" ${type === "number" ? 'min="0" step="1"' : ''} value="${esc(value)}"></label>`;
    const textarea = (path, value, label) => `<label class="mc-field mc-text-line">${esc(label)}<textarea data-mc-path="${esc(path)}" rows="1">${esc(value)}</textarea></label>`;
    function sizeTextLines() {
        host.querySelectorAll('textarea').forEach(input => {
            if (!input.getClientRects().length) return;
            input.style.height = 'auto';
            input.style.height = `${Math.max(44, input.scrollHeight + 2)}px`;
        });
    }
    function clearSignatures(site) {
        const hadInk = Object.keys(site.signatures || {}).length || Object.values(site.signature_drafts || {}).some(value => value.image);
        if (!hadInk) return;
        site.signature_drafts ||= {};
        for (const role of ['technician','customer']) {
            const input = signatureInput(site,role);
            if (site.signatures?.[role]?.token || input.image) {
                invalidatedSignatures.set(`${site.id}:${role}`, site.signatures?.[role]?.token ? 'Assinatura invalidada: o conteúdo do local foi alterado. Recolha e guarde uma nova assinatura.' : 'Desenho removido: o conteúdo do local foi alterado. Recolha uma nova assinatura.');
            }
            site.signature_drafts[role] = {...input,image:''};
            signatureMessages.delete(`${site.id}:${role}`);
        }
        site.signatures = {};
        renderSignatureNotice();
        if (current() === site) host.querySelectorAll('[data-mc-sign-canvas]').forEach(canvas => canvas.getContext('2d').clearRect(0,0,canvas.width,canvas.height));
    }
    function createSite(index) {
        const doc = documentData(), technician = doc.technician_records?.[0] || {};
        return {id: uid(), version: def.version, location: [doc.local_store, `Local ${index + 1}`].filter(Boolean).join(" — "),
            date: technician.date || "", technician: technician.technician || "",
            scie: "", period: "", period_other: "", observations: "", final_observations: "", photos: [], general: {},
            peripherals: {}, trials: {}, peripheral_observations: "", coverage_percent: "", coverage_areas: "", peripheral_history: [],
            configuration: {conventional: null, addressable: null, repeater: null}, conventional: [], addressable: [], repeater: [], signatures: {}};
    }
    const createUnit = () => ({id: uid(), checks: {}});
    function questions(title, list, answers, path) {
        return `<h4>${esc(title)}</h4><div class="mc-questions">${list.map(([key, label]) => {
            const response = answers?.[key] || {};
            return `<div class="mc-question"><label><span>${esc(label)}</span><select aria-label="${esc(label)}" data-mc-path="${path}.${key}.answer"><option value="">Selecionar</option>${["OK", "NC", "NA"].map(value => `<option ${response.answer === value ? 'selected' : ''}>${value}</option>`).join("")}</select></label>${response.answer === "NC" ? textarea(`${path}.${key}.justification`, response.justification, "Justificação da não conformidade (obrigatória)") : ''}</div>`;
        }).join("")}</div>`;
    }
    function unitCard(kind, unit, index) {
        const path = `${kind}.${index}`, title = `${def.groups[kind]} ${index + 1}`;
        return `<details class="mc-unit" data-mc-detail="${esc(unit.id)}"><summary>${esc(title)}${unit.location ? ` — ${esc(unit.location)}` : ''}</summary><div class="mc-unit-body"><div class="mc-grid">${def.fields[kind].map(([key, label, type]) => binding(`${path}.${key}`, unit[key], label, type)).join("")}</div>${questions("Testes e estado geral", def[kind], unit.checks, `${path}.checks`)}${textarea(`${path}.observations`, unit.observations, "Observações")}</div></details>`;
    }
    function equipmentGroup(kind, site) {
        const title = def.groups[kind];
        return `<section class="mc-group"><div class="mc-grid"><label class="mc-field">Existem ${kind === 'conventional' ? 'centrais convencionais' : kind === 'addressable' ? 'centrais endereçáveis' : 'repetidores'}?<select data-mc-config="${kind}"><option value="">Selecionar</option><option value="yes" ${site.configuration?.[kind] === true ? 'selected' : ''}>Sim</option><option value="no" ${site.configuration?.[kind] === false ? 'selected' : ''}>Não</option></select></label>${site.configuration?.[kind] ? `<label class="mc-field">Quantidade — ${title}<input type="number" min="1" step="1" data-mc-count="${kind}" value="${site[kind].length || ''}"></label>` : ''}</div>${site.configuration?.[kind] ? site[kind].map((unit,index) => unitCard(kind,unit,index)).join("") : ''}</section>`;
    }
    function peripheralHistory(site) {
        if (!site.peripheral_history?.length) return "";
        return `<details class="mc-unit" data-mc-detail="peripheral-history"><summary>Consultar respostas anteriores por central</summary><div class="mc-unit-body"><p>As respostas coincidentes das centrais ativas foram reunidas na secção comum. As respostas diferentes ficaram por preencher. Confirme os campos em falta e recolha novas assinaturas. Os registos anteriores ficam preservados neste rascunho.</p>${site.peripheral_history.map(record => `<h4>${esc(record.label)}${record.location ? ` — ${esc(record.location)}` : ''}${record.active ? '' : ' (inativa)'}</h4><ul>${[...def.peripherals.map(([key,label]) => [label,record.peripherals?.[key]]),...def.trials.map(([key,label]) => [label,record.trials?.[key]])].filter(([,answer]) => answer?.answer || answer?.justification).map(([label,answer]) => `<li>${esc(label)}: <b>${esc(answer.answer || 'Por responder')}</b>${answer.justification ? ` — ${esc(answer.justification)}` : ''}</li>`).join('')}</ul><p>Elementos testados: ${esc(record.coverage_percent || '—')}${record.coverage_percent ? '%' : ''} · Áreas: ${esc(record.coverage_areas || '—')}</p>${record.peripheral_observations ? `<p>${esc(record.peripheral_observations)}</p>` : ''}`).join('')}</div></details>`;
    }
    function peripheralsSection(site) {
        return `<section class="mc-peripherals" data-mc-section="peripherals"><h3>4. Periféricos e ensaios</h3><p>Verificações comuns a todo o local.</p>${peripheralHistory(site)}${questions("Estado geral", def.peripherals, site.peripherals, "peripherals")}${questions("Ensaios", def.trials, site.trials, "trials")}<p class="mc-warning">${esc(def.trial_warning)}</p><p>${esc(def.coverage_warning)}</p><div class="mc-grid">${binding("coverage_percent", site.coverage_percent, "Elementos testados (%)", "text")}${textarea("coverage_areas", site.coverage_areas, "Áreas testadas")}</div>${textarea("peripheral_observations", site.peripheral_observations, "Observações dos periféricos e ensaios")}</section>`;
    }
    function photosSection(site) {
        return `<section data-mc-section="photos"><h3>Fotografias</h3><p>Fotografias deste local, incluídas no PDF da checklist. Até ${def.photo_limits.count} imagens JPG, PNG ou WebP (10 MB por ficheiro).</p><label class="mc-field">Adicionar fotografias<input type="file" accept="image/jpeg,image/png,image/webp" multiple data-mc-photos></label><div class="mc-photos">${(site.photos || []).map((photo,index) => `<article class="mc-photo">${photo.error ? `<p class="mc-errors">${esc(photo.error)}</p>` : `<img src="${esc(photo.image)}" alt="Fotografia ${index+1} do local">`}<p>${esc(photo.name)}</p>${textarea(`photos.${index}.caption`,photo.caption,`Legenda da fotografia ${index+1} (opcional)`)}<button type="button" class="btn btn-secondary" data-mc-remove-photo="${esc(photo.id)}">Remover fotografia ${index+1}</button></article>`).join('')}</div><p class="mc-photo-status" role="status"></p></section>`;
    }
    async function preparePhoto(file) {
        if (!['image/jpeg','image/png','image/webp'].includes(file.type)) throw new Error(`${file.name}: utilize uma imagem JPG, PNG ou WebP.`);
        if (file.size > def.photo_limits.source_bytes) throw new Error(`${file.name}: o limite é 10 MB por ficheiro.`);
        const url = URL.createObjectURL(file), picture = new Image();
        try {
            picture.src = url;
            await picture.decode();
            const scale = Math.min(1, def.photo_limits.edge / Math.max(picture.naturalWidth,picture.naturalHeight));
            const canvas = document.createElement('canvas');
            canvas.width = Math.max(1,Math.round(picture.naturalWidth*scale));
            canvas.height = Math.max(1,Math.round(picture.naturalHeight*scale));
            const context = canvas.getContext('2d');
            context.fillStyle = '#fff'; context.fillRect(0,0,canvas.width,canvas.height);
            context.drawImage(picture,0,0,canvas.width,canvas.height);
            let image = canvas.toDataURL('image/jpeg',.82);
            if (image.length > def.photo_limits.bytes * 4 / 3) image = canvas.toDataURL('image/jpeg',.6);
            if (image.length > def.photo_limits.bytes * 4 / 3) throw new Error(`${file.name}: imagem demasiado grande. Escolha uma versão de menor dimensão.`);
            return {id:uid(),name:file.name,caption:'',image,error:''};
        } catch (error) {
            throw new Error(error.message?.includes(file.name) ? error.message : `${file.name}: não foi possível ler a fotografia.`);
        } finally { URL.revokeObjectURL(url); }
    }
    async function addPhotos(input) {
        const site = current(), files = Array.from(input.files || []);
        const message = input.closest('section').querySelector('.mc-photo-status');
        if (!files.length) return;
        pendingPhotos++;
        input.disabled = true; input.dataset.mcBusy = 'true';
        try {
            if ((site.photos || []).length + files.length > def.photo_limits.count) throw new Error(`Pode adicionar até ${def.photo_limits.count} fotografias por local.`);
            message.textContent = 'A preparar fotografias…';
            const added = [];
            for (const file of files) added.push(await preparePhoto(file));
            // A navigation or hydration during decoding must never attach files to another site.
            if (readOnly() || !sites.includes(site)) throw new Error('O formulário mudou. Adicione novamente as fotografias ao local pretendido.');
            site.photos ||= [];
            if (site.photos.length + added.length > def.photo_limits.count) throw new Error(`Pode adicionar até ${def.photo_limits.count} fotografias por local.`);
            site.photos.push(...added); clearSignatures(site); dirty();
            if (current() === site) render(); else renderStatus();
        } catch (error) { message.textContent = error.message; notice.textContent = error.message; }
        finally { pendingPhotos--; input.value = ''; delete input.dataset.mcBusy; syncReadOnly(); }
    }
    function renderStatus() {
        nav.innerHTML = sites.map((site, index) => `<button type="button" class="mc-site ${index === active ? 'selected' : ''}" data-mc-site="${index}" aria-current="${index === active ? 'true' : 'false'}"><b>${esc(site.location || `Local ${index + 1}`)}</b><span>${model.status(site, def)} · ${siteIssues(site).length} pendências</span></button>`).join("");
        renderSignatureCards();
        renderSignatureNotice();
        renderValidationSummary();
        syncReadOnly();
    }
    function syncReadOnly() {
        if (!panel) return;
        panel.querySelectorAll("input,select,textarea,button").forEach(el => { el.disabled = el.dataset.mcBusy === 'true' || (readOnly() && !el.hasAttribute("data-mc-site") && !el.hasAttribute("data-mc-issue")); });
        panel.querySelectorAll('[data-mc-sign-canvas]').forEach(canvas=>canvas.setAttribute('aria-disabled',String(Boolean(readOnly()))));
    }
    function render() {
        const open = new Set(Array.from(host.querySelectorAll("details[open]")).map(el => el.dataset.mcDetail));
        document.getElementById("mc-site-count").value = sites.length || "";
        active = Math.max(0, Math.min(active, sites.length - 1));
        const site = current();
        if (!site) { host.innerHTML = '<p class="mc-empty">Indique o número de locais para começar.</p>'; renderStatus(); return; }
        host.innerHTML = `<h3>${esc(site.location || `Local ${active + 1}`)}</h3><p class="mc-context"></p><div class="mc-grid">${binding("location", site.location, "Identificação do local")}${binding("date", site.date, "Data da manutenção", "date")}${binding("technician", site.technician, "Técnico de serviço")}${binding("scie", site.scie, "Técnico responsável SCIE (opcional)")}<label class="mc-field">Periodicidade<select data-mc-path="period"><option value="">Selecionar</option>${Object.entries(def.periods).map(([key,label]) => `<option value="${key}" ${site.period === key ? 'selected' : ''}>${label}</option>`).join("")}</select></label>${site.period === "other" ? binding("period_other", site.period_other, "Descrição da periodicidade") : ''}</div><p class="mc-warning">${esc(def.start_warning)}</p><p>OK — Cumpre função · NC — Não Conforme · NA — Não Aplicável</p>${questions("1. Sistema — Verificações gerais", def.general, site.general, "general")}${textarea("observations", site.observations, "Observações gerais")}
        <section data-mc-section="centrals"><h3>2. Centrais</h3>${equipmentGroup("conventional",site)}${equipmentGroup("addressable",site)}</section><section data-mc-section="repeaters"><h3>3. Repetidores</h3>${equipmentGroup("repeater",site)}</section>${peripheralsSection(site)}<p class="mc-warning">${esc(def.end_warning)}</p>${photosSection(site)}<section data-mc-section="final-observations"><h3>Observações finais</h3>${textarea("final_observations",site.final_observations,"Observações finais deste local (opcional)")}</section><h3>Assinaturas deste local</h3><p>Pode assinar com campos por preencher. A verificação dos campos obrigatórios é feita ao finalizar. Uma alteração posterior exige novas assinaturas.</p><div class="mc-signatures signature-grid"></div><button class="btn btn-secondary" type="button" data-mc-validate>Verificar checklist</button><div class="mc-errors" role="status"></div>`;
        host.querySelectorAll("details").forEach(el => { el.open = open.has(el.dataset.mcDetail); });
        const doc = documentData();
        host.querySelector(".mc-context").textContent = `Cliente: ${doc.customer_name || '—'} · Folha de serviço: ${doc.service_number || '—'}`;
        renderStatus();
        sizeTextLines();
    }
    function siteIssues(site) {
        return [...model.validateSiteDetails(site, def), ...['technician', 'customer']
            .filter(role => !site.signatures?.[role]?.token)
            .map(role => ({message: `Assinatura do ${role === 'technician' ? 'técnico' : 'cliente'}`, path: role, target: 'signature'}))];
    }
    function allIssues() {
        if (!sites.length) return [{message: 'Indique o número de locais e preencha as checklists.', target: 'sites'}];
        return sites.flatMap(site => siteIssues(site).map(issue => ({...issue, siteId: site.id})));
    }
    function renderSignatureNotice() {
        if (!signatureNotice) return;
        const affected = sites.filter(site => ['technician', 'customer'].some(role => invalidatedSignatures.has(`${site.id}:${role}`)));
        signatureNotice.hidden = !affected.length;
        signatureNotice.textContent = affected.map(site => {
            const roles = ['technician', 'customer'].filter(role => invalidatedSignatures.has(`${site.id}:${role}`)).map(role => role === 'technician' ? 'técnico' : 'cliente');
            return `${site.location || `Local ${sites.indexOf(site) + 1}`}: conteúdo alterado; recolha e guarde novamente a assinatura do ${roles.join(' e do ')}.`;
        }).join(' ');
    }
    function renderValidationSummary() {
        if (!validationSummary) return;
        validationSummary.hidden = !validationVisible;
        if (!validationVisible) return;
        const issues = allIssues();
        const open = new Set(Array.from(validationSummary.querySelectorAll('details[open]')).map(el => el.dataset.mcIssueSite));
        validationSummary.innerHTML = `<h3 id="mc-validation-title" role="status">${issues.length ? `${issues.length} pendências para finalizar` : 'Checklists completas e assinadas'}</h3><p>Pode continuar a guardar o rascunho incompleto. Selecione uma pendência para ir ao campo.</p>${!sites.length ? '<button type="button" class="mc-issue-link" data-mc-issue="0">Indicar número de locais</button>' : sites.map((site, index) => {
            const entries = issues.map((issue, issueIndex) => ({...issue, issueIndex})).filter(issue => issue.siteId === site.id);
            return `<details data-mc-issue-site="${esc(site.id)}" ${open.has(site.id) || index === active ? 'open' : ''}><summary>${esc(site.location || `Local ${index + 1}`)} — ${entries.length} pendências</summary><ul>${entries.map(issue => `<li><button type="button" class="mc-issue-link" data-mc-issue="${issue.issueIndex}">${esc(issue.message)}</button></li>`).join('')}</ul></details>`;
        }).join('')}`;
        host.querySelectorAll('[aria-invalid="true"]').forEach(input => input.removeAttribute('aria-invalid'));
        siteIssues(current() || {}).forEach(issue => {
            if (issue.target === 'path' || issue.target === 'configuration' || issue.target === 'count') issueControl(issue)?.setAttribute('aria-invalid', 'true');
        });
    }
    function issueControl(issue) {
        if (issue.target === 'sites') return document.getElementById('mc-site-count');
        if (issue.target === 'photos') return host.querySelector('[data-mc-remove-photo]') || host.querySelector('[data-mc-photos]');
        if (issue.target === 'photo') return host.querySelectorAll('[data-mc-remove-photo]')[Number(issue.path)];
        if (issue.target === 'signature') {
            const input = signatureInput(current(), issue.path), card = host.querySelector(`[data-mc-sign-card="${issue.path}"]`);
            return card?.querySelector(!input.name.trim() || input.name.trim().split(/\s+/).length < 2 ? '[data-mc-sign-field="name"]' : !input.date ? '[data-mc-sign-field="date"]' : !input.image ? 'canvas' : '[data-mc-sign-save]');
        }
        const attribute = {path: 'data-mc-path', configuration: 'data-mc-config', count: 'data-mc-count'}[issue.target];
        return attribute && Array.from(host.querySelectorAll(`[${attribute}]`)).find(input => input.getAttribute(attribute) === issue.path);
    }
    function focusIssue(issue) {
        if (!issue) return;
        if (issue.siteId) {
            const index = sites.findIndex(site => site.id === issue.siteId);
            if (index < 0) return;
            if (active !== index) { active = index; render(); }
        }
        selectTab(true);
        const input = issueControl(issue);
        if (!input) return;
        for (let parent = input.parentElement; parent && parent !== panel; parent = parent.parentElement) {
            if (parent.tagName === 'DETAILS') parent.open = true;
        }
        sizeTextLines();
        input.scrollIntoView({block: 'center', behavior: 'auto'});
        input.focus({preventScroll: true});
    }
    function showValidation({focusFirst = true} = {}) {
        if (!model.applicable(documentData()) || tabs.hidden) return;
        validationVisible = true;
        renderValidationSummary();
        selectTab(true);
        if (focusFirst) focusIssue(allIssues()[0]);
    }
    function selectTab(checklists) {
        panel.hidden = !checklists;
        servicePart.hidden = checklists;
        tabs.querySelectorAll("button").forEach((button,index) => button.setAttribute("aria-selected", String(index === (checklists ? 1 : 0))));
        if (checklists && current()) {
            const doc = documentData();
            host.querySelector(".mc-context").textContent = `Cliente: ${doc.customer_name || '—'} · Folha de serviço: ${doc.service_number || '—'}`;
            sizeTextLines();
        }
    }
    function sync() {
        if (!loaded || !form) return;
        const doc = documentData(), enabled = model.applicable(doc), commonNow = JSON.stringify([doc.customer_name, doc.service_number, enabled]);
        if (common && commonNow !== common) { sites.forEach(clearSignatures); dirty(); renderStatus(); }
        common = commonNow;
        tabs.hidden = !enabled || !config.selectedFileIsDraft;
        if (tabs.hidden) selectTab(false);
        syncReadOnly();
    }
    async function reduce(items, count, label) {
        const dialog = document.createElement("dialog"); dialog.className = "mc-dialog";
        const headingId = `mc-dialog-${uid()}`;
        dialog.setAttribute('aria-labelledby', headingId);
        dialog.innerHTML = `<h3 id="${headingId}">Remover ${items.length - count} ${esc(label)}</h3><p>Escolha os elementos a remover. Os seus dados e assinaturas serão removidos do rascunho.</p>${items.map((item,index) => `<label class="mc-remove-choice"><input type="checkbox" value="${index}">${esc(item.location || item.brand || `${label} ${index + 1}`)}</label>`).join("")}<p role="status"></p><div class="mc-dialog-actions"><button type="button" data-cancel autofocus>Voltar</button><button type="button" data-remove>Confirmar remoção</button></div>`;
        document.body.append(dialog); dialog.showModal();
        return new Promise(resolve => {
            dialog.addEventListener("close", () => { dialog.remove(); resolve(null); }, {once:true});
            dialog.querySelector("[data-cancel]").onclick = () => dialog.close();
            dialog.querySelector("[data-remove]").onclick = () => {
                if (readOnly()) return dialog.close();
                const selected = Array.from(dialog.querySelectorAll("input:checked")).map(input => Number(input.value));
                if (selected.length !== items.length - count) { dialog.querySelector('[role="status"]').textContent = `Selecione exatamente ${items.length - count} elementos.`; return; }
                resolve(items.filter((_,index) => !selected.includes(index))); dialog.close();
            };
        });
    }
    function confirmAction(title, message, acceptLabel) {
        const dialog = document.createElement('dialog'), headingId = `mc-dialog-${uid()}`, previousFocus = document.activeElement;
        dialog.className = 'mc-dialog';
        dialog.setAttribute('aria-labelledby', headingId);
        dialog.innerHTML = `<h3 id="${headingId}">${esc(title)}</h3><p>${esc(message)}</p><div class="mc-dialog-actions"><button type="button" data-cancel autofocus>Voltar</button><button type="button" data-accept>${esc(acceptLabel)}</button></div>`;
        document.body.append(dialog);
        return new Promise(resolve => {
            dialog.addEventListener('close', () => {
                const accepted = dialog.returnValue === 'accept';
                dialog.remove();
                if (previousFocus?.isConnected) previousFocus.focus({preventScroll: true});
                resolve(accepted);
            }, {once: true});
            dialog.querySelector('[data-cancel]').onclick = () => dialog.close('cancel');
            dialog.querySelector('[data-accept]').onclick = () => dialog.close(readOnly() ? 'cancel' : 'accept');
            dialog.showModal();
        });
    }
    async function changeCount(input) {
        const count = Number(input.value), kind = input.dataset.mcCount;
        const owner = current(), items = kind ? owner[kind] : sites;
        const redraw = () => {
            render();
            (kind ? host.querySelector(`[data-mc-count="${kind}"]`) : document.getElementById('mc-site-count'))?.focus({preventScroll: true});
        };
        if (!Number.isSafeInteger(count) || count < 1) { notice.textContent = "Indique uma quantidade inteira superior a zero."; render(); return; }
        // Keep accidental enormous browser allocations out of the UI, without silently truncating data.
        if (count > 100 && !await confirmAction(`Criar ${count} elementos?`, 'Esta quantidade pode tornar o formulário mais lento. Confirme a quantidade antes de continuar.', 'Confirmar quantidade')) { redraw(); return; }
        let next = items;
        if (count < items.length) { next = await reduce(items,count,kind ? def.groups[kind].toLowerCase() : "locais"); if (!next) { redraw(); return; } }
        else next = [...items, ...Array.from({length:count-items.length},(_,i) => kind ? createUnit() : createSite(items.length+i))];
        if (readOnly() || (kind ? !sites.includes(owner) || owner[kind] !== items : sites !== items)) { redraw(); return; }
        if (count === items.length) { redraw(); return; }
        if (kind) { owner[kind] = next; clearSignatures(owner); } else sites = next;
        dirty(); redraw();
    }
    function signatureInput(site, role) {
        const today = new Date(); today.setMinutes(today.getMinutes()-today.getTimezoneOffset());
        const input = site.signature_drafts?.[role] || site.signatures?.[role];
        return input ? {name:input.name || '',date:input.date || '',image:input.image || ''} :
            {name:role === 'technician' ? site.technician || '' : '',date:today.toISOString().slice(0,10),image:''};
    }
    function signatureKey(site,role) {
        const {signatures,signature_drafts,...content} = site;
        const doc = documentData();
        return JSON.stringify([content,doc.customer_name,doc.service_number,model.applicable(doc),signatureInput(site,role)]);
    }
    function renderSignatureCards() {
        const container = host.querySelector('.mc-signatures'), site = current();
        if (!container || !site) return;
        if (!container.children.length) {
            container.innerHTML = [['technician','Técnico'],['customer','Cliente']].map(([role,label]) => {
                const input = signatureInput(site,role), id = esc(`mc-sign-${site.id}-${role}`);
                if (!site.signatures?.[role] && !site.signature_drafts?.[role]) {
                    site.signature_drafts ||= {}; site.signature_drafts[role] = input;
                }
                return `<section class="signature-card" data-mc-sign-card="${role}" aria-label="Assinatura do ${label.toLowerCase()}">
                    <div class="signature-card-head"><h3>${label}</h3><button type="button" class="btn btn-secondary" data-mc-sign-clear="${role}">Limpar</button></div>
                    <div class="signature-capture"><div class="signature-details">
                        <div class="form-field"><label for="${id}-name">Primeiro e último nome <span class="required" aria-hidden="true">*</span></label><input id="${id}-name" type="text" data-mc-signer="${role}" data-mc-sign-field="name" aria-required="true" placeholder="Primeiro e último nome" autocomplete="name" maxlength="120" value="${esc(input.name)}"></div>
                        <div class="form-field signature-date"><label for="${id}-date">Data da assinatura <span class="required" aria-hidden="true">*</span></label><input id="${id}-date" type="date" data-mc-signer="${role}" data-mc-sign-field="date" aria-required="true" value="${esc(input.date)}"></div>
                    </div><canvas class="signature-canvas" width="700" height="160" tabindex="0" data-mc-sign-canvas="${role}" aria-label="Assinatura do ${label.toLowerCase()}"></canvas>
                    <p class="signature-help">Assine no espaço acima com o dedo ou com uma caneta digital.</p></div>
                    <div class="mc-signature-actions"><p class="signature-help" data-mc-sign-status role="status"></p><button type="button" class="btn btn-secondary" data-mc-sign-save="${role}">Guardar assinatura</button></div>
                </section>`;
            }).join('');
            container.querySelectorAll('[data-mc-sign-canvas]').forEach(canvas => setupSignatureCanvas(canvas,site));
        }
        container.querySelectorAll('[data-mc-sign-card]').forEach(card => {
            const role = card.dataset.mcSignCard, key = `${site.id}:${role}`;
            card.querySelector('[data-mc-sign-status]').textContent = signatureJobs.has(key) ? 'A guardar assinatura…' : signatureMessages.get(key) || (site.signatures?.[role]?.token ? 'Assinatura guardada.' : signatureInput(site, role).image ? 'Desenho por guardar. Complete o nome e a data e guarde a assinatura.' : invalidatedSignatures.get(key) || 'Assinatura por recolher');
            const save = card.querySelector('[data-mc-sign-save]');
            save.hidden = Boolean(site.signatures?.[role]?.token);
            save.dataset.mcBusy = String(signatureJobs.has(key));
            card.querySelector('canvas').setAttribute('aria-disabled',String(Boolean(readOnly())));
        });
    }
    function setupSignatureCanvas(canvas, site) {
        const role = canvas.dataset.mcSignCanvas, ctx = canvas.getContext('2d');
        ctx.lineWidth=2.5; ctx.lineCap='round'; ctx.strokeStyle='#163246';
        const initial = signatureInput(site,role).image;
        let drawing=false, changed=false, restoring=Boolean(initial);
        if (initial) {
            const image = new Image();
            image.onload=()=>{
                if(canvas.isConnected && !changed && signatureInput(site,role).image===initial){
                    const scale=Math.min(canvas.width/image.width,canvas.height/image.height);
                    ctx.drawImage(image,(canvas.width-image.width*scale)/2,(canvas.height-image.height*scale)/2,image.width*scale,image.height*scale);
                }
                restoring=false;
            };
            image.onerror=()=>{restoring=false;}; image.src=initial;
        }
        const point = event => {const r=canvas.getBoundingClientRect();return [(event.clientX-r.left)*canvas.width/r.width,(event.clientY-r.top)*canvas.height/r.height];};
        canvas.onpointerdown=event=>{
            if(readOnly() || restoring || event.button > 0)return;
            drawing=true;changed=true;canvas.setPointerCapture(event.pointerId);ctx.beginPath();ctx.moveTo(...point(event));event.preventDefault();
        };
        canvas.onpointermove=event=>{if(drawing && !readOnly()){ctx.lineTo(...point(event));ctx.stroke();}};
        canvas.onpointerup=canvas.onpointercancel=()=>{
            if(!drawing)return;drawing=false;
            if(readOnly() || !sites.includes(site))return;
            const input=signatureInput(site,role);
            site.signature_drafts ||= {}; site.signature_drafts[role]={...input,image:canvas.toDataURL('image/png')};
            delete site.signatures?.[role];signatureMessages.delete(`${site.id}:${role}`);dirty();renderStatus();
            saveSignature(site,role);
        };
    }
    function editSigner(input) {
        const site=current(),role=input.dataset.mcSigner, value=signatureInput(site,role);
        if(value[input.dataset.mcSignField]===input.value)return;
        value[input.dataset.mcSignField]=input.value;
        if(site.signatures?.[role]?.token){
            value.image='';host.querySelector(`[data-mc-sign-canvas="${role}"]`).getContext('2d').clearRect(0,0,700,160);
            invalidatedSignatures.set(`${site.id}:${role}`, 'Assinatura invalidada: o nome ou a data foi alterado. Recolha e guarde uma nova assinatura.');
        }
        site.signature_drafts ||= {};site.signature_drafts[role]=value;delete site.signatures?.[role];
        signatureMessages.delete(`${site.id}:${role}`);dirty();renderStatus();
    }
    function clearSignature(role) {
        const site=current(),value=signatureInput(site,role);
        site.signature_drafts ||= {};site.signature_drafts[role]={...value,image:''};delete site.signatures?.[role];
        const canvas=host.querySelector(`[data-mc-sign-canvas="${role}"]`);canvas.getContext('2d').clearRect(0,0,canvas.width,canvas.height);
        signatureMessages.delete(`${site.id}:${role}`);dirty();renderStatus();
    }
    async function saveSignature(site,role) {
        if(readOnly() || !sites.includes(site))return;
        const jobKey=`${site.id}:${role}`, input=signatureInput(site,role);
        if(signatureJobs.has(jobKey)){signatureJobs.get(jobKey).again=true;return;}
        const message=!input.image ? 'Assine no espaço indicado.' : !input.name.trim() || !input.date ? 'Indique o primeiro e último nome e a data da assinatura.' : pendingPhotos ? 'Aguarde a preparação das fotografias antes de assinar.' : '';
        if(message){signatureMessages.set(jobKey,message);renderStatus();return;}
        let settled;
        const job={again:false,done:new Promise(resolve=>{settled=resolve;})}, key=signatureKey(site,role);
        signatureJobs.set(jobKey,job);signatureMessages.delete(jobKey);renderStatus();
        try {
            const metadata=await window.__EDITING_COORDINATOR__.prepareOperation('maintenance-sign');
            if(readOnly() || !sites.includes(site) || signatureKey(site,role)!==key)return;
            const response=await fetch(`/api/file/${encodeURIComponent(window.__FILES_EDITOR__.getActiveFileName())}/maintenance/sign`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({_edit:metadata,document:documentData(),site_id:site.id,role,name:input.name,date:input.date,image:input.image})});
            const result=await response.json();
            if(!response.ok){window.__EDITING_COORDINATOR__?.handleConflict(result);throw new Error(result.error||'Não foi possível guardar a assinatura.');}
            if(!readOnly() && sites.includes(site) && signatureKey(site,role)===key){
                site.signatures ||= {};site.signatures[role]=result.signature;delete site.signature_drafts?.[role];invalidatedSignatures.delete(jobKey);dirty();
            }
        } catch(error){signatureMessages.set(jobKey,error.message);}
        finally {
            signatureJobs.delete(jobKey);if(current()===site)renderStatus();
            if(job.again && sites.includes(site))saveSignature(site,role);
            settled();
        }
    }
    window.MaintenanceEditor = {
        init() {
            form=document.getElementById("service-form");panel=document.getElementById("maintenance-panel");tabs=document.getElementById("maintenance-tabs");servicePart=document.getElementById("service-part");host=document.getElementById("mc-content");nav=document.getElementById("mc-sites");notice=document.getElementById("mc-notice");
            signatureNotice = document.createElement('p');signatureNotice.className = 'mc-warning';signatureNotice.setAttribute('role', 'status');signatureNotice.hidden = true;
            validationSummary = document.createElement('section');validationSummary.className = 'mc-validation';validationSummary.setAttribute('aria-labelledby', 'mc-validation-title');validationSummary.hidden = true;
            host.before(signatureNotice, validationSummary);
            tabs.onclick=event=>{const tab=event.target.closest("[data-mc-tab]");if(tab)selectTab(tab.dataset.mcTab==="checklists");};
            form.addEventListener("input",event=>{
                const input=event.target;
                if(input.dataset.mcSigner && !readOnly()) {editSigner(input);return;}
                if(input.dataset.mcPath && !readOnly()) {
                    let target=current();const parts=input.dataset.mcPath.split(".");const key=parts.pop();for(const part of parts){target[part] ||= {};target=target[part];}
                    if(target[key]!==input.value){target[key]=input.value;clearSignatures(current());dirty();renderStatus();}
                    if(input.tagName === 'TEXTAREA') sizeTextLines();
                }
                if(!panel.contains(input))sync();
            });
            panel.addEventListener("change",async event=>{
                if(readOnly())return;
                const input=event.target;
                if(input.dataset.mcSigner){const site=current();if(signatureInput(site,input.dataset.mcSigner).image)saveSignature(site,input.dataset.mcSigner);return;}
                if(input.hasAttribute('data-mc-photos')){await addPhotos(input);return;}
                if(input.id==="mc-site-count"||input.dataset.mcCount){await changeCount(input);return;}
                if(input.dataset.mcConfig){const kind=input.dataset.mcConfig;current().configuration[kind]=input.value===""?null:input.value==="yes";clearSignatures(current());dirty();render();return;}
                if(input.tagName==="SELECT"&&input.dataset.mcPath)render();
            });
            panel.addEventListener("click",async event=>{
                const button=event.target.closest("button");if(!button)return;
                if(button.hasAttribute("data-mc-site")){active=Number(button.dataset.mcSite);render();return;}
                if(button.hasAttribute("data-mc-issue")){focusIssue(allIssues()[Number(button.dataset.mcIssue)]);return;}
                if(readOnly())return;
                if(button.dataset.mcRemovePhoto){
                    const site=current(), photoId=button.dataset.mcRemovePhoto;
                    if(!await confirmAction('Remover fotografia?', 'A fotografia será retirada deste local e do PDF da checklist. As assinaturas deste local terão de ser recolhidas novamente.', 'Remover fotografia'))return;
                    if(readOnly() || !sites.includes(site))return;
                    site.photos=site.photos.filter(photo=>photo.id!==photoId);clearSignatures(site);dirty();render();host.querySelector('[data-mc-photos]')?.focus({preventScroll:true});return;
                }
                if(button.hasAttribute("data-mc-validate"))showValidation();
                if(button.dataset.mcSignClear)clearSignature(button.dataset.mcSignClear);
                if(button.dataset.mcSignSave)saveSignature(current(),button.dataset.mcSignSave);
            });
            new MutationObserver(syncReadOnly).observe(form,{attributes:true,attributeFilter:["class"]});
            host.addEventListener('toggle',sizeTextLines,true);
        },
        hydrate(document) {sites=structuredClone(document.maintenance_checklists||[]);signatureMessages.clear();invalidatedSignatures.clear();validationVisible=false;active=0;loaded=true;common="";notice.textContent="";render();sync();},
        collect() {return structuredClone(sites);},
        async settleSignatures() {
            while(signatureJobs.size)await Promise.all(Array.from(signatureJobs.values(),job=>job.done));
        },
        previewTarget() {return panel && !panel.hidden && !tabs.hidden ? {siteId: current()?.id} : null;},
        validate(document) {
            if(!model.applicable(document))return [];
            if(signatureJobs.size)return ["Aguarde a gravação das assinaturas antes de finalizar."];
            if(pendingPhotos)return ['Aguarde a preparação das fotografias antes de finalizar.'];
            if(!sites.length)return ["SADI: indique o número de locais e preencha as checklists."];
            return sites.flatMap((site,index)=>[...model.validateSite(site,def),...(["technician","customer"].filter(role=>!site.signatures?.[role]?.token).map(role=>`Assinatura do ${role==='technician'?'técnico':'cliente'}`))].map(error=>`${site.location||`Local ${index+1}`}: ${error}`));
        },
        showValidation,
        sync
    };
})();
