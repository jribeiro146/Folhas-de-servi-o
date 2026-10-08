# Compatibilidade das bases SQLite

Esta alteração acrescenta identidade e versão às bases de edição e fila. Não é ainda a ferramenta de migração/instalação. A integração do contrato de execução foi aprovada em `667d341`; esta alteração tem testes e revisão próprios.

| Base | application_id | user_version | Estrutura suportada |
|---|---|---|---|
| editing-state/editing-state.sqlite3 | 0x46534544 | 1 | editing_states, admin_actions |
| graph-sync.sqlite3 | 0x46535155 | 1 | graph_sync_jobs, incluindo attempt_token |

`sqlite_storage_format.py` mantém o DDL canónico e compara tabelas, colunas (incluindo ocultas/geradas), tipos, nulabilidade, defaults, chaves primárias/estrangeiras e índices únicos. Recusa vistas, triggers, tabelas adicionais, identificadores/versões desconhecidos e estruturas divergentes. Índices adicionais não únicos não alteram o contrato. A validação estrutural não valida todos os valores JSON, fórmulas ou conteúdos de ficheiros, nem equivale a uma análise completa de integridade SQLite. A preparação/ensaio de migração deve tratar os payloads e o conjunto dos dados separadamente.

Só uma base realmente vazia, sem marcador nem objetos de utilizador, pode ser criada no arranque já admitido. DDL e marcadores são gravados numa transação IMMEDIATE; a falha reverte a criação. `journal_mode=WAL`, quarentena da fila e criação de assets só acontecem depois de a estrutura ser aceite. Uma base antiga não é convertida automaticamente, mesmo quando as tabelas parecem semelhantes. Não apagar ficheiros para a fazer parecer nova.

Web/worker inspecionam ambas as bases existentes antes de construir os restantes serviços, dentro da admissão do contrato de execução. Uma fila incompatível impede o arranque também quando o transporte está desativado. Cada nova ligação operacional volta a validar o formato antes de ler/escrever/claim; a guarda de finalização também verifica a base de edição que autoriza um trabalho retido. Um comprovativo numa base futura não liberta o trabalho.

`storage_format_invalid` identifica estrutura/base incompatível ou ilegível. Não contém conteúdo de documentos nem configuração privada. Contenção da inspeção inicial usa `state_contract_busy`; não é apresentada como migração necessária. Uma base corrompida não é substituída. A abertura SQLite pode recuperar o seu journal ou gerir WAL/SHM; não se promete ausência de atividade interna SQLite ao abrir um ficheiro, apenas ausência de migração ou alteração dos dados da aplicação numa recusa.

Foram removidas a alteração automática de colunas da fila e a importação automática em massa de estados JSON do construtor. JSON antigo sem base preparada é recusado antes de criar SQLite. Snapshots legados existentes não são apagados. A próxima alteração fornece preparação offline explícita, com backup, inspeção de versões conhecidas e escritores parados. O teste antigo de conversão automática passa a exigir recusa sem tocar no ficheiro; os cenários de retenção/conversão explícita serão ensaiados com essa ferramenta antes de avançar a funcionalidades.

O contrato global continua a limitar as operações à release ativa. Os marcadores não tornam seguro operar com código legado que não verifica esse contrato, nem autorizam atualizar manualmente um PRAGMA enquanto há processos ativos. Primeira transição desde `a1cac3c` exige parar todos os escritores. Nessa revisão, a base de edição ainda não tinha admin_actions e a fila ainda não tinha attempt_token; a comparação foi feita com os blobs Git, sem executar dados reais.

Testes sintéticos cobrem criação/reabertura, versão futura, identidade errada, coluna alterada, base desconhecida (incluindo nome parecido com tabela interna SQLite), corrupção, instâncias já construídas, recusa antes de criar outros stores, guarda de finalização e dois processos inicializadores. A validação Linux e a ferramenta de preparação continuam pendentes; não instalar este checkpoint.
