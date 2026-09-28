# Preparação de alojamento e recuperação

Esta entrega prepara Docker e mantém a SADI limitada à demonstração local isolada. Não autoriza publicação, migração de dados, envio real ou arranque de um worker sobre uma fila operacional. As diferenças para o servidor referido nas notas de 26/09 continuam a exigir comparação da revisão e configuração efetivas.

## Imagem e processos

`aplicacao/Dockerfile` instala Chromium e fontes, usa `appuser` sem privilégios (UID/GID 10001) e dá-lhe um HOME com escrita em `/home/appuser`. A base de dados, cache, Excel local e logs por defeito ficam sob `/app/data`. O contexto de build inclui apenas código, requisitos, lançador WSGI e duas ferramentas sintéticas; não inclui `.env`, dados, logs ou filas.

`docker-compose.yml` define `app` e `worker` a partir da mesma imagem, configuração e volume `app-data`. Só a app publica `127.0.0.1:8000`. O worker executa `python -B -m src.queue_worker --watch --interval 5`; `FS_GRAPH_QUEUE_IN_WEB=false` é imposto nos dois serviços para evitar consumidores adicionais no Gunicorn. O Compose não monta código do host. Se o destino já usa uma montagem `/app/src` ou de logs, reconciliá-la explicitamente: uma montagem pode sobrepor o código da imagem.

O SQLite usa WAL, timeout de concorrência e exclusão do consumidor; app e worker devem partilhar armazenamento local no mesmo host. Não colocar as bases SQLite num volume de rede/OneDrive nem distribuir os processos por hosts diferentes. A suite sintética inclui concorrência de duas instâncias sobre a mesma fila; estado `Up` do container não substitui essa validação nem comprova entrega.

## Verificação da imagem candidata

Numa máquina com Docker, a partir de `aplicacao/`, construir uma tag exclusiva e executar o teste sem volumes, sem `.env` e sem rede:

```sh
docker build -t folhas-servico:candidato-20260928 .
docker run --rm --network none folhas-servico:candidato-20260928 python -B tools/smoke_pdf.py
```

O teste conserva apenas a seleção explícita do navegador e do sandbox, remove variáveis operacionais herdadas, cria diretórios novos, bloqueia a rede Python e importa apenas o serviço de PDF. Não inicia Flask nem workers. Gera duas páginas fictícias e usa `pypdf` para confirmar número de páginas e texto legível. A saída contém tamanho, páginas, UID, HOME e estado do sandbox; guardar esta saída junto da tag/digest da imagem candidata. `--network none` impede também rede do processo Chromium. No host, executar `python -B tools/smoke_pdf.py` após instalar `requirements-smoke.txt`; o teste fora de Docker não valida a imagem Linux e o bloqueio Python não substitui o isolamento de rede do navegador.

O sandbox fica ativo por defeito. Se o ambiente Docker de destino bloquear a criação do sandbox, não repetir com root. Avaliar primeiro as permissões de sandbox suportadas pelo host; só quando se confirmar o isolamento do container, execução sem privilégios e HTML local controlado, testar explicitamente:

```sh
docker run --rm --network none -e FS_PDF_BROWSER_NO_SANDBOX=true folhas-servico:candidato-20260928 python -B tools/smoke_pdf.py
```

Essa opção reduz a proteção do navegador e deve ficar documentada no ambiente privado do destino apenas se necessária. O teste nunca a ativa automaticamente. Um browser instalado ou um PDF gerado no Windows não prova que a imagem funciona.

## Configuração de destino

`.env.example` é uma referência sem segredos, não deve substituir um `.env` existente. Rever diferenças com valores sensíveis ocultados. Definir `FS_ENVIRONMENT` explicitamente: a ausência com Microsoft/Graph produz agora um aviso, mas mantém `development`, cookies sem Secure e transportes externos bloqueados. `production` mantém os cookies Secure e participa na autorização técnica de transportes; não ativar integrações em testes. A cópia atual não passa a disponibilizar SADI operacional ao definir `production`.

Variáveis e comportamentos desta entrega:

| Configuração | Comportamento |
|---|---|
| `HOME` | Imagem define `/home/appuser`, com escrita para UID 10001. |
| `FS_BASE_PATH`, `FS_APP_DATA_DIR`, `GRAPH_CACHE_DIR` | Imagem/Compose colocam dados locais sob `/app/data`. |
| `FS_GRAPH_QUEUE_IN_WEB` | Imagem e Compose usam `false`; consumidor no serviço `worker`. |
| `FS_IMAGE` | Seleciona a mesma tag para os dois serviços, por defeito `folhas-servico:local`. Usar tag exclusiva na release. |
| `FS_PDF_BROWSER_PATH` | Imagem define `/usr/bin/chromium`; host pode usar deteção automática. |
| `FS_PDF_BROWSER_NO_SANDBOX` | Sem alteração ao valor seguro por defeito; exemplo explícito `false`. |
| `FS_PDF_TEMP_DIR` | Opcional, pasta temporária com escrita; teste usa uma pasta nova própria. |
| `FS_ENVIRONMENT` | Sem mudança automática; aviso quando ausente com Graph/Microsoft. |

Antes de arrancar serviços num destino autorizado, confirmar projeto e volume existentes; por exemplo, projeto `app` pode ter volume `app_app-data`. Mudar a identidade Compose pode criar um volume vazio. Verificar também que o UID da nova imagem tem acesso aos ficheiros existentes e planear qualquer adaptação de proprietário; não executar `chown` recursivo sobre um volume sem confirmar âmbito e backup. O código não corrige permissões de volumes operacionais automaticamente.

## Piloto e recuperação

1. Registar revisão, tag/digest, configuração sem segredos, identidade Compose, montagens, permissões e localização real do volume. Preservar imagem/código/Compose/configuração anteriores como um conjunto.
2. Concluir testes Python/JavaScript, teste de PDF dentro da imagem e ensaio funcional sintético de gravação, conflito, assinatura e finalização. Validar manifest público e impressão em dispositivos reais antes de um piloto de campo.
3. Num deploy posteriormente autorizado, parar escritas e consumidores conforme a janela acordada e obter backup consistente dos dados, incluindo SQLite/WAL. Confirmar que o backup é legível antes de mudar a release. Não copiar apenas o `.sqlite3` enquanto há processos a escrever.
4. Aplicar a tag aprovada e apenas as diferenças necessárias de configuração, preservando projeto e volume. Verificar app e worker, PDF, login/cookies, lista e estado da fila. Não reexecutar envios ambíguos sem reconciliação.
5. Se a validação falhar, suspender novos consumidores e repor em conjunto imagem, código montado (se existir), Compose e configuração anteriores. Não apagar volumes nem usar `docker compose down -v`.
6. Restaurar dados é uma decisão separada: pode perder trabalho posterior ao backup. Comparar estado/revisões, verificar compatibilidade do esquema e obter autorização específica antes de restaurar um volume; não automatizar esse passo.

## Passenger/WSGI fora de Docker

Não usar o procedimento Compose por analogia. Instalar `requirements-server.txt` e o servidor WSGI escolhido; instalar/configurar Chromium no host com HOME e pastas de dados/temporários graváveis pelo utilizador de serviço. Configurar `FS_GRAPH_QUEUE_IN_WEB=false` e um serviço/tarefa independente para `src.queue_worker`, com o mesmo ambiente e armazenamento da web. Preservar logs fora do document root. Aplicar o mesmo teste PDF com dados fictícios e o mesmo plano de recuperação, adaptando os comandos ao supervisor real.
