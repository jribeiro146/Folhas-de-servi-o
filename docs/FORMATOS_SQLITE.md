# Compatibilidade das bases SQLite

Esta alteração acrescenta identidade e versão às bases de edição e fila. Não é ainda a ferramenta de migração/instalação. A integração do contrato de execução foi aprovada em `667d341`; esta alteração tem testes e revisão próprios.

| Base | application_id | user_version | Estrutura suportada |
|---|---|---|---|
| editing-state/editing-state.sqlite3 | 0x46534544 | 1 | editing_states, admin_actions |
| graph-sync.sqlite3 | 0x46535155 | 3 | graph_sync_jobs e graph_mail_messages; resultados incertos Teams |

Na etapa de mensagem durável, a fila passou a 2 para impedir workers da versão 1
de reconstruírem emails após a remoção do ativo. O contrato global também declara
agora queue=3; editing permanece 1. O validador congelado do commit aa671c0 em
`tests/legacy_sqlite_format_v1.py` prova a recusa nos dois sentidos. Não há conversão
implícita nem atualização manual de PRAGMA. A migração offline continua pendente.
Ver [mensagem durável](TRANSPORTES_EXTERNOS.md) para transações, retenção e impacto.

A versão 3 mantém as tabelas da versão 2, mas altera o contrato de execução
Teams: sending_teams/teams_unknown nunca permitem replay genérico. O marcador
impede que um worker da versão 2 ignore estas fases e repita o POST. O validador
congelado de 2a13fd8, em tests/legacy_sqlite_format_v2.py, prova recusa nos dois
sentidos. O contrato global e o marcador local têm ambos de ser compatíveis.
Na migração offline futura, trabalhos Teams legados já tentados e sem comprovativo
exigem reconciliação; não basta trocar o PRAGMA nem conservar retryable=1.

`sqlite_storage_format.py` mantém o DDL canónico e compara tabelas, colunas (incluindo ocultas/geradas), tipos, nulabilidade, defaults, chaves primárias/estrangeiras e índices únicos. Também compara o SQL canónico das tabelas, normalizando apenas whitespace e maiúsculas fora de literais/citações; isto deteta CHECK, collations e outras restrições que PRAGMA table_xinfo não revela. Outras grafias/ordenações de DDL exigem preparação explícita. Recusa vistas, triggers, tabelas adicionais, identificadores/versões desconhecidos e estruturas divergentes. Índices adicionais não únicos não alteram o contrato. A validação estrutural não valida todos os valores JSON, fórmulas ou conteúdos de ficheiros, nem equivale a uma análise completa de integridade SQLite. A preparação/ensaio de migração deve tratar os payloads e o conjunto dos dados separadamente.

Só uma base realmente vazia, sem marcador nem objetos de utilizador, pode ser criada no arranque já admitido. DDL e marcadores são gravados numa transação IMMEDIATE; a falha reverte a criação. `journal_mode=WAL`, quarentena da fila e criação de assets só acontecem depois de a estrutura ser aceite. Uma base antiga não é convertida automaticamente, mesmo quando as tabelas parecem semelhantes. Não apagar ficheiros para a fazer parecer nova.

Web/worker inspecionam ambas as bases existentes antes de construir os restantes serviços, dentro da admissão do contrato de execução. Uma fila incompatível impede o arranque também quando o transporte está desativado. Cada nova ligação operacional volta a validar o formato antes de ler/escrever/claim; a guarda de finalização também verifica a base de edição que autoriza um trabalho retido. Um comprovativo numa base futura não liberta o trabalho.

`storage_format_invalid` identifica estrutura/base incompatível ou ilegível. Não contém conteúdo de documentos nem configuração privada. Contenção da inspeção inicial usa `state_contract_busy`; não é apresentada como migração necessária. Uma base corrompida não é substituída. A abertura SQLite pode recuperar o seu journal ou gerir WAL/SHM; não se promete ausência de atividade interna SQLite ao abrir um ficheiro, apenas ausência de migração ou alteração dos dados da aplicação numa recusa.

Foram removidas a alteração automática de colunas da fila e a importação automática em massa de estados JSON do construtor. JSON antigo sem base preparada é recusado antes de criar SQLite. Snapshots legados existentes não são apagados. A próxima alteração fornece preparação offline explícita, com backup, inspeção de versões conhecidas e escritores parados. O teste antigo de conversão automática passa a exigir recusa sem tocar no ficheiro; os cenários de retenção/conversão explícita serão ensaiados com essa ferramenta antes de avançar a funcionalidades.

O contrato global continua a limitar as operações à release ativa. Os marcadores não tornam seguro operar com código legado que não verifica esse contrato, nem autorizam atualizar manualmente um PRAGMA enquanto há processos ativos. Primeira transição desde `a1cac3c` exige parar todos os escritores. Nessa revisão, a base de edição ainda não tinha admin_actions e a fila ainda não tinha attempt_token; a comparação foi feita com os blobs Git, sem executar dados reais.

Testes sintéticos cobrem criação/reabertura, versão futura, identidade errada, coluna alterada, base desconhecida (incluindo nome parecido com tabela interna SQLite), corrupção, instâncias já construídas, recusa antes de criar outros stores, guarda de finalização e dois processos inicializadores. A validação Linux e a ferramenta de preparação continuam pendentes; não instalar este checkpoint.

## Resposta à primeira revisão

O P2 e o P3 foram reproduzidos em seis testes. Preflight e factory aceitam agora a mesma base verdadeiramente vazia, incluindo 0 bytes ou criação ainda não confirmada noutro processo. O preflight lê marcadores/estrutura numa única transação de leitura, evitando misturar metadata antes/depois do commit concorrente. Se houver JSON legado, uma base de edição vazia continua a ser recusada em ambos os pontos, sem criar estrutura. A transação IMMEDIATE do factory volta a verificar antes de criar.

Foi removido também o fallback individual para JSON em EditingStateService: uma linha ausente recebe um novo estado, nunca o conteúdo de um snapshot antigo. Importação/quarentena são exclusivas da futura ferramenta offline; os ficheiros não são apagados. Teste com versão 99, revisão e autosave legados confirma que o snapshot não é adotado nem alterado. Dois processos reais arrancam agora a web e o worker com os preflights sobre a mesma instalação nova.

Na revisão própria adicional, reproduziu-se um CHECK desconhecido que passava na comparação só por PRAGMA. A comparação do DDL canónico acrescentada acima fecha esse caso. Não foram operados dados reais.

## Resposta à segunda revisão

Os P2/P3 anteriores foram confirmados corrigidos. O P3 adicional de recuperação foi reproduzido: um subprocesso criou o DDL em DELETE, forçou páginas para disco e morreu com journal válido; `mode=ro` impedia o rollback e classificava a base como ilegível. O preflight usa agora `mode=rw`: continua a não criar um ficheiro ausente, não faz DDL nem escreve dados da aplicação, mas permite a recuperação normal do SQLite antes da inspeção. A transação de inspeção continua a ser BEGIN (leitura).

O teste prova que a base mudou no disco antes do crash e que há journal válido, sem o apagar pela aplicação. Após recuperar, a criação prossegue sem os dados não confirmados. Um segundo processo morto em WAL deixa também WAL/SHM; o valor confirmado permanece, o valor não confirmado desaparece. Há ainda regressões de COLLATE/comentário desconhecidos no DDL e arranque web/worker sobre base marcada com snapshots JSON retidos, intactos e não importados. Linux permanece por executar.
