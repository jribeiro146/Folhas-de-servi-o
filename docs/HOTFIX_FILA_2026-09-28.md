# Correção da finalização em produção — 28/09/2026

## Problema corrigido

Na revisão `1ede4d8`, finalizar uma folha com backend Microsoft Graph podia devolver:

```text
'GraphSyncQueue' object has no attribute 'has_unfinished_upload'
```

A chamada foi incluída no fluxo de finalização, mas a implementação correspondente ficou fora do pacote. O teste de produção substituía a fila por uma simulação que incluía esse método e não detetava a omissão. O erro ocorre antes de criar o trabalho de arquivo/envio dessa tentativa e também afeta folhas sem SADI.

## Correção

- A fila real identifica publicações do mesmo rascunho ainda pendentes, em execução ou falhadas, incluindo os caminhos de ficheiros temporários e de versões anteriores.
- A finalização prossegue quando essa publicação termina; a consulta não altera nem reprocessa trabalhos.
- Foi completada a dependência de finalização atómica na fila: trabalhos com `_commit_guard` aguardam a confirmação da gravação na base de dados de edição antes de publicar o arquivo ou enviar mensagens. Um estado desconhecido ou uma gravação falhada não autoriza o envio.
- Os testes de produção passam a usar a classe `GraphSyncQueue` real, numa base de dados SQLite temporária, com Graph, PDF e comunicações simulados. Foram acrescentados testes para os estados da fila, finalização com/sem SADI, repetição sem duplicação e retenção antes de confirmar a gravação.

## Instalação sobre a revisão 1ede4d8

Validação local: `python -B -m pytest -q` — 298 testes aprovados. O erro original foi reproduzido no teste antes da correção e deixou de ocorrer depois dela.

1. Fazer backup do código, configuração e dados persistentes pelo procedimento habitual do servidor.
2. Publicar o pacote corrigido. O único ficheiro de código operacional alterado neste hotfix é `src/services/graph_sync_queue.py`; os outros ficheiros alterados são testes e documentação. O mesmo código deve ser usado pela app e pelo worker.
3. Reiniciar a aplicação e assegurar que a próxima execução do worker usa o ficheiro atualizado, pelo procedimento normal do Plesk.
4. Confirmar a revisão instalada antes de retomar a finalização. Uma publicação que esteja efetivamente pendente continuará a apresentar a indicação de sincronização por concluir.

Não são necessárias novas variáveis, novas credenciais ou migrações do esquema da base de dados. Preservar `FS_SECRET_KEY`, rascunhos, checklists, arquivos e filas. Não apagar bases de dados nem repetir trabalhos falhados como parte desta atualização. Qualquer envio anterior com resultado ambíguo deve ser reconciliado antes de ser repetido.

A correção foi preparada e testada localmente com dados fictícios; a instalação e a validação no servidor continuam a cargo do responsável pelo Plesk. Nenhuma folha real foi enviada ou reprocessada durante o diagnóstico.
