# Interface, fluxos e contratos da aplicação

Levantamento estático em 28-09-2026. Este documento descreve o estado do código presente na pasta de trabalho, incluindo alterações locais. Não comprova execução, validação visual ou funcionamento em produção.

O código da interface está em `aplicacao/src/web/templates` e `aplicacao/src/web/static`. O pacote inclui o backend real da aplicação de campo: Flask/Jinja, regras, serviços de ficheiros/Excel, assinaturas, PDF, Microsoft Graph, autenticação e filas. Esta é uma base local para continuar o desenvolvimento; a administração fica excluída. Os templates Jinja não arrancam por si próprios: são renderizados pelo backend incluído. O laboratório estático é uma ferramenta separada para experimentar regras e não substitui a aplicação.

As referências `src/...:linha` identificam o código original analisado; procurar o mesmo caminho sob `aplicacao/`. As referências a `application.py` descrevem o controlador incluído. Antes de o importar ou arrancar, estabelecer o ambiente de teste isolado: configuração operacional excluída, diretórios fictícios exclusivos, comunicações bloqueadas e nenhuma fila operacional. Os mocks servem para validação de transportes e cenários de falha, não substituem o backend entregue. A cópia de código não configura produção nem autoriza comunicações reais.

## 1. Mapa da interface existente

| Área | Ficheiros principais | Comportamento observado |
| --- | --- | --- |
| Seleção e preenchimento | `templates/field_app.html`, `partials/active_file_list.html` | Lista pesquisável, estados, formulário, confirmação, ocupação, recuperação e ações finais. |
| Editor atual | `static/js/document-editor.js` | Recolha/preenchimento de dados, idiomas, materiais, técnicos/horas, assinatura, fotografias, validação final, pré-visualização e ações. |
| Estado de edição | `static/js/editing-coordinator.js`, `static/css/editing-state.css` | Inicialização, sessão, revisão, recuperação local, autosave, conflitos e transições entre páginas/abas. |
| Regras da folha | `static/js/document-validation.js` | Validação pura com definições de campos obrigatórios injetadas na página. |
| Checklists SADI | `static/js/maintenance-model.js`, `maintenance-editor.js`, `static/css/maintenance.css` | Regras puras e UI por local, equipamentos, respostas, fotos e assinaturas. |
| Documento da folha | `templates/service_document.html`, `partials/document_header.html`, `static/css/service-document.css` | Documento PT/EN, cabeçalho comum, materiais, horas e assinatura do cliente, com impressão. |
| Documento SADI | `templates/maintenance_document.html`, `static/css/maintenance-document.css` | Documento separado por local, verificações, não conformidades, fotografias e duas assinaturas. |
| Consulta de documentos | `templates/document_preview.html` | Separadores da folha e das checklists, iframe e impressão do documento selecionado. |
| Resultado da demo | `templates/maintenance_bundle.html` | Lista de PDFs por destino e botão de simulação de envio. |
| Acesso e erro | `templates/login.html`, `work_folder_error.html`, `static/css/auth.css` | Apresentação para acesso e indisponibilidade da pasta da obra. A implementação de autenticação acompanha o backend; identidade e credenciais operacionais não acompanham o pacote. |
| Instalação PWA | `static/js/pwa.js`, `service-worker.js`, `manifest.webmanifest`, `offline.html`, ícones | Instalação, cache de recursos estáticos e página de indisponibilidade. |
| Estado de envio | `static/js/mail-job-monitor.js` | Consulta de trabalho assíncrono e distinção entre pendente, aceitação, falha, cancelamento e resultado desconhecido. Exercitar estes estados com transporte simulado durante a validação. |

`static/js/field-app.js` é uma implementação anterior presente no repositório, mas não é carregada pelo template atual. Não carregar os dois editores em simultâneo nem usar esse ficheiro como referência principal. A ordem atual de scripts é monitor → validação → modelo/editor de manutenção, quando habilitados → editor da folha → coordenador → PWA (`src/web/templates/field_app.html:618`).

## 2. Fluxos a preservar

### 2.1 Abrir uma folha

1. Mostrar lista com nome, cliente, local, contacto, morada e telefone quando existem.
2. Pesquisar na lista sem pedido por tecla; a pesquisa usa os textos `data-search` e atualiza o contador.
3. Permitir abrir estados `ready` e `in_progress`; apresentar `locked` como «Em uso» e os restantes estados inválidos com a respetiva mensagem, sem ligação de abertura.
4. Abrir `/?file=<nome codificado>`. A página atual recebe apenas um marcador inicial; os dados são obtidos uma vez através de `bootstrap`.
5. Enquanto a sessão é preparada, mostrar ocupação e desativar gravação/finalização. Rascunho de outro utilizador ou de outra aba fica em consulta.

Referências: `src/web/templates/partials/active_file_list.html:1`, `src/web/application.py:858`, `src/web/static/js/document-editor.js:1700`, `src/web/static/js/editing-coordinator.js:582`.

### 2.2 Preencher a folha

O formulário cobre identificação do cliente e pedido, contacto e local, obra/contrato, tipos de serviço e equipamentos, tarefas pedidas, relatório da intervenção, materiais, técnicos/horas, fotografias internas e assinatura do cliente. O número de serviço é apresentado como só de leitura; a obra tem ligação própria. Preservar também os campos e coleções que não estejam visíveis na secção ativa.

Materiais dependem de «Foram utilizados materiais?». Ao remover materiais preenchidos, existe confirmação. As linhas de técnicos incluem nome/identificação selecionável, início, fim, total de horas, data e a indicação de correção manual do total. O editor limita a quatro técnicos. A duração calculada e a alteração manual têm de ser preservadas sem confundir horas decimais com `HH:MM`.

Referências: `src/web/templates/field_app.html:211`, `src/web/static/js/document-editor.js:80`, `:1025`, `:1317`, `:1817`.

### 2.3 Guardar, finalizar e cancelar

| Ação | Comportamento atual da interface | Comportamento a preservar no backend incluído |
| --- | --- | --- |
| Guardar rascunho | Permite dados incompletos; pode criar uma cópia privada a partir da folha original; navega para o rascunho devolvido. | Consolidar os ficheiros/dados do rascunho e devolver `file` e `created_copy` coerentes. Validar com armazenamento local fictício. |
| Finalizar | Só disponível num rascunho com sessão ativa; valida obrigatórios, datas/horas, assinatura ou ausência do cliente, e SADI quando aplicável. Abre confirmação e campo de observações internas. | Preparar os artefactos e concluir segundo o estado persistido; em testes, usar ficheiros fictícios e transportes bloqueados/simulados. A demo SADI mantém a sua restrição própria. |
| Cancelar folha | Confirma retirada da lista ativa; o contrato pode mover ficheiros. | Cancelar apenas a folha identificada, conservando as garantias de revisão e recuperação. Ensaiar exclusivamente com ficheiros fictícios. |
| Recarregar/sair/mudar de folha | O coordenador tenta preservar alterações e fechar a sessão antes da navegação. | Não descartar dados locais sem uma transição explícita. |

Não confundir «Recarregar» com «Cancelar folha»: são ações com efeitos diferentes. A finalização escolhe texto de envio/arquivo de acordo com email e flags. No ambiente de desenvolvimento, manter os transportes desligados ou injetar mocks antes do arranque. A mensagem da interface não serve de prova de que o envio está bloqueado. O código de envio real está incluído, mas a sua ativação exige configuração e autorização próprias.

Referências: `src/web/static/js/document-editor.js:1474`, `:1920`, `:1950`, `:1974`, `:1980`; `src/web/static/js/editing-coordinator.js:741`.

### 2.4 Checklists

O código atual só disponibiliza a interface SADI quando `maintenanceEnabled` está ativo, a folha é um rascunho e o modelo considera a manutenção aplicável. O template original liga a funcionalidade a `config.MAINTENANCE_DEMO`. Não apresentar este estado como rollout de produção concluído (`src/web/static/js/maintenance-editor.js:4`, `:154`; `src/web/templates/field_app.html:595`).

O fluxo previsto é criar rascunho → selecionar Manutenção/SADI → indicar locais → preencher identificação/periodicidade → responder verificações gerais → definir existência/quantidade de centrais convencionais, endereçáveis e repetidores → preencher periféricos/ensaios comuns ao local → fotografias e observações → assinaturas do técnico e cliente → verificar/finalizar.

As respostas são `OK`, `NC` e `NA`; `NC` mostra justificação. As regras completas e os limites vêm do catálogo/modelo, não devem ser duplicados em textos dispersos. A redução da quantidade de locais/equipamentos obriga a escolher exatamente o que remover; a UI avisa que dados e assinaturas desses elementos desaparecem do rascunho. Há confirmação adicional para quantidades acima de 100 (`src/web/static/js/maintenance-editor.js:46`, `:165`, `:179`).

As assinaturas de checklist são capturadas por canvas, têm nome/data e são guardadas através de contrato próprio. Pode assinar antes de preencher todos os campos; a validação completa ocorre na finalização. Alterar conteúdo do local invalida as suas assinaturas; alterar cliente/número da folha/aplicabilidade invalida as assinaturas dos locais. Só aplicar uma resposta de assinatura se o conteúdo não mudou durante o pedido (`src/web/static/js/maintenance-editor.js:26`, `:154`, `:273`).

### 2.5 Fotografias: dois usos diferentes

| Fotografias da folha | Fotografias da checklist |
| --- | --- |
| Uso interno; o texto da UI indica exclusão do relatório enviado ao cliente. | Incluídas no PDF da checklist do local. |
| Objetos guardados têm `id`, `url`, `size` e metadados apresentados. Novos ficheiros ficam em memória até «Guardar rascunho»/finalizar. | Ficam dentro de `maintenance_checklists[].photos`, com `id`, `name`, `caption`, `image`, `error`. |
| Limites UI: 10 MB por ficheiro, 50 MB no total; upload multipart. | JPG/PNG/WebP; redução em canvas para JPEG e limites definidos por `maintenanceDefinition.photo_limits`. |
| Autosave de texto não grava os ficheiros pendentes. Há aviso para guardar rascunho e proteção `beforeunload`. | A preparação é assíncrona; não finalizar/assinar enquanto houver preparação pendente. |

Referências: `src/web/static/js/document-editor.js:80`, `:138`, `:458`, `:565`, `:608`, `:1898`; `src/web/static/js/editing-coordinator.js:409`, `:474`; `src/web/static/js/maintenance-editor.js:70`.

### 2.6 Consulta e impressão

A pré-visualização original envia o documento atual, recebe HTML, abre uma janela e escreve esse HTML. Exportar PDF usa o fluxo de impressão do navegador (`_auto_print`), não um download binário diretamente nesta ação da UI. Para SADI, pode selecionar `_maintenance_site_id`; a consulta conjunta oferece um separador por documento. Preservar CSS de impressão e cabeçalho comum, verificando depois com textos compridos e várias páginas.

Na demo finalizada, a folha de serviço tem destino «Anexo do email ao cliente» e cada checklist tem destino «Guardado na pasta do serviço». O ecrã da demo simula envio; não é prova de comunicação real. Fora da demo, o backend incluído contém serviços de geração PDF e envio, cuja configuração operacional não está ativada por esta migração. Referências: `src/web/static/js/document-editor.js:1651`; `src/web/application.py:1408`; `src/web/templates/document_preview.html:24`; `src/web/templates/maintenance_bundle.html:3`.

Limitação observada: o template da folha imprime apenas `materials[:5]` e quatro registos de técnicos, enquanto a UI permite adicionar linhas de materiais. Decidir uma regra explícita de limite ou paginação/continuação e verificar que nenhuma linha aceite no formulário desaparece silenciosamente no documento (`src/web/templates/service_document.html:23`; `src/web/static/js/document-editor.js:1871`).

## 3. Contexto de renderização existente

O controlador Flask incluído constrói o contexto de renderização. A tabela seguinte ajuda a preservar o contrato em alterações e a preparar fixtures de teste. Os valores fictícios indicados aplicam-se à validação isolada; o backend também possui os percursos reais. Não basta servir o ficheiro Jinja como HTML.

| Contexto | Dados necessários |
| --- | --- |
| Página | `files`, `selected_file_name`, `selected_file_is_draft`, `selected_document_data` como marcador de seleção, `selected_file_error`, `current_user` fictício e `asset_version`. |
| Configuração | `config.SYNTHETIC_TEST_VERSION`, `config.MAINTENANCE_DEMO`; funções equivalentes a `url_for('static')`, `manifest`, `index`, `logout`. |
| Regras/catálogos | `document_required_fields`, `technician_required_fields`, `signature_required_fields`, `service_type_options`, `equipment_options`, `technician_options` fictícias e `maintenance_definition`. |
| Estado JS | `window.__FILES_APP__` com `bootstrapEnabled`, `maintenanceEnabled`, `maintenanceDefinition`, flags de integração, regras, seleção, dados, assinaturas vazias, estado de edição, `editorUser` fictício e `recoveryMaxAgeDays`. |
| Documento da folha | `document`, `file_name`, `signatures` sintéticas/vazias, `responsible_technician` fictício, `service_type_options`, `equipment_options`, `embedded_styles`, `logo_src`, `auto_print`. |
| Documento SADI | `document`, `site`, `definition` com o catálogo SADI, `draft_preview`, `embedded_styles`, `logo_src` e `auto_print`. |
| Consulta conjunta | `preview = {documents:[{id,label,html}], selected}`. |
| Resultado simulado | `entries = [{label,delivery,name}]`, `bundle`, URLs locais de consulta e simulação. |
| Acesso/erro | `auth_provider`, `next_url`, `error`, `asset_version`; no erro de pasta, título e mensagem. Sem identidades ou credenciais reais. |

Referências: `src/web/templates/field_app.html:592`, `src/web/application.py:833`, `:858`; `src/web/templates/service_document.html:1`; `src/web/templates/maintenance_document.html:1`.

Jinja utiliza includes, macros, `tojson`, filtros, acesso a objetos e métodos Python como `removesuffix` no template SADI. A base entregue conserva Flask/Jinja. Um motor de templates de outra linguagem não é automaticamente compatível; uma eventual conversão para componentes é uma evolução separada e deve preservar estes contratos e o resultado visual.

## 4. Contratos HTTP observados — interface e backend incluídos

Os caminhos abaixo são dependências existentes da UI e estão implementados no backend incluído. Preservar os seus formatos ao desenvolver noutra pasta. Para testes de UI ou falhas, um mock pode responder a estes contratos com dados sintéticos, sem encaminhar pedidos para servidores operacionais. A extração futura de um `AppAdapter` é uma proposta de manutenção, não uma condição para usar a base entregue.

| Pedido | Entrada relevante | Resposta usada pela interface |
| --- | --- | --- |
| `GET /api/files[?refresh=0/1]` | Modo de atualização. | `{success, files, html, refresh}`. A UI atual substitui a lista por `html`, logo devolver só JSON de linhas não é suficiente sem adaptar o consumidor. |
| `GET /api/file/:name/bootstrap?client_id=…` | Nome codificado e id da sessão da aba. | `{success,file,is_draft,data,source_document,document,signatures,photos,recovery_source,editing}`. |
| `POST /api/file/:name/autosave` | `{document, _edit}`. | `{success,message,editing}`. Assinaturas da folha não são consolidadas pelo autosave original. |
| `POST /api/file/:name/editing/close` | `{document: documentoOuNull, _edit}`; pode usar beacon. | A UI depende da libertação da sessão; a cópia local é o recurso de recuperação em falha. |
| `POST /api/file/:name/draft` | Documento com `_edit`, ou multipart descrito abaixo. | `success`, `error`, `file`, `created_copy`, `photos`, revisão/estado, eventual `publication_status`. |
| `POST /api/file/:name/send` | Documento com `_edit` e `_internal_observations`, ou multipart. | `success`, `error`, `missing_fields`, `invalid_fields`, estado, `email_status`, `graph_job_id`, `message` ou `maintenance_bundle_url`. Em testes, concluir apenas dados fictícios e simular/bloquear envio. |
| `POST /api/file/:name/cancel` | Metadados de edição no corpo JSON. | `success`, `error` e estado resultante. |
| `POST /api/file/:name/document-preview` | Documento e opcionais `_auto_print`, `_maintenance_site_id`. | `{success,html}` ou erro. |
| `GET /api/file/:name/photos/:photo_id` | ID de imagem interna já guardada. | Imagem usada pelo atributo `url` da resposta de fotografias; mock deve apontar apenas para assets de teste. |
| `POST /api/file/:name/maintenance/sign` | `{_edit,document,site_id,role,name,date,image}`. | `{success,signature}`; consumidor exige `signature.token` para considerar assinatura guardada. Um token mock não constitui autenticação ou assinatura verificável. |
| `GET /api/graph/status` | Sem corpo. | `{success,refresh}` para a atualização da lista. Simular ou remover polling se a integração estiver desligada. |
| `GET /api/graph/jobs/:id` | ID de trabalho. | `{success,job}`; ver estados abaixo. |
| `POST /api/graph/jobs/:id/retry` | Pedido explícito de repetição. | `success/error` seguido da monitorização do mesmo trabalho. Validar com fila fictícia; nunca reprocessar a fila operacional para testar. |
| `GET /work-folder/:work_number` | Número da obra. | Navegação para a pasta da obra quando configurada. Em testes, simular resolução sem abrir pastas empresariais. |
| `GET /demo/maintenance/:bundle` e `/pdf/:filename` | Conjunto sintético. | Página de resultado e PDFs separados gerados pela demo isolada do backend. O laboratório de regras estático não gera estes artefactos. |
| `POST /demo/maintenance/:bundle/simulate` | Sem dados de cliente adicionais. | `{message}` ou `{error}` mostrado pelo resultado. |

Referências: `src/web/static/js/document-editor.js:1487`, `:1531`, `:1593`, `:1626`, `:1677`, `:1722`; `src/web/static/js/editing-coordinator.js:379`, `:487`, `:728`; `src/web/static/js/maintenance-editor.js:287`; `src/web/static/js/mail-job-monitor.js:49`; `src/web/application.py:1010`, `:1131`, `:1245`.

Rotas presentes mas não consumidas pelo fluxo atual principal: `GET /api/file/:name` pertence também ao editor anterior; `/lease`, `/lease/heartbeat`, `/lease/release`, `/autosave/discard` e `/maintenance/validate` existem no servidor, mas o coordenador/editor atual usa bootstrap, autosave, close e validação SADI local. Estas rotas acompanham o backend; não acrescentar novos consumidores nem removê-las sem rever compatibilidade e testes. Referências: `src/web/application.py:718`, `:1203`, `:1262`, `:1283`, `:1306`, `:1388`.

### 4.1 Formatos partilhados

`_edit` contém `document_id`, `client_id`, `lease_token`, `base_revision` e `idempotency_key`. A revisão é numérica; a mesma operação mantém a chave em repetição até concluir. O snapshot `editing` deve dar pelo menos `document_id`, `revision`, `lease.token` e, quando aplicável, `server_document` e `owner.owner_name` fictício. Referência: `src/web/static/js/editing-coordinator.js:267`.

Sem fotografias pendentes, draft/send utilizam `application/json` com o documento no nível principal. Com alterações de fotos, utilizam `FormData` com `document` serializado, `removed_photo_ids` como JSON e uma entrada `photos` por ficheiro. Não misturar este formato com autosave, que usa `{document,_edit}`. Referência: `src/web/static/js/document-editor.js:608`.

O documento recolhido mantém nomes de campos em inglês e duas chaves históricas de assinatura em português. Tem escalares, `service_types`/`equipments` como mapas, `materials` como linhas `{ref,description,qty}`, `technician_records` como linhas de horas, `maintenance_checklists` como coleção e `client_not_present` como booleano. `nif_number` espelha `vat_number`. Não converter booleanos para textos «Sim/Não» no modelo. Referência: `src/web/static/js/document-editor.js:394`, `:1331`.

### 4.2 Erros e estados a validar

Preparar cenários determinísticos de sucesso e falha, sem depender de rede real:

- `400`: validação inválida, com `error`, `missing_fields` e/ou `invalid_fields` quando relevantes.
- `401`: sessão inválida; ações desativadas, sem apagar recuperação.
- `404`: folha inexistente.
- `423`: rascunho de outro utilizador, `code: lease_conflict`, snapshot de edição e documento consultável.
- `409` ou erro de negócio: `revision_conflict` / `graph_conflict`; apresentar a escolha de versão com as duas cópias preservadas.
- `editing_session_required` / `lease_required`: forçar renovação via bootstrap.
- Falha de transporte: manter cópia local, desativar consolidação e permitir tentativa posterior.

O monitor de email só conclui com `job.result.mail.accepted === true`. `complete` sem aceitação é erro; `failed` com `will_retry` mantém espera; `failed` com `reconciliation_required` é resultado desconhecido, não convite a repetir; existem `cancelled` e timeout. Aceitação pelo serviço não confirma entrega na caixa. Nas validações com mocks, rotular os resultados como simulados; numa utilização real autorizada, mostrar o estado efetivamente confirmado pelo serviço (`src/web/static/js/mail-job-monitor.js:59`).

## 5. Persistência, conflitos e limites offline

O coordenador atual usa IndexedDB `sensorpoint-service-recovery-v1`, coleção `recoveries`, chave por utilizador/documento e retenção de 30 dias. Guarda `{userId,documentId,fileName,clientId,baseRevision,payload,updatedAt,expiresAt}`. O `clientId` é mantido em sessionStorage. O save local tem atraso de 150 ms; o remoto, 750 ms; novas tentativas crescem até 30 s (`src/web/static/js/editing-coordinator.js:40`, `:188`, `:344`).

Há proteção de múltiplas abas com BroadcastChannel para rascunhos, libertação no `pagehide`/`freeze`, recuperação no `pageshow`/`resume`, persistência ao ocultar a página e tentativa ao regressar online. A escolha de conflito é entre «Usar servidor» e «Usar este dispositivo»; não há fusão por campo. Validar em conjunto com o serviço de edição/revisões incluído e usar mocks para falhas determinísticas. A coordenação no navegador não substitui a validação de concorrência no servidor (`src/web/static/js/editing-coordinator.js:304`, `:675`, `:781`, `:817`).

A PWA não é um editor totalmente offline: o service worker passa APIs diretamente à rede e não guarda páginas autenticadas. Uma navegação sem rede recebe `offline.html`. Uma página já aberta pode preservar dados no IndexedDB, mas as fotografias internas pendentes ficam em memória e o browser pode bloquear/limpar armazenamento. O texto da página offline é mais categórico do que esta garantia; a nova UI deve comunicar o último estado confirmado, em vez de garantir gravação incondicional (`src/web/static/service-worker.js:72`; `src/web/static/offline.html:24`).

O cache estático original tem uma versão fixa anterior à checklist e não lista todos os assets atuais, como os scripts de manutenção e o monitor. Atualizar o manifesto de recursos e a versão ao construir a nova aplicação; não copiar este service worker para a raiz de outro site sem adequar `scope`, caminhos absolutos e estratégia de atualização (`src/web/static/service-worker.js:1`; `src/web/static/js/pwa.js:94`).

## 6. Contrato proposto para separar interface e serviços

Esta secção é proposta de desenvolvimento; não está implementada pelo snapshot.

Uma melhoria possível é criar uma interface `AppAdapter` com métodos `listFiles`, `openDocument`, `autosave`, `closeEditing`, `saveDraft`, `finalize`, `cancel`, `preview`, `signChecklist` e `getOperationStatus`, encapsulando os pedidos HTTP que já ligam a UI ao backend. Um adaptador mock serviria para testes com fixtures. A UI receberia o adaptador por injeção, evitando `fetch` espalhado e URLs de ambiente dentro dos componentes. Esta refatorização não está implementada no pacote nem exige substituir os serviços reais existentes.

Separar o modelo de documento, regras puras, estado de edição, renderização de ecrã e renderização de impressão. Manter catálogos versionados em `dados/`, com identificadores estáveis e correspondência com os schemas Python incluídos. Assinatura, autenticação e tokens de integridade continuam a ser verificados no backend; o mock não deve fingir essas garantias.

No modo de desenvolvimento: utilizadores sintéticos, integrações desligadas, mensagens e anexos apenas preparados localmente, nenhuma credencial, imagem pessoal, ligação SharePoint ou fila operacional. O namespace de IndexedDB/sessionStorage e o service worker devem identificar o novo projeto para não reutilizar dados do original.

## 7. Melhorias de utilização identificadas

Estas são recomendações, não funcionalidades verificadas ou já entregues na aplicação original.

| Prioridade | Melhoria | Critério verificável |
| --- | --- | --- |
| Alta | Tornar explícito o estado «guardado neste dispositivo», «guardado no servidor» e «fotografias por guardar». | Perder ligação/fechar uma aba não resulta em mensagem enganadora nem perda silenciosa. |
| Alta | Resumo persistente de erros com ligações ao campo/secção, incluindo local SADI. | Finalização inválida coloca foco no primeiro problema e permite navegar aos restantes; não depende apenas de toast de 3,5 s. |
| Alta | Salvar/checkpoint de anexos e aviso específico de fotos pendentes. | Fotografias não desaparecem silenciosamente numa navegação confirmada ou recuperação. |
| Alta | Preservar dados e apresentar diferenças antes de resolver conflito. | Escolher uma versão tem pré-visualização do que se perde; manter possibilidade de exportar cópia local. |
| Alta | Distinguir visualmente demo, operação pendente, aceite e concluída. | Nenhuma simulação é apresentada como email real entregue ou assinatura validada externamente. |
| Alta | Resolver limite de materiais no documento impresso. | Uma folha com mais de cinco materiais tem paginação/continuação ou um limite explicado antes de aceitar dados, sem omissões. |
| Média | Acessibilidade completa de separadores SADI, canvas e validação. | Teclado e leitor de ecrã percorrem tarefas; há alternativa/explicação para captura de assinatura quando o dispositivo não permite desenho. |
| Média | Rever mistura de PT/EN. | Campos, ações, validações e impressão mantêm o idioma selecionado; SADI atualmente tem muitos textos fixos em PT. |
| Média | Reduzir dependência de popup para consulta. | Bloqueio de popups mostra alternativa de consulta; impressão não faz perder a edição. |
| Média | Otimizar checklists longas sem perder ordem e contexto. | Muitas centrais/locais continuam utilizáveis em telemóvel; resumo de progresso e nomes de locais orientam navegação. |
| Média | Validar layouts de campo e impressão separadamente. | Ensaios em 360/390/768/1280 px, zoom 200%, textos extensos e PDFs de várias páginas sem cortes. |

A interface já possui CSS responsivo, sidebar móvel, estados `aria-live`, confirmação e bloqueio de ações durante operações. Isso não substitui uma auditoria visual/acessível no novo runtime. Referências: `src/web/static/css/field-app.css:905`, `:975`; `src/web/static/css/maintenance.css:8`; `src/web/templates/document_preview.html:49`.

## 8. Aceitação mínima da aplicação na nova pasta

1. Abrir uma folha fictícia, editá-la, guardar rascunho incompleto e retomá-lo sem alteração do original.
2. Preservar materiais, quatro técnicos, duração corrigida, idioma, ausências, checklists e metadados durante round-trip de edição.
3. Apresentar e resolver cenários offline, revisão divergente, sessão expirada e duas abas com o backend em armazenamento fictício e falhas de transporte simuladas.
4. Exercitar locais/equipamentos SADI, `NC` com justificação, remoção confirmada e invalidação de assinaturas após alteração.
5. Diferenciar fotos internas da folha de fotos no PDF da checklist; verificar anexos pendentes.
6. Pré-visualizar e imprimir folha e cada checklist separadamente com dados sintéticos extensos.
7. Finalizar/cancelar apenas o registo fictício correto, com confirmação e resposta única para repetição da mesma operação.
8. Confirmar por inspeção da configuração, dos serviços injetados e do tráfego de teste que nenhuma comunicação, autenticação ou escrita operacional ocorre na validação.

Este levantamento não executou a aplicação nem os testes originais. Os contratos são evidência de leitura estática e o backend acompanha a migração; os pontos de aceitação acima continuam a exigir ensaio da cópia na nova pasta e correção das lacunas identificadas. A migração não verifica nem ativa produção.
