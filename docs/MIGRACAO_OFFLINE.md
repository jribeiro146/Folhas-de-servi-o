# Migração explícita dos formatos — M06

`tools/migrate_runtime.py` prepara e aplica uma conversão offline no mesmo disco,
mantendo os caminhos atuais. Não importa configuração privada nem a aplicação,
não liga a Graph/email/Teams e não muda os ficheiros das folhas. O instalador
executa-a numa janela em que web, worker, tarefas agendadas e versões anteriores
estão parados. A flag `--writers-stopped` é uma declaração dessa paragem, não um
comando para parar processos. Versões antigas sem contrato não respeitam o novo
bloqueio e têm obrigatoriamente de ser encerradas pelo instalador.

## Âmbito e impacto

| Entrada comprovada | Tratamento |
|---|---|
| JSON de edição v1 com nome `sha256(document_id).json` | Conversão para agregado v2; preserva identidade, revisão, autosave e operações; reserva antiga fornece a propriedade e a sessão, sem renovar prazos |
| SQLite de edição sem marcadores, esquema exato de `a1cac3c` | Conversão para edição1, com tabela de auditoria |
| SQLite edição1 atual | Preserva registos/auditoria; a linha SQLite tem precedência sobre um snapshot JSON antigo |
| Fila sem marcadores, esquema exato `a1cac3c`; queue1 e queue2 | Converte para queue3; mantém payloads, tentativas, recibos, snapshots e MIME. Todos os trabalhos por concluir ficam em quarentena sem repetição automática/manual |
| Queue3 atual | Preserva estados, resultados e mensagens, sem reiniciar trabalhos |
| Agregado de edição inválido/futuro com identidade conhecida | Conserva o JSON original na linha, recusado pela quarentena M04 |
| Esquema SQLite/contrato desconhecido ou snapshot sem identidade comprovada | Recusa publicar o conjunto; guarda o original para diagnóstico |

A retenção de trabalhos legados é deliberada: formatos antigos não fornecem
prova suficiente de todos os efeitos externos. Não converter `failed` em
`pending`, não apagar `invalid_state` e não recriar envios sem reconciliação.
O resultado contém `migration_hold`, estado anterior e recibo original. A ação
administrativa de fecho auditado da reconciliação pertence ao M08, ainda separado.
Trabalhos já concluídos mantêm o resultado, sem criar novos envios.

Os formatos sem `retryable`/`max_attempts` ou com DDL diferente não são inferidos.
As referências conhecidas estão no Git (`a1cac3c`, queue1 `aa671c0`, queue2
`2a13fd8`) e nas fixtures sintéticas históricas. Isto não certifica que todos os
servidores tenham um desses formatos: a preparação verifica o conjunto fornecido.

## Inventário e preparação

1. Identificar `FS_APP_DATA_DIR`, `FS_EXCEL_ROOT` e `GRAPH_CACHE_DIR` efetivamente
   usados. Incluir raízes externas com `--include-root`; subpastas de data já são
   incluídas. Contemplar rascunhos/arquivo/canceladas, `graph-sync-assets`,
   fotografias, assinaturas, recibos `.graph.json`, edição, SADI e PDFs privados.
2. Preservar a configuração e a chave `FS_SECRET_KEY` por canal privado separado.
   As assinaturas SADI dependem da mesma chave. O migrador preserva os bytes e
   identificadores; não troca a chave, não revalida ou volta a assinar checklists.
3. Reservar um workspace privado fora das raízes de dados e do artefacto, com
   espaço para o conjunto original e as novas bases. Restringir as permissões
   (incluindo ACL no Windows). O snapshot contém dados privados: nunca o colocar
   no Git, no pacote de revisão, em logs públicos ou numa pasta servida pelo web.
4. Usar o artefacto com manifesto válido. Manter os escritores parados durante
   preparação e aplicação. Os caminhos abaixo são exemplos, a substituir pelos
   caminhos da instalação; não acrescentar raízes que não pertençam à aplicação.

Executar como o utilizador da aplicação, ou como um instalador com privilégios
para conservar os donos existentes. No POSIX, cada base/contrato conserva UID,
GID e modo; ficheiros novos usam o dono/grupo da pasta de dados e modo 0600,
e diretórios novos são privados (0700). Falhar a reposição destes atributos
interrompe a publicação antes de substituir esse ficheiro. No Windows, as ACL
são herdadas da pasta: preparar as ACL privadas no destino e no workspace.
O Dockerfile atual declara UID/GID10001 por defeito; a ferramenta não pressupõe
esse número e conserva os atributos encontrados. O UID real da instalação e
o ensaio Linux serão confirmados com os detalhes Docker ainda aguardados.

```sh
python -B tools/migrate_runtime.py prepare \
  --data-dir /srv/folhas/data \
  --include-root /srv/folhas/Excel \
  --include-root /srv/folhas/cache \
  --workspace /srv/folhas/migracoes/m06 \
  --writers-stopped --roots-complete
```

Esta fase simula a conversão numa cópia, sem publicar dados novos. O resultado
mostra `state=prepared`, contagens de edição/quarentena e ficheiros preservados.
`snapshots_shadowed` conta JSON antigos preservados cujo estado SQLite prevalece.
`plan.json` identifica as raízes, hashes, estado e tentativa. A subpasta da
tentativa contém `original/0` para data, `original/1`, etc. para raízes externas,
e `prepared` com as bases novas. Bases SQLite são capturadas pela API de backup,
incluindo páginas WAL confirmadas; não se usa uma cópia isolada do `.sqlite3`.
O inventário compara os restantes ficheiros antes e depois da cópia.

Ligações simbólicas/junções, raízes sobrepostas ambíguas, armazenamento remoto e
dados dentro do artefacto são recusados. `--roots-complete` confirma o inventário
manual; a ferramenta não pode descobrir pastas omitidas ou configuração externa.

## Aplicação e recuperação de interrupções

```sh
python -B tools/migrate_runtime.py apply \
  --workspace /srv/folhas/migracoes/m06 --writers-stopped
```

A publicação confere originais, candidatos, bases atuais e ficheiros. Se algo
mudou, recusa a aplicação. Há exclusão entre workspaces de migração, reserva
SQLite e exclusão do contrato moderno. Antes da primeira substituição, revoga a
admissão antiga; só publica o contrato da nova versão depois de ambas as bases.
Um processo interrompido deixa a versão moderna recusada até retomar o plano.
Não se substitui uma base com WAL/journals ainda abertos.

Se a preparação for interrompida, repetir `prepare` com os mesmos argumentos:
é criada uma tentativa nova e os ficheiros da tentativa incompleta ficam
preservados. Se já existir um plano, é devolvido esse plano, sem o recalcular.
Se a aplicação for interrompida, repetir `apply` com o mesmo workspace e a mesma
versão: reconhece as bases já publicadas, prossegue nas restantes e reconhece a
publicação final mesmo se a última resposta se perder. Uma aplicação concluída
devolve `state=complete` sem voltar a converter. Esse estado é um recibo da
migração, não uma verificação contínua dos dados após a aplicação começar a operar.

Manter o workspace até à aceitação da instalação. A ferramenta não apaga o
original nem faz rollback automático. Não restaurar seletivamente uma base ou
recibo depois de voltarem a existir operações reais. O restauro coerente e a
reconciliação de efeitos posteriores pertencem ao M07.

O M06 não transfere armazenamento para outro caminho/servidor: isso mudaria
identidades locais e referências absolutas na fila. Não renomear diretórios ou
editar caminhos no plano para o tentar. Os ficheiros de negócio ficam nos mesmos
locais; a cópia privada serve de evidência/recuperação. Os testes cobrem falhas de
processo e erros de ficheiro; falhas físicas de disco exigem a política de backup.

## Código, testes e aceitação

`runtime_migration.py` gere cópia, reconhecimento dos formatos, conversão e
publicação; usa `RuntimeContract`, o validador SQLite e o validador M04 sem importar
a aplicação. `migrate_runtime.py` exige manifesto, paragem e inventário explícitos.
A aplicação só vê bases preparadas e um contrato final compatível.

```sh
python -B -m pytest -q tests/test_runtime_migration.py tests/test_runtime_contract.py tests/test_prepare_runtime.py tests/test_editing_quarantine.py
```

Os ensaios são sintéticos, temporários e sem rede. Cobrem identidade/reserva,
anexos/recibos/SADI, filas0/1/2, MIME e resultados, WAL confirmado, schemas
desconhecidos, quarentena, alteração entre fases, concorrência, interrupções de
cada publicação e repetição idempotente. O instalador deverá executar primeiro
sobre uma cópia isolada do seu conjunto e conferir as contagens e quarentenas.
Nenhum ensaio neste projeto autoriza a migração de produção.

### Proveniência dos esquemas sem marcadores

O commit `a1cac3c0d3da8c7c712fce816cbc2b746e01ba21` criava as duas tabelas
seguintes, em `editing_state_repository.py` e `graph_sync_queue.py`, com
`CREATE TABLE IF NOT EXISTS`. A criação SQLite guarda a declaração sem essa
cláusula; normalizar espaços/maiúsculas não dispensa comparar tipos, valores por
defeito, chaves e DDL. O adaptador exige exatamente estas formas:

```sql
CREATE TABLE editing_states (
    document_id TEXT PRIMARY KEY, state_json TEXT NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE graph_sync_jobs (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload_json TEXT NOT NULL,
    status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at REAL NOT NULL, retryable INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER, last_error TEXT, result_json TEXT,
    created_at REAL NOT NULL, updated_at REAL NOT NULL
);
```

Nessa versão, a normalização de edição atribuía a propriedade a partir do titular
da reserva antiga e passava a reserva para as sessões. O migrador só o faz quando
esses campos de prova existem e o agregado convertido passa o validador atual;
conserva o prazo, usando `acquired_at` como último instante historicamente conhecido
quando falta `last_seen_at`. Não usa a hora da migração para prolongar uma sessão.
As formas JSON v1/v2 independentes estão em `tests/legacy_editing_shapes.py`;
os validadores SQLite congelados de queue1/queue2 estão em
`tests/legacy_sqlite_format_v1.py` e `tests/legacy_sqlite_format_v2.py`.
O importador e o normalizador efetivos de a1cac3c foram ainda congelados em
`tests/legacy_editing_repository_a1.py` e `tests/legacy_editing_sessions_a1.py`:
confirmam que uma reserva sem `last_seen_at` podia ficar persistida dentro de
`sessions`. A migração converte também essa forma, preservando o prazo original.

Após a primeira revisão independente, os recibos mantêm os campos de incerteza
no resultado atual, além da cópia textual original. O indicador de reconciliação
continua disponível ao monitor de email e ao painel; quarentena não significa
que o email não tenha sido aceite. Há teste real POSIX de permissões que é
ignorado em Windows, além de testes simulados de UID/GID e falha de chown.
Os testes POSIX e a migração foram posteriormente executados nos dois perfis Docker identificados em [VALIDACAO_CI.md](VALIDACAO_CI.md). Isso não substitui a preparação do destino pelo instalador.

Resultados JSON legados com tipos inválidos (fase, aceitação, recibo, prazo Teams
ou intenção de cancelamento) ficam apenas no campo textual original; não se
expõem esses valores malformados no estado atual. O marcador explícito
`migration_uncertain` conserva o pedido de reconciliação na API/admin/monitor,
sem inventar prova de envio ou fazer a consulta do trabalho falhar com erro500.
