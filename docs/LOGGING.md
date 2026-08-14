# Logging operacional

A aplicação escreve eventos operacionais estruturados em JSON Lines: um objeto JSON
por linha, codificado em UTF-8 e com data/hora UTC. O mesmo sistema é inicializado pela
web WSGI, pelo servidor local e pelo worker da fila.

## Onde ficam os logs

O ficheiro ativo chama-se, por defeito, `folhas-servico.jsonl` e fica dentro de
`FS_APP_DATA_DIR/logs`. Pode ser alterado com `FS_LOG_DIR`, que tem de ser um caminho
absoluto num disco local persistente e privado.

| Ambiente | Local predefinido ou recomendado |
| --- | --- |
| Windows local | `%LOCALAPPDATA%\Sensorpoint\FolhasServico\logs\folhas-servico.jsonl` |
| Plesk de produção | `/var/www/vhosts/service.sensorpoint.pt/private/folhas-servico/logs/folhas-servico.jsonl` |
| Docker | `/app/data/logs/folhas-servico.jsonl`, persistido no volume `app-data` |

O diretório não deve ficar no document root, OneDrive, SharePoint sincronizado,
NFS ou SMB. Não deve existir uma rota HTTP para descarregar logs. Em Linux, a app
aplica modo `0750` ao diretório e o handler aplica `0640` aos ficheiros. O utilizador
WSGI e o utilizador da tarefa do worker precisam de acesso de escrita ao mesmo local.

Exemplo de preparação no servidor, ajustando utilizador e grupo:

```bash
install -d -m 0750 -o <utilizador-app> -g <grupo-app> \
  /var/www/vhosts/service.sensorpoint.pt/private/folhas-servico/logs
```

## Como são escritos e atualizados

- Cada evento é acrescentado ao ficheiro ativo e descarregado pelo handler durante
  a emissão; não é necessário esperar pelo encerramento da aplicação.
- A web e o worker podem escrever no mesmo ficheiro no mesmo servidor. Cada processo
  cria o seu próprio handler e um lock entre processos protege escrita e rotação.
- Quando uma nova linha faria o ficheiro ultrapassar `FS_LOG_MAX_BYTES`, o ficheiro é
  rodado. As cópias ficam comprimidas como `folhas-servico.jsonl.1.gz`, `.2.gz`, etc.
- São mantidas `FS_LOG_BACKUP_COUNT` cópias e a mais antiga é eliminada
  automaticamente. Com os valores predefinidos, o limite nominal antes da compressão
  é cerca de 110 MiB: um ficheiro ativo de 10 MiB e dez cópias.
- O pequeno ficheiro `.__folhas-servico.jsonl.lock` pertence ao mecanismo de coordenação.
  Não deve ser editado ou apagado enquanto existirem processos da aplicação.
- Não configurar `logrotate` sobre este ficheiro: a rotação é feita pela própria
  aplicação e coordenada entre processos.
- Todos os processos que partilham o ficheiro têm de usar o mesmo nome, tamanho e
  número de cópias. Depois de mudar estas opções, reiniciar todos os processos WSGI
  e o worker.

Por defeito, cada linha também é enviada para stderr. Isto permite ao Plesk,
Passenger, Gunicorn ou ao runtime Docker recolher a mesma informação, mas o ficheiro
privado continua a ser a fonte persistente gerida pela aplicação.

## Configuração

| Variável | Predefinição | Função |
| --- | --- | --- |
| `FS_LOG_ENABLED` | `true` | Ativa o logging gerido pela aplicação. |
| `FS_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` ou `CRITICAL`. |
| `FS_LOG_DIR` | `FS_APP_DATA_DIR/logs` | Diretório absoluto, privado e em disco local. |
| `FS_LOG_FILE_NAME` | `folhas-servico.jsonl` | Nome simples, sem componentes de caminho. |
| `FS_LOG_MAX_BYTES` | `10485760` | Tamanho do ficheiro ativo; mínimo 65536 bytes. |
| `FS_LOG_BACKUP_COUNT` | `10` | Número de cópias comprimidas; mínimo 1. |
| `FS_LOG_STDERR` | `true` | Espelha os eventos em stderr. |
| `FS_LOG_REQUESTS` | `true` | Regista a conclusão dos pedidos HTTP relevantes. |

Em produção recomenda-se manter `INFO`. Usar `DEBUG` apenas durante um diagnóstico
curto, porque aumenta o volume. A web e a tarefa `python -m src.queue_worker` devem
receber o mesmo ficheiro de ambiente.

Pedidos bem-sucedidos de autosave, heartbeat, consulta da fila/Graph e tentativa
esperada de adquirir uma folha ocupada ficam em `DEBUG`, evitando milhares de linhas
de polling. Respostas inesperadas `4xx` mantêm-se em `WARNING` e `5xx` em `ERROR`.

## Conteúdo e privacidade

Cada linha inclui, quando aplicável:

- versão do esquema, timestamp UTC, nível, componente, host, PID e thread;
- nome estável do evento, por exemplo `http_request_completed`,
  `graph_job_failed` ou `document_finalized`;
- `request_id`, método HTTP, modelo da rota, status e duração;
- identificadores técnicos e estado da fila necessários ao diagnóstico;
- exceção e stack trace nos erros inesperados.

Todos os pedidos recebem um `X-Request-ID`; um identificador válido enviado pelo proxy
é preservado e o mesmo valor é devolvido na resposta. Isto permite ligar a queixa de
um utilizador à linha correspondente sem guardar o conteúdo do pedido.

Não são registados corpos dos pedidos, query strings, cookies, cabeçalhos de
autorização, assinaturas, fotografias, destinatários ou conteúdo das folhas. A rota é
registada como modelo, por exemplo `/api/file/<name>/send`, e não com o nome real do
ficheiro. O formatador remove ainda campos e padrões comuns de passwords, tokens,
segredos, API keys, Bearer tokens, data URLs e parâmetros de URLs.

Esta proteção é uma segunda barreira: código novo não deve passar payloads, objetos de
pedido/resposta, URLs assinados ou credenciais para o logger.

## Consulta e monitorização

Linux/Plesk:

```bash
tail -F /var/www/vhosts/service.sensorpoint.pt/private/folhas-servico/logs/folhas-servico.jsonl
jq -c 'select(.level == "ERROR" or .level == "CRITICAL")' folhas-servico.jsonl
zgrep '"event":"graph_job_failed"' folhas-servico.jsonl.*.gz
```

Windows PowerShell:

```powershell
Get-Content "$env:LOCALAPPDATA\Sensorpoint\FolhasServico\logs\folhas-servico.jsonl" -Tail 100 -Wait
```

Configurar alertas para `ERROR` e `CRITICAL`, vigiar o espaço livre do volume e recolher
os logs num sistema central se for necessária retenção superior. O `request_id` ou o
`job_id` deve ser usado para seguir uma operação entre linhas. Os logs operacionais não
substituem um registo de auditoria legal.

## Verificação depois do deploy

1. Instalar ou atualizar `requirements-server.txt`.
2. Criar o diretório privado com as permissões corretas.
3. Aplicar as mesmas variáveis à app WSGI e ao worker.
4. Reiniciar todos os processos web e executar uma vez `python -m src.queue_worker`.
5. Fazer um pedido à app e confirmar `logging_configured`,
   `web_application_ready`, `http_request_completed` e
   `queue_worker_cycle_completed` no ficheiro.
6. Confirmar que o cabeçalho `X-Request-ID` da resposta existe e coincide com a linha
   do pedido.

Se a dependência, o diretório ou o ficheiro não puderem ser abertos no arranque, a app
termina com erro em vez de arrancar sem o ficheiro operacional. Uma falha posterior de
I/O, como disco cheio, deve ser detetada também através de stderr e da monitorização do
volume.
