# Reconciliação da versão de produção — 07/10/2026

Base: `0cb5a54`. Branch local: `codex/producao-consolidada-20261007`. O utilizador suspendeu qualquer publicação no GitHub. Não há pressuposto de ancestral comum com o pacote exterior modificado.

Os ficheiros iguais são preservados. A tabela identifica decisões para diferenças de conteúdo; a execução e evidência de cada funcionalidade constarão do registo de implementação. 'Comparar cobertura' é trabalho pendente, não integração concluída.

| Ficheiro | Estado | Decisão | Razão |
|---|---|---|---|
| `.dockerignore` | diferente | produção | Conservar base auditada. |
| `.env.example` | diferente | produção / atualizar | Manter documentação/configuração operacional; atualizar apenas para decisões implementadas e CI local preparado sem publicar. |
| `.env.production.example` | só produção | produção / atualizar | Manter documentação/configuração operacional; atualizar apenas para decisões implementadas e CI local preparado sem publicar. |
| `.github/workflows/production-validation.yml` | só produção | produção / atualizar | Manter documentação/configuração operacional; atualizar apenas para decisões implementadas e CI local preparado sem publicar. |
| `.gitignore` | diferente | produção | Conservar base auditada. |
| `Dockerfile` | diferente | produção / atualizar | Manter documentação/configuração operacional; atualizar apenas para decisões implementadas e CI local preparado sem publicar. |
| `README.md` | só produção | produção / atualizar | Manter documentação/configuração operacional; atualizar apenas para decisões implementadas e CI local preparado sem publicar. |
| `docker-compose.yml` | diferente | produção / atualizar | Manter documentação/configuração operacional; atualizar apenas para decisões implementadas e CI local preparado sem publicar. |
| `docs/10_ALOJAMENTO_DOCKER.md` | só produção | produção / atualizar | Manter documentação/configuração operacional; atualizar apenas para decisões implementadas e CI local preparado sem publicar. |
| `docs/DEMO_CHECKLIST_SADI.md` | só produção | produção / atualizar | Manter documentação/configuração operacional; atualizar apenas para decisões implementadas e CI local preparado sem publicar. |
| `docs/EMAIL_GRAPH_SETUP.md` | só produção | produção / atualizar | Manter documentação/configuração operacional; atualizar apenas para decisões implementadas e CI local preparado sem publicar. |
| `docs/ENTREGA_PRODUCAO_2026-09-28.md` | só produção | produção / atualizar | Manter documentação/configuração operacional; atualizar apenas para decisões implementadas e CI local preparado sem publicar. |
| `docs/Estado_Atual_WebApp.md` | só produção | produção / atualizar | Manter documentação/configuração operacional; atualizar apenas para decisões implementadas e CI local preparado sem publicar. |
| `docs/GRAPH_SETUP.md` | só produção | produção / atualizar | Manter documentação/configuração operacional; atualizar apenas para decisões implementadas e CI local preparado sem publicar. |
| `docs/IMPLEMENTACAO_SERVIDOR.md` | só produção | produção / atualizar | Manter documentação/configuração operacional; atualizar apenas para decisões implementadas e CI local preparado sem publicar. |
| `docs/LOGGING.md` | só produção | produção / atualizar | Manter documentação/configuração operacional; atualizar apenas para decisões implementadas e CI local preparado sem publicar. |
| `docs/MICROSOFT_LOGIN_SETUP.md` | só produção | produção / atualizar | Manter documentação/configuração operacional; atualizar apenas para decisões implementadas e CI local preparado sem publicar. |
| `docs/Projecto.md` | só produção | produção / atualizar | Manter documentação/configuração operacional; atualizar apenas para decisões implementadas e CI local preparado sem publicar. |
| `docs/RELEASE_NOTES_SADI_DEMO_2026-09-24.md` | só produção | produção / atualizar | Manter documentação/configuração operacional; atualizar apenas para decisões implementadas e CI local preparado sem publicar. |
| `docs/SINCRONIZACAO_V2_ADMIN.md` | só produção | produção / atualizar | Manter documentação/configuração operacional; atualizar apenas para decisões implementadas e CI local preparado sem publicar. |
| `docs/proposta-features-2026-07/RELATORIO.md` | só produção | produção / atualizar | Manter documentação/configuração operacional; atualizar apenas para decisões implementadas e CI local preparado sem publicar. |
| `docs/proposta-features-2026-07/mockup-01-materiais.svg` | só produção | produção / atualizar | Manter documentação/configuração operacional; atualizar apenas para decisões implementadas e CI local preparado sem publicar. |
| `docs/proposta-features-2026-07/mockup-02-horas-editaveis.svg` | só produção | produção / atualizar | Manter documentação/configuração operacional; atualizar apenas para decisões implementadas e CI local preparado sem publicar. |
| `docs/proposta-features-2026-07/mockup-03-sistemas.svg` | só produção | produção / atualizar | Manter documentação/configuração operacional; atualizar apenas para decisões implementadas e CI local preparado sem publicar. |
| `docs/proposta-features-2026-07/mockup-04-cliente-ausente.svg` | só produção | produção / atualizar | Manter documentação/configuração operacional; atualizar apenas para decisões implementadas e CI local preparado sem publicar. |
| `docs/proposta-features-2026-07/mockup-05-recuperacao-multiutilizador.svg` | só produção | produção / atualizar | Manter documentação/configuração operacional; atualizar apenas para decisões implementadas e CI local preparado sem publicar. |
| `requirements-dev.txt` | só pacote | produção / atualizar | Manter documentação/configuração operacional; atualizar apenas para decisões implementadas e CI local preparado sem publicar. |
| `requirements.txt` | diferente | produção / atualizar | Manter documentação/configuração operacional; atualizar apenas para decisões implementadas e CI local preparado sem publicar. |
| `src/config.py` | diferente | reconciliar | Preservar SADI/Graph produção; importar configuração explícita de assinatura e manter demo fora da release operacional. |
| `src/maintenance_schema.py` | diferente | produção | Preservar cliente ausente, versão e validade das assinaturas SADI. |
| `src/services/atomic_files.py` | só produção | produção | Conservar base auditada. |
| `src/services/bundle_removals.py` | só produção | produção | Conservar base auditada. |
| `src/services/editing_state_service.py` | diferente | produção | Preservar autorização SADI de acesso partilhado e administrador já sincronizado. |
| `src/services/excel_service.py` | diferente | importar melhoria | Leitura LINK em passagens limitadas; verificar igualdade e libertação do ficheiro. |
| `src/services/graph_mail_service.py` | diferente | reconciliar | Transportes e envelope/limites do pacote, conservando comprovativos e estado incerto da produção; imagem privada opcional. |
| `src/services/graph_storage_service.py` | diferente | produção com revisão | Preservar propriedade de arquivos e remoções explícitas; verificar diferenças e guardas por cenário. |
| `src/services/graph_sync_queue.py` | diferente | produção com revisão | Preservar auditoria da fila; acrescentar recuperação Teams sem substituir pela fila anterior do pacote. |
| `src/services/local_changes.py` | diferente | produção | Preservar eliminação de marcador remoto antigo quando há edição local atual. |
| `src/services/maintenance_access.py` | só produção | produção | Conservar base auditada. |
| `src/services/maintenance_artifact_service.py` | diferente | produção | Preservar arquivo privado e regras SADI operacionais. |
| `src/services/maintenance_private_service.py` | só produção | produção | Conservar base auditada. |
| `src/services/photo_attachment_service.py` | diferente | produção | Pacote coincide com referência anterior à remoção explícita/versionada; não regredir. |
| `src/services/runtime_safety.py` | diferente | reconciliar | Preservar modo produção/teste; guardas diretas de transporte. Demo continua separado. |
| `src/services/signature_service.py` | diferente | produção | Pacote coincide com referência anterior à remoção explícita/versionada; não regredir. |
| `src/services/teams_notification_service.py` | diferente | reconciliar | Transporte injetável e guarda antes de I/O; resultado incerto explícito. |
| `src/web/application.py` | diferente | produção com revisão | Preservar SADI, validação, dados privados, administrador e feedback; só integrar diferenças funcionais selecionadas. |
| `src/web/static/css/field-app.css` | diferente | produção | Preservar interface operacional SADI; diferenças demo não são base de release. |
| `src/web/static/css/maintenance.css` | diferente | produção | Preservar avisos e assinaturas SADI operacionais. |
| `src/web/static/img/assinatura-dora.jpg` | só pacote | não importar binário | Não publicar automaticamente; suporte futuro por recurso privado configurável. |
| `src/web/static/js/document-editor.js` | diferente | produção | Preservar feedback de rascunho/publicação pendente; testes de timeout já nas duas árvores. |
| `src/web/static/js/mail-job-monitor.js` | diferente | produção com revisão | Rever semântica de notificações e estados no fluxo consolidado. |
| `src/web/static/js/maintenance-editor.js` | diferente | produção | Preservar assinaturas e cliente ausente. |
| `src/web/static/js/maintenance-model.js` | diferente | produção | Preservar modelo operacional e compatibilidade SADI. |
| `src/web/templates/field_app.html` | diferente | produção | Preservar ativação e acesso operacional SADI. |
| `src/web/templates/login.html` | diferente | produção | Banner demo pertence ao perfil demo. |
| `src/web/templates/maintenance_document.html` | diferente | produção | Preservar PDF e privacidade SADI. |
| `src/web/templates/maintenance_private_bundle.html` | só produção | produção | Conservar base auditada. |
| `tests/fixtures/sadi-private-1ede4d8.json` | só produção | comparar cobertura | Preservar regressões produção e transportar cenários úteis com dados sintéticos; não copiar fixtures binárias sem proveniência. |
| `tests/fixtures/sadi-private-1ede4d8.provenance.json` | só produção | comparar cobertura | Preservar regressões produção e transportar cenários úteis com dados sintéticos; não copiar fixtures binárias sem proveniência. |
| `tests/fixtures/test_sample.xlsx` | diferente | comparar cobertura | Preservar regressões produção e transportar cenários úteis com dados sintéticos; não copiar fixtures binárias sem proveniência. |
| `tests/js/active-file-list.test.cjs` | diferente | comparar cobertura | Preservar regressões produção e transportar cenários úteis com dados sintéticos; não copiar fixtures binárias sem proveniência. |
| `tests/js/draft-save-feedback.test.cjs` | só produção | comparar cobertura | Preservar regressões produção e transportar cenários úteis com dados sintéticos; não copiar fixtures binárias sem proveniência. |
| `tests/js/mail-job-monitor.test.cjs` | diferente | comparar cobertura | Preservar regressões produção e transportar cenários úteis com dados sintéticos; não copiar fixtures binárias sem proveniência. |
| `tests/js/maintenance-model.test.cjs` | diferente | comparar cobertura | Preservar regressões produção e transportar cenários úteis com dados sintéticos; não copiar fixtures binárias sem proveniência. |
| `tests/js/maintenance-notice.test.cjs` | só produção | comparar cobertura | Preservar regressões produção e transportar cenários úteis com dados sintéticos; não copiar fixtures binárias sem proveniência. |
| `tests/js/migration-fixtures.test.cjs` | só pacote | comparar cobertura | Preservar regressões produção e transportar cenários úteis com dados sintéticos; não copiar fixtures binárias sem proveniência. |
| `tests/test_archive_and_web.py` | diferente | comparar cobertura | Preservar regressões produção e transportar cenários úteis com dados sintéticos; não copiar fixtures binárias sem proveniência. |
| `tests/test_audit_archive_ownership.py` | só produção | comparar cobertura | Preservar regressões produção e transportar cenários úteis com dados sintéticos; não copiar fixtures binárias sem proveniência. |
| `tests/test_audit_atomic_files.py` | só produção | comparar cobertura | Preservar regressões produção e transportar cenários úteis com dados sintéticos; não copiar fixtures binárias sem proveniência. |
| `tests/test_audit_mail_recovery.py` | só produção | comparar cobertura | Preservar regressões produção e transportar cenários úteis com dados sintéticos; não copiar fixtures binárias sem proveniência. |
| `tests/test_audit_mail_test_mode.py` | só produção | comparar cobertura | Preservar regressões produção e transportar cenários úteis com dados sintéticos; não copiar fixtures binárias sem proveniência. |
| `tests/test_audit_queue_corruption.py` | só produção | comparar cobertura | Preservar regressões produção e transportar cenários úteis com dados sintéticos; não copiar fixtures binárias sem proveniência. |
| `tests/test_audit_removals.py` | só produção | comparar cobertura | Preservar regressões produção e transportar cenários úteis com dados sintéticos; não copiar fixtures binárias sem proveniência. |
| `tests/test_audit_worker_ownership.py` | só produção | comparar cobertura | Preservar regressões produção e transportar cenários úteis com dados sintéticos; não copiar fixtures binárias sem proveniência. |
| `tests/test_config.py` | diferente | comparar cobertura | Preservar regressões produção e transportar cenários úteis com dados sintéticos; não copiar fixtures binárias sem proveniência. |
| `tests/test_demo_mail_policy.py` | só pacote | comparar cobertura | Preservar regressões produção e transportar cenários úteis com dados sintéticos; não copiar fixtures binárias sem proveniência. |
| `tests/test_demo_runtime.py` | só pacote | comparar cobertura | Preservar regressões produção e transportar cenários úteis com dados sintéticos; não copiar fixtures binárias sem proveniência. |
| `tests/test_document_schema.py` | diferente | comparar cobertura | Preservar regressões produção e transportar cenários úteis com dados sintéticos; não copiar fixtures binárias sem proveniência. |
| `tests/test_editing_state_service.py` | diferente | comparar cobertura | Preservar regressões produção e transportar cenários úteis com dados sintéticos; não copiar fixtures binárias sem proveniência. |
| `tests/test_excel_service.py` | diferente | comparar cobertura | Preservar regressões produção e transportar cenários úteis com dados sintéticos; não copiar fixtures binárias sem proveniência. |
| `tests/test_file_refresh_api.py` | diferente | comparar cobertura | Preservar regressões produção e transportar cenários úteis com dados sintéticos; não copiar fixtures binárias sem proveniência. |
| `tests/test_graph_finalization_queue.py` | só produção | comparar cobertura | Preservar regressões produção e transportar cenários úteis com dados sintéticos; não copiar fixtures binárias sem proveniência. |
| `tests/test_graph_mail_service.py` | diferente | comparar cobertura | Preservar regressões produção e transportar cenários úteis com dados sintéticos; não copiar fixtures binárias sem proveniência. |
| `tests/test_graph_publication_recovery.py` | só produção | comparar cobertura | Preservar regressões produção e transportar cenários úteis com dados sintéticos; não copiar fixtures binárias sem proveniência. |
| `tests/test_maintenance.py` | diferente | comparar cobertura | Preservar regressões produção e transportar cenários úteis com dados sintéticos; não copiar fixtures binárias sem proveniência. |
| `tests/test_maintenance_access.py` | só produção | comparar cobertura | Preservar regressões produção e transportar cenários úteis com dados sintéticos; não copiar fixtures binárias sem proveniência. |
| `tests/test_photo_attachments.py` | diferente | comparar cobertura | Preservar regressões produção e transportar cenários úteis com dados sintéticos; não copiar fixtures binárias sem proveniência. |
| `tests/test_safety_regressions.py` | só pacote | comparar cobertura | Preservar regressões produção e transportar cenários úteis com dados sintéticos; não copiar fixtures binárias sem proveniência. |
| `tests/test_sync_v2.py` | diferente | comparar cobertura | Preservar regressões produção e transportar cenários úteis com dados sintéticos; não copiar fixtures binárias sem proveniência. |
| `tests/test_teams_notification_service.py` | diferente | comparar cobertura | Preservar regressões produção e transportar cenários úteis com dados sintéticos; não copiar fixtures binárias sem proveniência. |
| `tools/build_legacy_sadi_fixture.py` | só produção | produção / selecionar | Ferramentas de demo/migração do pacote não substituem os lançadores/testes da produção. |
| `tools/build_synthetic_fixture.py` | só pacote | produção / selecionar | Ferramentas de demo/migração do pacote não substituem os lançadores/testes da produção. |
| `tools/demo_runtime.py` | só pacote | produção / selecionar | Ferramentas de demo/migração do pacote não substituem os lançadores/testes da produção. |
| `tools/run_demo_version.py` | só pacote | produção / selecionar | Ferramentas de demo/migração do pacote não substituem os lançadores/testes da produção. |
| `tools/smoke_pdf.py` | diferente | produção / selecionar | Ferramentas de demo/migração do pacote não substituem os lançadores/testes da produção. |
| `tools/validate_migration.py` | só pacote | produção / selecionar | Ferramentas de demo/migração do pacote não substituem os lançadores/testes da produção. |
| `tools/validate_reports.py` | diferente | produção / selecionar | Ferramentas de demo/migração do pacote não substituem os lançadores/testes da produção. |
| `tools/verify_sadi_pdf_demo.py` | só produção | produção / selecionar | Ferramentas de demo/migração do pacote não substituem os lançadores/testes da produção. |
