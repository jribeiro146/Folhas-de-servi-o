# Backend dos formulários incluído

Esta nota corrige o âmbito da primeira preparação: **apenas a administração fica de fora**. A pasta `aplicacao/` contém o servidor real que suporta a UI.

## Componentes transportados

| Função | Implementação |
|---|---|
| Rotas e composição Flask | `src/web/application.py`, `src/web/app.py` |
| Regras e dados | `document_schema.py`, `maintenance_schema.py`, `field_map.py` |
| Excel e ficheiros | `excel_service.py`, `file_service.py`, `active_file_index.py`, `file_diagnostics.py` |
| Edição, revisão e recuperação | `editing_state_service.py`, `editing_state_repository.py`, `file_mutex.py`, `local_changes.py` |
| Assinaturas e fotografias | `signature_service.py`, `photo_attachment_service.py` |
| Finalização e documentos | `finalization_service.py`, `archive_service.py`, `document_*`, `maintenance_artifact_service.py`, `local_pdf_service.py` |
| Microsoft | `microsoft_auth_service.py`, `graph_storage_service.py`, `graph_mail_service.py`, `work_folder_service.py` |
| Filas e avisos | `graph_sync_queue.py`, `graph_sync_coordinator.py`, `teams_notification_service.py`, `src/queue_worker.py` |
| Segurança e diagnóstico | `runtime_safety.py`, `logging_config.py` |
| Arranque | `src/main.py`, `passenger_wsgi.py`, `tools/run_test_version.py` |

Todos os imports locais em `src/` pertencem à própria aplicação. Não é necessário importar o Registo Admin nem o workbook mestre administrativo. O catálogo SADI está no código; não é necessário um `SADI.xlsx` externo para executar a demo.

## Execução recomendada para desenvolver

Na pasta `aplicacao/`, usar `python -B tools/run_test_version.py --check` para diagnóstico sem servidor ou remover `--check` para abrir a app em `http://127.0.0.1:5012`.

O lançador configura os caminhos, remove variáveis operacionais herdadas, aponta para um `.env` inexistente, cria dados sintéticos e bloqueia a rede antes de importar Flask/app. Email, Teams e worker web ficam desligados. Os ficheiros/base/fila temporários pertencem exclusivamente ao arranque.

A configuração de origem foi adaptada na cópia: sem overrides, o armazenamento usa uma área `Sensorpoint/FolhasServicoMigracao/<identificador-da-instalação>` em LOCALAPPDATA ou no diretório temporário. Não usa a área `Sensorpoint/FolhasServico` da instalação original. Isso evita referências à máquina de origem; o lançador de teste continua a ser a opção preferida e cria uma pasta nova em cada execução.

## Integrações e segredos

A implementação de Graph/email/login/Teams está presente, mas os valores secretos ficam no JSON privado de transferência exterior ao pacote. Nenhum código o lê automaticamente. Os testes não precisam dele.

Preparar uma instalação real requer configurar o `.env`/ambiente privado do servidor, a URI de retorno Microsoft, os diretórios de dados e os destinos Graph. Credenciais presentes não garantem validade ou permissões. Não carregar a configuração privada no browser nem em builds públicos.

O rodapé corporativo do email foi incluído como asset porque o serviço verifica a existência do JPEG antes de preparar mensagens. Não foram transportadas assinaturas de clientes nem anexos de intervenções reais.

## PDFs e alojamento

- PDF local requer Chrome/Chromium/Edge; `FS_PDF_BROWSER_PATH` pode indicar o executável. A validação automatizada usa renderizadores simulados onde previsto nos testes.
- O Dockerfile e Compose foram atualizados em 28/09/2026: Chromium e HOME gravável para utilizador sem privilégios, web e worker com a mesma imagem/configuração/volume, consumidor da fila no web desligado. A construção da imagem ainda não foi executada neste computador, sem Docker. O `.env` privado continua a ser necessário apenas na instalação configurada. Consultar `10_ALOJAMENTO_DOCKER.md`.
- `gunicorn` é instalado pelo Dockerfile. Para WSGI fora de Docker, instalar o servidor WSGI escolhido separadamente, além de `requirements-server.txt`.
- Não montar filas/volumes antigos em testes nem iniciar `src.queue_worker` contra dados operacionais. A mesma imagem/código deve ser usada para web e worker quando essa arquitetura for configurada e autorizada.
- Não aplicar automaticamente as opções de sandbox/HOME das notas de deploy. Essas notas referem outro ambiente e outra revisão; validar o destino.

## Diferenças de versão que continuam por resolver

A cópia local usa `MAINTENANCE_DEMO` para SADI. As notas de deploy existentes na raiz Migração referem uma versão de servidor com opções adicionais e correções próprias. Não foram importadas alterações desconhecidas desse servidor nem afirmado que já estão presentes aqui.

O estado atualizado das melhorias de impressão, PWA, cabeçalho móvel e listagem SharePoint consta de `09_MELHORIAS_E_VALIDACAO.md`. Os documentos 02–04 conservam o levantamento original; não devem ser lidos como uma lista atual de falhas ainda existentes. A ativação operacional da SADI, os testes em dispositivos físicos e a comparação com o servidor continuam separados da validação local.
