# Queixas, causas demonstradas e regressões — M17

Estado de desenvolvimento em 14/10/2026. Base auditada: esta árvore de produção;
o pacote/demo tem diferenças históricas e não a substitui. Os commits abaixo são
identificadores desta árvore. A prova sintética demonstra o mecanismo do defeito
ou o comportamento esperado; não demonstra que um incidente passado tenha tido
a mesma causa. Sem uma referência de pedido/operação e evidência correspondente,
a causa de uma queixa histórica permanece por confirmar.

## Mapa de diagnóstico e rastreabilidade

| Sintoma/queixa | Causa demonstrada ou limite | Correção/comportamento e commits | Regressão verificável |
|---|---|---|---|
| Folhas eliminadas no SharePoint continuam na lista | Enumerar a cache local pode mostrar ficheiros que já não pertencem ao inventário remoto. Demonstrado com 65 ficheiros em cache e apenas 25 confirmados; atribuição ao incidente histórico por confirmar. | Visibilidade pelo inventário confirmado, sem apagar trabalho local para ocultá-lo. `825929f`, reconciliado em `e7399a5`. | `test_active_file_index.py`: `test_65_cached_sheets_show_only_23_remote_bases_and_two_drafts`, remoção da base/Excel dentro da pasta e falha de refresh. |
| A lista falha quando um ficheiro desaparece durante a leitura | Corrida reproduzida entre enumeração, stat e leitura Excel. | Ignorar apenas desaparecimento confirmado e conservar outras entradas; erro de permissões continua a ser erro. `2692672`. | `test_file_listing_races.py`: remoção depois de enumerar, durante leitura, inventário alterado e erro de permissões. |
| Atualização lenta/falhada deixa cartões antigos ou perde o formulário | Um refresh iniciado não equivale a uma lista nova confirmada; respostas de gerações distintas não provam a conclusão pedida. | Aplicar inventário confirmado mesmo após polling lento, indicar falha/desatualização e preservar formulário. `2502df5`, `e7399a5`; prova no navegador em `a2b40e95`. | `test_graph_refresh_coordinator.py`, `test_file_refresh_api.py`, `tests/js/active-file-list.test.cjs`; grupo de refresh falhado do M16. |
| Não se consegue criar/guardar o rascunho | Conflitos 412 na renomeação de pasta Graph e pedidos de resultado perdido têm reproduções próprias. Não provar que todos os erros de rascunho têm esta causa. | Recuperar apenas a pasta cuja propriedade/identidade é comprovada; manter conflitos reais. `a1cac3c`, `332240d`, `0e6d61f`. | `test_graph_publication_recovery.py`: recuperação após 412/resposta perdida e recusa de pasta de outra pessoa; `test_graph_folder_creation.py`. |
| Finalização fica bloqueada depois de várias gravações | Snapshots de upload anteriores podem ficar pendentes após gravação/publicação mais recente. O bloqueio também é legítimo se a publicação atual não está concluída. | Preservar ordem, não publicar snapshot antigo sobre novo, retirar apenas trabalhos comprovadamente ultrapassados. `1c3d350`, `29489fa`; gate de finalização coberto na base atual. | `test_graph_publication_recovery.py`: duas gravações, conflito real, upload parcial e versões antigas; `test_maintenance_access.py::test_real_queue_blocks_finalization_until_draft_upload_finishes`. |
| Texto desaparece ao recarregar depois de erro ao guardar | M16 demonstrou Excel escrito + JSON com ENOSPC; o fingerprint classificava a própria escrita parcial como alteração externa e apagava autosave. Causa histórica por confirmar. | Reconhecer a escrita própria sob bloqueios, manter autosave/revisão e estado falhado; alteração externa posterior continua a invalidar. `a2b40e95`. | `test_draft_partial_failure.py`; ensaio Chromium com ENOSPC/reload/repetição em `tools/check_integrated_journeys.py`. |
| Upload do arquivo falha e o painel não deixa repetir o email | M17 reproduziu três falhas antes do PDF/envio: nenhum email tentado, mas a ausência de fase parecia um envio legado incerto. | Checkpoint `uploading_archive` antes do upload, projeção pública e classificação administrativa coerentes. `8a8878ed3008066792319ad5fa8f7811b822911c`. | `test_archive_preparation.py`: esgotamento, morte real do processo, falha do checkpoint e legados incertos; `tests/js/mail-job-monitor.test.cjs`. |
| Email não chega, estado parece parado ou pode ser repetido | Aceitação do fornecedor não prova entrega. Resposta perdida depois do pedido é resultado incerto; upload/PDF falhado antes do pedido é outra situação. | Checkpoint antes do envio, bytes fixados, recibo durável, Teams separado e consulta sem repetir POST. `56ddc2c`, `62d1734`, `2a13fd8`, `973374d`, `be7101d`. | `test_durable_mail.py`, `test_durable_teams.py`, `test_job_reconciliation.py`; M16 mata o worker após recibo e confirma um email/um Teams simulados. |
| HTTP 400 ao finalizar | Campos obrigatórios ausentes, assinatura exigida ou total manual inválido são recusas esperadas. O número HTTP, isoladamente, não identifica um defeito. | Rascunho admite incompletude; envio valida antes de arquivar. Regras já existentes na base, com metadados de edição em `f409af0b`/`5303efcf` e diagnósticos em `3b1c321`. | `test_archive_and_web.py`: `test_web_api_rejects_missing_required_fields`, `test_draft_does_not_require_all_fields`, cliente presente/ausente, total inválido; `tests/js/document-validation.test.cjs`. |
| Assinatura SADI/retoma parece incorreta | A matriz própria pertence a M18. M22 ainda tem um P3 conhecido: bootstrap offline, três abas, retry oculto até recarregar; cópia IndexedDB preservada. | M22 não está concluído/aprovado. Não confundir a prova dos percursos M16 com fecho desse caso. | `tests/js/sadi-recovery.test.cjs` cobre variantes anteriores; a reprodução adicional de bootstrap offline está registada na nota de passagem. |

O template atual carrega `document-editor.js`, `editing-coordinator.js` e os
módulos auxiliares. `field-app.js` é legado; a sua presença no disco não demonstra
que execute no percurso que falhou. Não atribuir uma falha atual a esse ficheiro
sem confirmar o HTML/código realmente servido e a identidade da versão.

## Recuperação do upload anterior ao email

O checkpoint novo só é gravado durante a execução admitida de
`archive_and_remove` com email, antes de `upload_archive_bundle` e quando ainda
não existe mensagem preparada. A escrita é durável e delimitada pelo token da
tentativa. Se não for possível persistir, não começa o upload. Se já existe uma
mensagem fixada/recibo, a execução conserva a recuperação existente, sem refazer
PDF ou enviar de novo por causa desta alteração.

Quando a falha esgota as tentativas, a administração permite «Repetir trabalho»;
a ação é auditada e não envia imediatamente: volta à fila. Um processo morto
durante o upload fica retido ao expirar a atividade e admite a mesma recuperação
manual. O upload pode ter tido efeitos parciais no arquivo, mas o email ainda não
foi tentado. As guardas de publicação/identidade Graph continuam aplicáveis.

O monitor do técnico recebe uma falha anterior ao envio, sem afirmar aceitação.
A reconciliação não oferece decisões para este caso conhecido como não enviado.
Não se preenchem fases em registos antigos por inferência. Trabalhos legados
tentados sem fase, `sending_mail` e `mail_unknown` continuam recusados nas duas
rotas HTTP de repetição; a inspeção conserva a via de reconciliação existente.
Falhas anteriores à gravação do checkpoint mantêm o tratamento conservador.
Não editar SQLite à mão para transformar um resultado incerto nesta fase.

## Evidência desta alteração e como repetir

O teste de esgotamento falhou antes da correção precisamente em `can_retry=False`,
depois de três uploads falhados e zero chamadas de email. Depois passaram seis
cenários dirigidos. A bateria relacionada passou 475 testes Python, com um skip
POSIX no Windows (144,44 s); os percursos HTTP e a regressão M16 passaram mais 32
(42,09 s). JavaScript: 175 aprovados (4,95 s). Logs locais:
`qa-output/consolidacao-20261007/p2-m17-{red,green,python-r1,web-r1,js-r1}.txt`.
Estes resultados correspondem ao código de `8a8878ed`; a documentação posterior
não altera o código testado. O parecer/check da revisão cumulativa fica no registo
de colaboração, identificado pelo commit final do pacote.

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -B -m pytest -p no:cacheprovider tests/test_archive_preparation.py tests/test_graph_finalization_queue.py tests/test_durable_mail.py tests/test_durable_teams.py tests/test_job_reconciliation.py tests/test_operational_admin.py tests/test_graph_job_visibility.py tests/test_graph_publication_recovery.py tests/test_archive_and_web.py tests/test_draft_partial_failure.py -q
node --test tests/js/*.test.cjs
```

Usar o ambiente de QA e o isolamento de `tests/conftest.py`, com rede bloqueada
e dados fictícios. A suite completa de M16 no commit anterior tinha 1519 aprovados
e quatro skips; não apresentar esse número como nova execução completa M17.
O Docker de M15 também continua identificado pelo seu SHA próprio. Não houve
ensaio operacional nem comunicação real. Para um incidente novo, recolher apenas
versão, referência de pedido/operação, estado e passo que falhou nos canais
autorizados; não copiar documentos, destinatários ou segredos para pacotes de
revisão. Consultar os guias de [percursos](PERCURSOS_INTEGRADOS.md) e
[reconciliação](RECONCILIACAO_TRABALHOS.md).
