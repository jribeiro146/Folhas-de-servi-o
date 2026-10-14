# Histórico, custo em repouso e simulação de retenção (M12)

## Âmbito

O M12 mede o crescimento e o custo da fila antes de otimizar. O utilizador escolheu comparar **90, 180 e 365 dias e decidir depois**. Não foi escolhido um prazo de eliminação. Não há limpeza automática, opção `--apply`, eliminação de registos nem migração de formato nesta alteração.

Os IDs de `graph_sync_jobs` são usados por `enqueue` para reconhecer operações já conhecidas. Os resultados guardam recibos, referências de substituição, relações pai/filho Teams e dados necessários à reconciliação. Apagar uma linha antiga pode permitir repetir uma operação. A idade isolada não prova que essa linha deixou de ser necessária.

## Medição reproduzível

Executar `python -B tools/measure_history.py` num ambiente de QA. O lançador não aceita uma base existente: cria uma pasta temporária nova fora do repositório, configura o perfil sintético e bloqueia a rede antes de importar a fila. Usa 10 mil e 100 mil trabalhos, primeiro só uploads concluídos e depois uma mistura de 50% uploads, 25% emails aceites e 25% filhos Teams aceites. Os snapshots de email da mistura já têm o BLOB retirado; conservam os metadados de identificação.

Cada função tem aquecimento e três amostras: `summary`, ciclo `process_once` em repouso e dez consultas `status`. O relatório guarda tempos individuais/medianas de parede e CPU, volume SQLite, versões, identidade de fonte e pasta fictícia. A validação exige contagens antes/depois iguais e nenhum filho Teams em falta ou em conflito. A simulação de idade inclui também auditoria sintética numa base separada.

São medições locais Windows, com páginas de base aquecidas e sem concorrência, transporte, configuração de serviços, health/logging do laço exterior ou contrato operacional ligado à fila. Não representam a latência do servidor AlmaLinux, de chamadas HTTP ou de uma fila com trabalho ativo. A resolução do relógio de CPU pode produzir zero ou saltos de 15,625 ms nas operações curtas. A mediana é informativa, não um limiar rígido de aprovação.

## Correção proporcional

As duas rotinas de reconciliação de uploads percorriam também o histórico completo quando já não havia uploads pendentes, falhados ou suspensos. A medição demonstrou custo elevado em repouso. Antes de carregar e resolver os caminhos desse histórico, cada rotina verifica agora se existe um upload num dos estados que pode alterar. Quando existe, mantém o algoritmo anterior e toda a evidência histórica.

A verificação ocorre na mesma transação `BEGIN IMMEDIATE` dos chamadores, por isso não há intervalo para outro escritor introduzir um candidato entre a consulta e a decisão. Não se removem linhas, não se dispensam recibos/guardas, não se muda a prioridade nem se adicionam índices ou formatos. Uma fila com candidatos ainda percorre o histórico; o resumo e as consultas de estado continuam a ter custos dependentes do número de linhas. Não se introduz cache de resultados que possa esconder trabalho recente.

## Simulação só de leitura

`python -B tools/history_retention.py --queue CAMINHO_COPIA/graph-sync.sqlite3 --editing CAMINHO_COPIA/editing-state.sqlite3 --days 90 180 365` produz apenas um relatório agregado. Usar uma cópia coerente e privada; a ferramenta abre SQLite com `mode=ro`, `query_only=ON` e transação de leitura, valida o formato e nunca instancia o worker. O caminho inexistente é recusado sem criar base. A base de edição é opcional e é lida numa transação própria; não há promessa de snapshot atómico entre bases vivas. Não se executou esta ferramenta sobre dados operacionais.

| Dados | Tratamento nos três prazos |
|---|---|
| IDs de todos os trabalhos | Preservados: necessários à idempotência |
| Pending/running/held/failed e estados desconhecidos | Preservados; idade não desbloqueia nem permite repetição |
| Recibos, mail/Teams, cancelamentos, relações, flags de incerteza ou formatos desconhecidos | Preservados integralmente |
| Upload simples concluído e com formato conhecido | Contado por idade para estudar arquivo futuro; o ID e a linha continuam protegidos |
| `graph_mail_messages`, incluindo órfãos e incertos | Metadados e BLOB ainda existente protegidos |
| `admin_actions` | Contagem por idade; auditoria protegida |

`deletion_candidates` é zero por desenho. `plain_uploads_older_than` é um conjunto de estudo, **não uma lista utilizável num DELETE**. O volume JSON contado é uma estimativa de conteúdo, não espaço recuperável em disco; não inclui índice, páginas, WAL ou VACUUM. A seleção exige idade estritamente superior ao prazo a partir de `updated_at`, sem incluir a fronteira exata, datas inválidas ou futuras.

A retirada dos bytes volumosos do email já existe: um recibo aceite e `payload=NULL, retired_at=...` são confirmados na mesma transação. O hash, tamanho e identidade permanecem. Uma operação incerta conserva os bytes e exige reconciliação. O M12 não altera essa regra nem elimina registos antigos pelo facto de o BLOB já estar retirado.

Qualquer política futura de eliminação exige a escolha do prazo pelo utilizador e uma solução explícita para conservar idempotência, recibos, dependências e auditoria (incluindo leitura de históricos arquivados e compatibilidade de restauro). A simulação atual pode ser concluída sem essa escolha; não concede autorização de eliminação.

## Evidência

Os dados e relatórios completos permanecem em `output/history/fst-*` e no registo local de QA, ignorados pelo Git. Não publicar bases, conteúdo de mensagens ou auditoria real.

### Resultado de 14/10/2026

Medições em Windows/Python 3.13.1, sem outros testes concorrentes nas duas execuções usadas na comparação. Valores medianos de três amostras após aquecimento:

| Perfil | Trabalhos | Base SQLite (MiB) | Ciclo antes (ms) | Ciclo depois (ms) | Resumo depois (ms) | Dez consultas depois (ms) |
|---|---:|---:|---:|---:|---:|---:|
| uploads | 10000 | 2.17 | 9119.98 | 105.00 | 14.88 | 212.73 |
| uploads | 100000 | 21.77 | 119393.08 | 805.23 | 148.88 | 1099.61 |
| mixed | 10000 | 2.68 | 5013.96 | 139.74 | 56.61 | 180.79 |
| mixed | 100000 | 26.81 | 48263.08 | 1963.59 | 803.78 | 1076.14 |

A base anterior foi o código operacional de `a1cd5d34` mais o lançador de medição: `output/history/fst-cef2043e/result.json`, source `8a3d32c81dde3495aefad239da78e936acfeb36d2dda76f9931946212362316e`. O resultado posterior final é `output/history/fst-5a358681/result.json`, source `9055df1d0f27557e7003dbc6ff689079eabb93f1c95c2657b2619c5a4418154f`, 122 ficheiros. Logs `qa-output/consolidacao-20261007/p2-m12-baseline.txt` e `p2-m12-after-final.txt`. O primeiro ensaio posterior, `fst-c4c3feec`/`p2-m12-after.txt`, coincidiu com regressões Python e foi preservado, mas não é usado nesta tabela.

A melhoria é no ciclo sem uploads a reconciliar. O custo de resumo de pais/filhos e das consultas de estado continua visível a 100 mil trabalhos; não se afirma custo constante nem latência equivalente no servidor. O volume da base é medido após checkpoint WAL e não inclui os ficheiros auxiliares ou a base de auditoria. Não se justifica eliminar recibos para obter esta melhoria.

| Histórico estudado | Mais de 90 dias | Mais de 180 dias | Mais de 365 dias |
|---|---:|---:|---:|
| Uploads simples, uploads, 10000 trabalhos | 8740 | 7480 | 4890 |
| Uploads simples, uploads, 100000 trabalhos | 87670 | 75340 | 49995 |
| Uploads simples, mixed, 10000 trabalhos | 4370 | 3740 | 2445 |
| Uploads simples, mixed, 100000 trabalhos | 43834 | 37670 | 24997 |

Todos os IDs permaneceram protegidos. No cenário misto de 100 mil trabalhos conservaram-se 25 mil linhas de snapshots de email já retirados e 10 mil registos de auditoria; os números desta tabela não são candidatos à eliminação. A matriz de regressão separada inclui BLOB incerto, snapshot órfão, flags de migração/restauro/reconciliação, recibos, estados por resolver, datas inválidas/futuras e fronteira exata do prazo.

Regressões: **242 Python aprovados, zero falhas**, 97,66 s, log `p2-m12-python-r1.txt`: history_retention, graph_publication_recovery, graph_finalization_queue, durable_mail, durable_teams, teams_uncertainty, job_reconciliation, cancellation_recovery e archive_preparation. Inclui os sete testes novos de leitura/seleção e os cenários existentes de supersessão, upload antigo versus novo, falha parcial, guardas, recibos e repetição. A medição final exercitou também o novo estado RUNNING/PASS do relatório. Não houve alteração JavaScript nesta etapa; o CI global final fica no M20.
