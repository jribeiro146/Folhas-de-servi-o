# Consulta privada dos trabalhos — M03

## Autorização e produtores

`GET /api/graph/jobs/<job_id>` só devolve o estado ao dono ou ao administrador
Microsoft validado pelo M02. Outro técnico recebe 403; uma sessão Microsoft
terminada recebe 401; um identificador inexistente recebe 404. As respostas
mantêm Cache-Control no-store.

Os quatro produtores web (rascunho, finalização Graph, finalização/email local,
cancelamento) guardam `_owner` no payload interno. O valor vem exclusivamente
da sessão do servidor, nunca de um campo do documento, nome de ficheiro, email,
prefixo do identificador ou dono atual da reserva. Na autenticação Microsoft,
contém provider, tenant_id e object_id normalizados. No modo local/demo, contém
provider local e o identificador da sessão de edição assinada já existente.
Esse modo continua isolado por sessão; a identidade de técnico fictício injetada
explicitamente no lançador sintético mantém o âmbito de demonstração existente.

O proprietário é o autor da operação, não quem abrir a folha depois. Repetir
enqueue com o mesmo job_id conserva o primeiro registo/dono. O filho Teams herda
a identidade do pai na mesma transação que cria a intenção de notificar; a
verificação de conflito e a idempotência continuam válidas. Nenhum email é
repetido para alterar ou recuperar propriedade.

Trabalhos antigos sem `_owner`, dados de dono inválidos e sessões Microsoft
antigas sem tenant não têm prova suficiente para a consulta como dono; ficam
disponíveis ao administrador. Fazer novo login permite atribuir corretamente
os trabalhos novos. A sessão local nova também não herda trabalhos da sessão
anterior. Não há migração automática da propriedade nem inferência pelo email.
O estado/payload original é preservado para diagnóstico/reconciliação administrativa.

## Resposta do monitor

A serialização interna da fila continua a servir worker e reconciliação. A
resposta HTTP passa pela projeção explícita de `graph_job_visibility.py`:

| Campo | Significado |
|---|---|
| id | Identificador do trabalho consultado |
| status | held, pending, running, failed, complete ou cancelled |
| will_retry | Repetição automática prevista |
| reconciliation_required | Resultado incerto que exige prova/reconciliação |
| next_attempt_at | Instante UTC numérico da tentativa prevista, ou null |
| result.phase | Fase conhecida e enumerada; texto livre é omitido |
| result.mail.accepted | Booleano de aceitação do email, quando existe |
| result.commit_guard_required | Confirmação local em falta, quando existe |
| result.invalid_state | Dados inválidos/retidos, quando existe |
| result.accepted | Aceitação Teams, quando existe |
| result.retry_not_before | Prazo Teams numérico, quando existe |
| result.cancellation_confirmed | Confirmação do cancelamento, quando existe |

Payload, dono, attempt_token, ficheiros/caminhos, destinatários, texto bruto de
erro, comprovativos completos, snapshots e campos novos não enumerados não são
copiados para HTTP, mesmo para o administrador nesta rota. A projeção aceita
apenas os tipos esperados e timestamps finitos não negativos. Diagnósticos de
outras rotas/painel continuam a ser o âmbito seguinte M09.

O monitor atual já consome estes campos; não foi reescrito. Mail accepted é
aceitação pelo serviço, não prova de entrega ao destinatário. Complete sem
mail accepted mantém a confirmação em falta; commit_guard_required, invalid_state,
reconciliation_required e next_attempt_at conservam as decisões existentes.
A recuperação após navegação pertence ao M10. GET nunca reenvia o POST de envio.

## Compatibilidade e validação

O metadado `_owner` é aditivo ao JSON interno existente: não altera tabelas,
schema SQLite ou o contrato de armazenamento. A atualização continua a exigir
web/worker da mesma versão segundo o contrato M13; não misturar processos antigos.
Nenhum dado/fila real foi migrado ou consultado.

Testes novos em `tests/test_graph_job_visibility.py`: 22 aprovados em 20,17s
na primeira execução dirigida, em 12/10/2026. Usam Flask e SQLite reais com
dados fictícios, transportes simulados e worker automático desativado. Incluem
os quatro produtores e tentativa de forjar `_owner`, dono/admin/outro tenant,
legados, isolamento local, privacidade de marcadores e herança Teams sem reenvio.
O wrapper de instrumentação em test_durable_teams aceita o novo argumento owner
e mantém a verificação de transação/contrato; o ensaio não foi enfraquecido.

Regressão Python em 12/10/2026: **247 aprovados, 1 ignorado** (permissões POSIX
em Windows), 136,47s. Com PYTEST_DISABLE_PLUGIN_AUTOLOAD=1:

```text
python -B -m pytest -p no:cacheprovider tests/test_graph_job_visibility.py tests/test_durable_teams.py tests/test_durable_mail.py tests/test_graph_finalization_queue.py tests/test_cancellation_recovery.py tests/test_audit_queue_corruption.py tests/test_runtime_admission.py tests/test_runtime_migration.py tests/test_operational_admin.py tests/test_archive_and_web.py -q
node --test tests/js/*.test.cjs
```

Node: **79 aprovados**, zero skips/falhas, 1,16s. Logs `p2-m03-regression-r1.txt`
e `p2-m03-node-r1.txt`. Estes ensaios cobrem também retoma Teams, preservação dos
payloads internos, recibos, quarentena, admissão de versões e monitor existente.
Não equivalem à matriz de navegador com worker separado do M16.

Os resultados de regressão, Node, commit e parecer/check são registados na
passagem P2 e no ESTADO/REGISTO. Logs locais `qa-output/consolidacao-20261007/p2-m03-*.txt`
na raiz Migração. Testes com runtime temporário antes de importar a aplicação e
saída de rede bloqueada. Sem publicação, login real ou efeitos operacionais.
