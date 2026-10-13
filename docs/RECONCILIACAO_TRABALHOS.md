# M08 — Reconciliação administrativa por operação

## Decidir sem repetir um resultado desconhecido

João, autenticado com a identidade administrativa estável, pode consultar uma
operação no painel e registar aceitação comprovada, recusa comprovada ou resultado
indeterminado. O painel também permite procurar um identificador de operação já
concluída para recuperar apenas a intenção Teams de um email aceite.

A reconciliação não chama transportes. A aceitação comprovada encerra o trabalho;
no email, conserva o recibo/auditoria, retira os bytes de mensagem como no fluxo
normal e cria a intenção Teams na mesma transação, se estava prevista. Essa
intenção pode depois ser executada pelo worker normal. Aceitação significa
aceitação pelo fornecedor, não entrega ao destinatário nem publicação efetiva
no canal Teams. Uma recusa comprovada mantém o trabalho falhado/parado; exige
uma ação separada de repetição, com novo motivo e as guardas existentes. O email
usa os mesmos bytes fixados. Prazos 429 continuam a bloquear repetição antecipada.

Indeterminado conserva bytes, evidência anterior e bloqueio. Não localizar uma
mensagem, receber 500/408/425 ou ter apenas a opinião de que o envio falhou não
comprova recusa. Estado inválido, migração/restauro incertos e falta de confirmação
do commit local não são desbloqueados por esta ferramenta. Podem receber uma
nota indeterminada, mas mantêm o procedimento de recuperação próprio.

## Evidência humana e verificações automáticas

O administrador obtém o registo no fornecedor, verifica a origem e transcreve
os campos relevantes para um JSON até 64 KiB. `reviewed_from_provider: true` é uma
declaração explícita de revisão humana. A aplicação **não autentica assinaturas
do fornecedor nem consulta Microsoft Graph/Exchange/Workflows** nesta ação.
Confere consistência com os dados locais, identidade, conteúdo, correlação e
estado atual. Um JSON fabricado por um administrador malicioso não é distinguido
de uma transcrição verdadeira. Não apresentar esta validação como prova
criptográfica ou recolha independente. A autoridade é o administrador restrito
M02, com rasto durável da decisão. Se não puder verificar a origem, escolher
indeterminado; não preencher campos com valores esperados só para obter aprovação.

O formato mínimo é:

```json
{
  "source": "exchange_sent_message",
  "reference": "Referência privada do registo conservado pelo administrador",
  "reviewed_from_provider": true,
  "record": {
    "operation_id": "IDENTIFICADOR-EXATO-DA-OPERACAO",
    "outcome": "accepted",
    "observed_at": 0,
    "provider_record_id": "IDENTIFICADOR-DO-REGISTO-REAL"
  }
}
```

Este exemplo está deliberadamente incompleto e não permite fechar uma operação.
`observed_at` é o instante UTC, em segundos Unix, em que o registo foi observado;
tem de estar entre a criação da operação e agora (tolerância de 300 s para relógio).
`outcome` tem de coincidir com a decisão selecionada: `accepted` ou `rejected`.
Campos adicionais obrigatórios:

| Efeito/decisão | Fonte e campos do registo |
|---|---|
| Email aceite | `source=exchange_sent_message`; registo de mensagem enviada, `is_draft=false`, `sender`, listas `to`/`cc`, `subject`, `attachment_sha256` do PDF original em bytes; identificador da mensagem e cabeçalho `x-sensorpoint-operation-id` iguais à operação. Endereços comparados sem distinção de capitalização; assunto e SHA256 exatos. |
| Email recusado | `source=provider_response`; `http_status` de recusa 4xx, `request_id` do último pedido e `payload_sha256` dos bytes exatos do corpo JSON enviado. Não se reconstrói o JSON a partir de uma mensagem exportada para obter esse hash. Sem acesso ao registo correspondente do pedido, manter indeterminado. |
| Teams aceite | `source=workflow_history`; `http_status` 2xx, `payload_sha256` da carga da notificação e operação igual ao cabeçalho `X-Sensorpoint-Operation-Id`; identificador do registo/execução Workflow. O hash usa JSON canónico: chaves ordenadas, separadores vírgula/dois-pontos sem espaços, Unicode UTF-8 sem escape ASCII e sem NaN. |
| Teams recusado | `source=provider_response`; mesmos operação/hash, `http_status` 4xx e `request_id` do último pedido. |
| Cancelamento confirmado | `source=graph_item`; observação do mesmo `item_id` e `drive_id` guardados no plano; `name` igual ao destino aleatório planeado, `parent_path` igual à raiz de arquivo/Canceladas e `parent_id` não vazio. Não aceitar um homónimo. Nenhum PATCH/DELETE é feito pela reconciliação. |
| Cancelamento recusado | `source=provider_response`; `item_id`, `drive_id`, `if_match` original, `request_id` do último PATCH e `http_status` 409/412. A decisão mantém o trabalho falhado e o conflito visível; uma repetição separada volta a validar identidade, versão e inventário antes de mover. |

Para 429, é também obrigatório `retry_not_before`, instante Unix correspondente
ao prazo fornecido. Não abreviar esse prazo. Os códigos 408/425 e todos os 5xx
são insuficientes para classificar recusa. O registo integral deve permanecer
acessível ao administrador pela referência usada; o JSON não deve incluir tokens,
cookies, URLs assinadas, corpos de mensagens ou PDFs. Campos de envelope ficam
apenas no registo privado da decisão, necessários à conferência do email.

## Correlação de cada tentativa

Cada novo POST de email/Teams e PATCH de cancelamento recebe um GUID aleatório
próprio, gravado antes do transporte em `delivery_request_id`. É diferente do
`attempt_token` interno do worker, que continua privado. Usa `client-request-id`
no Graph e `X-Sensorpoint-Request-Id` no Workflow. Não altera bytes da mensagem,
permissões ou o destino. O administrador consulta este GUID no diálogo para
localizar o pedido no fornecedor.

Uma recusa só liberta a incerteza se o seu `record.request_id` corresponder ao
último pedido externo. Uma recusa antiga da mesma operação e dos mesmos bytes
não liberta uma tentativa posterior desconhecida. Provas antigas sem este GUID
permitem registar indeterminado ou confirmar aceitação por mensagem/estado
correspondente; não permitem declarar recusa para repetir. Se o fornecedor não
conservou a correlação ou o corpo necessário à conferência, preservar retenção.

Referência Microsoft: [GUID client-request-id por pedido](https://learn.microsoft.com/en-us/graph/best-practices-concept).
A aplicação conserva as regras M09: não passa a registar tokens, cabeçalhos
completos, URLs ou corpos de clientes nos logs.

## Sequência no painel

1. Abrir Administração como João. Consultar reconciliação no trabalho falhado
   ou procurar o identificador no campo próprio (inclui operações concluídas).
2. Conferir operação, tipo, estado, pedido externo e histórico. Obter a evidência
   no fornecedor e verificar a origem, sem executar um novo envio para testar.
3. Selecionar decisão, anexar o JSON para aceite/recusado e indicar motivo.
   Indeterminado não exige ficheiro e mantém bloqueio.
4. Se a operação mudou, voltar a consultar. Se existir worker ativo, aguardar o
   fim da atividade e tentar novamente. Não parar serviços operacionais por
   iniciativa deste procedimento; respeitar o responsável pela instalação.
5. Após recusa confirmada, rever o motivo técnico antes de pedir uma repetição
   separada. Esta segunda ação é que pode originar um novo envio.

«Recuperar apenas aviso Teams» exige email já aceite e intenção Teams guardada.
Não pede prova externa adicional porque a aceitação já está no recibo. Reutiliza
o ID determinístico `teams:<operação>`; chamadas repetidas não duplicam a intenção.
Uma intenção existente divergente fica retida. Um aviso Teams incerto deve ser
reconciliado pelo seu próprio identificador, sem reabrir o email.

## Persistência, autorização e limites

GET/POST `/api/admin/jobs/<id>/reconciliation` têm a mesma autorização M02,
CSRF/origem e `no-store` do painel. O POST exige a revisão consultada. Uma revisão
SHA256 dos campos da linha impede aplicar decisões sobre estado desatualizado.
O mutex do worker exclui transportes ativos e a admissão do runtime preserva
retenções de manutenção/restauro. Uma transação SQLite inclui decisão, histórico,
recibo, retirada de bytes e criação de intenção Teams. Uma falha faz rollback de
tudo. A ação não inicia worker nem repete um POST/PATCH.

A auditoria autoritativa fica em `result_json.reconciliation_history`, juntamente
com a operação: identidade estável do administrador, instante, motivo, decisão,
revisão anterior, fase anterior, registo de evidência e o seu SHA256. Não há janela
entre bases diferentes para gravar decisão e auditoria. O histórico por operação
é consultável no diálogo; não se duplica na tabela administrativa de reservas.
O browser recebe decisão/hora/motivo sanitizado e o resultado de aplicação;
evidência, envelope, referências privadas e token interno não são exportados.
O estado normal do técnico mantém a projeção M03 e apenas reflete aceitação,
conclusão ou retenção resultantes.

Sem dependência, variável de configuração ou migração SQLite nova; campos JSON
aditivos na fila 3. Instalar web e worker do mesmo artefacto identificado. Não
reutilizar um estado alterado pelo M08 com uma versão anterior que desconheça a
retenção `reconciliation_hold`; rollback exige procedimento de backup/restauro e
reconciliação dos efeitos externos. A auditoria não é inviolável para quem edita
a base diretamente; não remover histórico/provas para libertar operações.

Este mecanismo não reconstrói pastas ou provas de propriedade M21, não resolve
remoções antigas sem versão, não converte conflitos de conteúdo em autorização
de substituição e não liberta dados inválidos. Esses casos continuam visíveis e
exigem recuperação específica. Não altera SADI, permissões Microsoft ou retenção
de backups. Nenhuma operação real foi executada durante o desenvolvimento.

## Ensaios

Os testes usam filas novas, documentos fictícios e transportes simulados com
rede bloqueada antes de importar a aplicação. Cobrem decisões, insuficiência de
prova, identidade/versão/bytes divergentes, worker ativo, falha transacional,
indeterminado, restauro/migração retidos, correlação por tentativa, prazos 429,
Teams independente, cancelamento por ID, CSRF e privacidade. O JavaScript testa
consulta, decisão ligada à revisão, ficheiro obrigatório/limitado, erro de estado
desatualizado sem replay e recuperação Teams sem chamar a repetição de email.
Resultados finais e parecer independente: ESTADO/REGISTO do projeto.

Validação local de 13/10/2026: 404 casos Python distintos aprovados nos 16 módulos
pertinentes e 97 JavaScript. A bateria ampla terminou com 400 aprovados e duas
falhas de simuladores sem o novo parâmetro; depois de os corrigir e acrescentar
dois casos, a confirmação dos três módulos afetados teve 93 aprovados em 34,52 s.
Os restantes 311 resultados continuam aplicáveis; não houve mudança funcional
após a bateria ampla. Logs: `p2-m08-regression-r1.txt`,
`p2-m08-focused-final-r1.txt` e `p2-m08-js-r1-elevated.txt` na pasta de consolidação.
O primeiro comando Node foi impedido pela sandbox (EPERM); a execução autorizada
passou os 97 casos em 2,68 s. Não houve novo ensaio Linux ou navegador real nesta
etapa; a matriz integrada continua em M16.

Comandos: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -B -m pytest -p no:cacheprovider`
com os módulos `test_job_reconciliation`, `test_operational_admin`,
`test_diagnostic_privacy`, `test_graph_job_visibility`, `test_durable_mail`,
`test_durable_teams`, `test_cancellation_recovery`, `test_graph_mail_service`,
`test_teams_notification_service`, `test_graph_finalization_queue`,
`test_graph_publication_recovery`, `test_audit_mail_recovery`,
`test_fixed_mail_delivery`, `test_audit_worker_ownership`,
`test_audit_queue_corruption` e `test_runtime_admission` em `tests/`, com `-q`.
Confirmação: apenas `test_job_reconciliation.py`, `test_graph_mail_service.py` e
`test_cancellation_recovery.py`. Interface: `node --test tests/js/*.test.cjs`.
No PowerShell, definir a variável com `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'`
antes do comando Python.
