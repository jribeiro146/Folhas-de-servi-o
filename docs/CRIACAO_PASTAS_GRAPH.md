# M21 — Criação concorrente de pastas Graph

## Comportamento

As raízes partilhadas (Activas, Arquivo e Canceladas, incluindo os seus
antepassados configurados) são criadas com `conflictBehavior=fail`. Uma leitura
404 seguida de POST 409 permite apenas voltar a ler a pasta, sem substituir nem
apagar o item ou os seus filhos. Exige ID textual, nome correspondente e `folder` válido;
recusa ficheiros, atalhos `remoteItem`, itens eliminados e metadados de destino
contraditórios. Um POST confirmado também é relido e o ID tem de coincidir.
Uma leitura ainda 404 deixa a operação por confirmar; outros erros propagam-se.
Nas raízes partilhadas e na subpasta de fotografias, os nomes são comparados com
normalização Unicode NFC e `casefold`, preservando compatibilidade com caminhos
Graph que não distinguem maiúsculas de minúsculas. IDs, drive, eTags, nomes
aleatórios de staging e nomes de bundles provados continuam a ser exatos.
Esta regra não depende da capitalização efetivamente configurada em produção.

Cada segmento seguinte é lido/criado pelo ID do pai já confirmado. O helper
`ensure_folder_path` destina-se às raízes partilhadas e devolve a pasta final.
Não deve ser usado para criar/reconstituir uma pasta pertencente a uma operação.

Os bundles ativos e de arquivo continuam a exigir prova local do ID. A criação
passa pelo nome aleatório durável `.fs-upload-…`, pasta vazia e mudança de nome
condicional por eTag. O POST dessa pasta usa agora o ID da raiz confirmada.
Um conflito explícito no primeiro POST fica marcado em `creation_conflict` na
prova local e é terminal: nem uma nova gravação pode adotar esse item.
Uma pasta aleatória já existente sem ID/prova de tentativa é igualmente recusada.

Antes do POST grava-se `creation_attempted`. Se a resposta se perder, a mesma
tentativa pode recuperar o item de nome aleatório, confirmar tipo/ID/vazio e
continuar a mudança de nome condicionada. A prova de ID continua a recuperar a
perda da resposta do PATCH. O pressuposto existente é a exclusividade do nome
aleatório criptográfico, não uma garantia contra adulteração dos metadados locais.
Um crash antes de persistir a resposta mantém a ambiguidade inerente desse
protocolo. Se a flag de tentativa já existia **antes** do POST atual, um 409 pode
ser consequência da criação anterior com resposta perdida e leitura ainda 404.
Neste caso volta-se a ler, validam-se nome exato/tipo/ID/vazio e recupera-se a
mesma operação. Se continuar invisível, conserva-se erro repetível, sujeito aos
limites normais da fila. Não se grava conflito definitivo nesse intervalo. Um
409 na primeira tentativa continua terminal; não se infere prova do próprio erro.

As fotografias são lidas/criadas dentro do ID do bundle provado. A subpasta pode
ser partilhada por tentativas da mesma operação, após a mesma validação de tipo
e conflito. Todos os uploads de fotografias usam o ID dessa subpasta; nunca
reconstituem os antepassados pelo nome nem escrevem numa operação homónima.
Um pai removido provoca erro. Os ficheiros alheios são preservados e não há
pruning implícito. Remoções explícitas, eTags e publicação de arquivo mantêm os
seus contratos anteriores.

## Dependências e operação

Sem biblioteca, variável de ambiente, permissão Graph ou migração SQLite nova.
As provas JSON `.fs-upload-owner.json`/`.fs-archive-owner.json` ganham duas flags.
A fila já transporta a prova ativa entre staging e rascunho original, incluindo
falhas; um teste atravessa duas gravações para confirmar a retenção do conflito.
Provas antigas com ID continuam utilizáveis. Uma prova antiga apenas com nome
temporário e item remoto existente, sem a nova flag de tentativa, fica retida
para revisão, em vez de assumir que a criação pertence à aplicação.

Em conflito, preservar pasta remota, rascunho e prova local. Confirmar a
identidade/tipo e a origem do item com o administrador antes de preparar uma
correção específica. Não apagar a prova ou marcar a pasta como própria para
forçar repetição. Esta alteração não inclui ferramenta de desbloqueio nem
autoriza operações reais. A reconciliação funcional por trabalho pertence a M08.

## Verificação e limites

`tests/test_graph_folder_creation.py` cobre raízes existentes e 404→409, resposta
de sucesso com ID substituído, ficheiros/atalhos/destinos divergentes, perda de
resposta, criação por ID do pai, conflitos de staging persistentes entre saves,
legado sem prova, fotografias de ativo/arquivo e pai removido. Os testes existentes
de publicação verificam perda de POST/PATCH, eTags, supersessão e preservação do
arquivo. As fixtures antigas passaram a representar os campos reais de pasta.

Primeiro grupo focado: 152 aprovados em 30,22 s. Regressão final, já incluindo os
dois últimos casos de fila/tipo: **301 aprovados em 88,85 s**, quinze módulos,
log `qa-output/consolidacao-20261007/p2-m21-regression-r1.txt` na raiz do projeto.
Comando: `python -B -m pytest -p no:cacheprovider -q` sobre os módulos
`test_graph_folder_creation`, `test_graph_publication_recovery`,
`test_audit_archive_ownership`, `test_audit_removals`, `test_photo_attachments`,
`test_cancellation_recovery`, `test_graph_finalization_queue`,
`test_graph_mail_service`, `test_graph_refresh_coordinator`, `test_sync_v2`,
`test_archive_and_web`, `test_audit_atomic_files`, `test_audit_worker_ownership`,
`test_audit_queue_corruption`, `test_durable_mail`, com
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`. Sem alteração JavaScript; não se repetiu
a bateria Node. O parecer independente é registado no ESTADO/REGISTO.
Em 13/10 a primeira análise Claude (`20261013T081350Z_332240d3_450990`) identificou
os dois casos acima: capitalização e POST perdido/leitura404/409. Reprodução:
11 falhas esperadas e 40 aprovados,8,16s (`p2-m21-reproduce-r2.txt`). Após correção,
**314 aprovados em141,28s**, nos mesmos quinze módulos, log
`p2-m21-regression-r2.txt`. Inclui variantes ativo/arquivo, visibilidade tardia,
cancelamento com raiz de capitalização diferente e nomes de operação exatos.
Sem recolher dados/configuração de produção: corrigir o contrato de nomes torna
desnecessário saber qual das capitalizações existe no destino real.
Todos os ensaios são sintéticos, isolados antes de
importar a aplicação, com rede bloqueada, email/Teams desativados e temporários
fora do OneDrive. Não houve Graph real, publicação ou ensaio remoto desta versão.

Não foi demonstrada perda de filhos em produção pelo comportamento anterior
`replace`; corrigiu-se uma política de conflito inadequada e falta de validação.
Esta alteração não torna todas as operações Graph atómicas: por exemplo, a
publicação dos ficheiros principais ativos conserva o contrato anterior de
caminho/eTag. O âmbito de M21 é criação de pastas e respetivas identidades.

Referências de contrato Microsoft (consultadas em 12–13/10/2026):

- [Criar pasta e endpoint pelo ID do pai](https://learn.microsoft.com/en-us/graph/api/driveitem-post-children?view=graph-rest-1.0).
- [DriveItem: facetas, identidade e conflictBehavior](https://learn.microsoft.com/en-us/graph/api/resources/driveitem?view=graph-rest-1.0).
- [Caminhos sem distinção de maiúsculas/minúsculas; IDs sensíveis à capitalização](https://learn.microsoft.com/en-us/onedrive/developer/rest-api/concepts/case-sensitivity?view=odsp-graph-online).
