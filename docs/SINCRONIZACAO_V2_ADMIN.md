# Sincronização v2 — checklist de administração

Esta versão separa três responsabilidades: o formulário guarda imediatamente uma cópia no dispositivo, o servidor persiste o estado transacional em SQLite e o Microsoft Graph publica as alterações em segundo plano através de uma fila durável.

## Feasibility e limites suportados

- Suportado: vários técnicos e browsers. Vários processos no mesmo servidor podem partilhar a fila, desde que todos usem o mesmo `FS_APP_DATA_DIR` num disco local persistente.
- Em Plesk/Passenger: definir `FS_GRAPH_QUEUE_IN_WEB=false` e executar `python -m src.queue_worker` numa tarefa agendada independente. Isto evita depender do ciclo de vida dos workers WSGI.
- Suportado: indisponibilidade temporária do SharePoint. Os trabalhos ficam em `graph-sync.sqlite3` e são repetidos com backoff.
- Não suportado nesta versão: várias réplicas da aplicação em hosts diferentes sobre NFS/SMB. Para esse cenário, substituir SQLite por PostgreSQL antes de escalar horizontalmente.
- O SharePoint deixa de estar no caminho crítico de abrir, editar, guardar, finalizar ou cancelar. Essas ações concluem primeiro no servidor local e apresentam a publicação como pendente.

## Tarefas obrigatórias antes do primeiro arranque

1. Parar todas as instâncias e workers da versão anterior.
2. Fazer uma cópia de segurança integral do diretório atual indicado por `FS_APP_DATA_DIR`.
3. Confirmar que `FS_APP_DATA_DIR` é absoluto, persistente e gravável pelo utilizador da aplicação.
4. Confirmar que esse diretório está num disco local do servidor. Não usar OneDrive, SharePoint sincronizado, NFS, SMB ou uma pasta temporária.
5. Configurar as variáveis recomendadas:

```text
FS_EDIT_SESSION_SECONDS=2592000
FS_STATE_DB_BUSY_MS=10000
FS_GRAPH_REFRESH_SECONDS=30
FS_GRAPH_JOB_STALE_SECONDS=900
```

6. Manter `GRAPH_CACHE_DIR` no mesmo volume local persistente ou noutro volume local gravável.
7. Confirmar no Microsoft Entra que as permissões Graph já documentadas em `docs/GRAPH_SETUP.md` continuam com consentimento de administrador.
8. Iniciar a aplicação e abrir `/api/graph/status`. Confirmar `graph_enabled: true`, `refresh.last_error: null` e ausência de trabalhos `failed` no `outbox`.

## Migração automática

No primeiro arranque, os estados JSON da versão anterior são importados para `editing-state/editing-state.sqlite3` com `INSERT OR IGNORE`. Os JSON não são apagados automaticamente e servem de cópia de segurança durante a validação.

A fila acrescenta automaticamente a coluna `attempt_token` para identificar cada
tentativa. Todos os workers e instâncias web devem usar a mesma versão; parar os
processos anteriores antes da atualização. O bloqueio `graph-sync.sqlite3.worker.lock`
é libertado pelo sistema operativo ao terminar o processo, incluindo numa queda.
Não apagar esse ficheiro enquanto a aplicação estiver em utilização: diferentes
processos têm de bloquear o mesmo ficheiro. Os antigos bloqueios de edição são
distintos deste bloqueio da fila.

O worker mantém o bloqueio entre a reclamação do trabalho e o fim dos seus efeitos.
A atividade é atualizada, no máximo, a cada 30 segundos. Checkpoints, sucesso, falha e
atividade só podem alterar a tentativa que ainda possui o trabalho.

## Operação e monitorização

- `GET /api/graph/status`: estado do refresh da cache e contadores da fila (`pending`, `running`, `failed`, `complete`).
- O worker externo imprime os mesmos contadores antes e depois de cada execução: `python -m src.queue_worker`.
- `GET /api/graph/jobs/<job_id>`: detalhe de uma publicação devolvida por guardar, finalizar ou cancelar.
- Um trabalho `failed` só é repetido automaticamente quando `will_retry=true`. O campo `last_error` indica a causa mais recente. Envios sem resultado confirmado e registos com `result.invalid_state=true` ficam parados para revisão; a API não permite repetir estes casos sem resolver a causa.
- Os snapshots em `graph-sync-assets` pertencem à fila. Não os apagar enquanto existirem trabalhos pendentes ou falhados.
- Incluir no backup: `editing-state.sqlite3`, `graph-sync.sqlite3`, respetivos ficheiros `-wal`/`-shm` e `graph-sync-assets`. Para uma cópia simples e consistente, parar primeiro a aplicação.

As remoções de assinaturas e fotografias são registadas em `.fs-removals.json` e
executadas com o ID e a versão conhecidos. Uma fotografia ausente de um snapshot
antigo não autoriza a sua eliminação remota. Manter estes registos e os metadados
`*.graph.json` nos backups; não os enviar manualmente para o SharePoint.

O arquivo remoto usa `.fs-archive-owner.json` como prova local da pasta criada por
essa operação. A primeira publicação cria ficheiros com conflito `fail`; as retomas
usam versões conhecidas ou comparam o conteúdo sem sobrescrever. Arquivos existentes
sem prova local, conteúdos diferentes ou versões alteradas ficam preservados e
bloqueiam a remoção da folha ativa e o envio. Não fabricar provas de propriedade
nem trocar eTags para contornar conflitos; rever primeiro a origem e o destino.

## Validação pós-deploy

1. Abrir a mesma folha original com dois técnicos e confirmar que cada um recebe uma área privada diferente.
2. Em cada área, escrever textos diferentes, atualizar a página uma vez e confirmar recuperação imediata.
3. Guardar os dois rascunhos e confirmar nomes/pastas diferentes.
4. Abrir o mesmo rascunho em duas abas do mesmo browser e confirmar que apenas uma fica editável.
5. Desligar temporariamente o acesso ao Graph, guardar um rascunho e confirmar que a UI indica publicação pendente.
6. Restabelecer o Graph e confirmar em `/api/graph/status` que o trabalho passa a `complete`.
7. Finalizar um rascunho depois de a sua última publicação ser confirmada. Enquanto existir uma publicação pendente ou falhada desse rascunho, a finalização deve aguardar a sua resolução.

No primeiro acesso após o deploy, recomenda-se uma atualização forçada do browser para substituir o service worker e os assets da versão anterior.
