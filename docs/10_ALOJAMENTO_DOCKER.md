# Docker/Plesk — entrega identificada e preparação explícita — M14

Build e execução Linux/Docker ainda não realizados neste processo. O João está a
obter um ambiente. Até lá o instalador deve executar a
[nota de validação](VALIDACAO_DOCKER_INSTALADOR.md) e registar os resultados.
A referência histórica 1ede4d8 não identifica a nova entrega.

## Construir do artefacto

Gerar o ZIP do SHA limpo testado conforme [M13](IDENTIDADE_DA_VERSAO.md), conferir
artifact_sha256 e extrair para pasta nova. O contexto Docker é essa pasta, com
release-manifest.json. Não construir de checkout sem manifesto nem regenerar o
manifesto dentro do Docker para esconder diferenças.

```sh
# Na pasta extraída, sem configuração privada no contexto:
docker build --target production -t folhas-servico:SHA_COMPLETO .
```

O build verifica o manifesto. A imagem copia src, tools completos, WSGI, todos os
requisitos atuais, Dockerfile, .dockerignore, compose e manifesto. Novos ficheiros
cobertos pela identidade também terão de ser copiados: a verificação falha se
faltarem. O target final production usa appuser e inclui ferramentas offline e
Chromium, sem testes/pytest/Git. O target qa deriva da mesma camada runtime e
acrescenta Git, pytest e fixtures fictícias para ensaios. O target por defeito
continua a ser production.

## Identidade e volumes

Novas instalações usam 10001:10001. A documentação histórica identifica hs4 como
100:101 e volume app_app-data; o profissional deve confirmar os valores reais.
Preservar UID/GID, projeto Compose, volume, caminhos absolutos e FS_SECRET_KEY.
Para uma instalação confirmada em 100:101:

```sh
docker build --target production --build-arg APP_UID=100 --build-arg APP_GID=101 -t folhas-servico:SHA_COMPLETO .
```

Não executar chown recursivo nem recriar volumes para contornar incompatibilidades.
O chown do Dockerfile afeta só diretórios vazios da imagem. /app/data fica vazio;
cache e restantes dados são criados depois de contrato válido. Um volume antigo
não recebe preparação automática. A persistência e o preenchimento inicial dos
volumes seguem as [regras Docker](https://docs.docker.com/engine/storage/volumes/).

## Compose e preparação

FS_IMAGE é obrigatório e deve identificar a etiqueta do commit verificado; latest
já não é implícito. APP_UID/APP_GID continuam argumentos de build. Preservar .env
privado, nunca incluído na imagem/ZIP. Web e worker partilham imagem e app-data,
sem montagem de src/tools. Não editar código de processos em execução.

O bind de logs mantém a origem histórica configurável por FS_LOG_HOST_DIR e
destino /app/logs. create_host_path=false exige pasta privada existente e acessível
ao UID/GID correto, evitando criação silenciosa como root. O instalador confirma
caminho/permissões fora do document root. HOME é /home/appuser e Chromium fica em
/usr/bin/chromium; manter a opção de sandbox adequada ao servidor.

prepare só corre explicitamente; não acompanha up, não recebe .env, não tem rede
nem monta logs. Usa a mesma imagem/volume e apenas init. Os perfis suportam essa
ativação explícita ([Docker](https://docs.docker.com/reference/compose-file/profiles/)).

```sh
# APENAS instalação nova, volume vazio, projeto/nome conferidos:
docker compose -p PROJETO_CONFIRMADO run --rm --no-deps prepare
# Esperado: command=init, verified=true, epoch=1; exit 0.
```

App/worker sem contrato recusam o arranque. init recusa um volume legado sem o
adotar/apagar. Nesse caso seguir [M06](MIGRACAO_OFFLINE.md) com inventário, cópia
privada e escritores parados. Com contrato compatível de outra versão, seguir
[ativação explícita](PREPARACAO_RUNTIME.md). Os caminhos absolutos têm de ser
preservados: mudar /app/data ou caminhos Excel/cache não é migração automática.

Antes da troca real, o profissional guarda imagem/configuração/contrato anteriores,
valida backup e define janela sem escritores. Depois de migrar/ativar conforme o
caso, usa a mesma FS_IMAGE na web/worker com --no-build e confirma identidade,
HTTP, login, reservas, SADI privada e diagnóstico da fila. Reconciliar trabalhos
retidos/incertos antes de repetir. Não ligar um worker por cron se o Compose já
processa a fila. Nenhum destes passos foi executado na instalação real.

Rollback respeita contrato/formatos e trabalho posterior. [M07](BACKUP_RESTAURO.md)
restaura com bloqueio global e fila retida, libertados após reconciliação. Se
leitores de diagnóstico criarem locks/journals, parar os processos e diagnosticar;
não apagar journals em uso para fazer passar uma recusa.

A CI foi adaptada só para gerar o ZIP antes do build e registar identidade da
imagem. Não foi executada remotamente; triggers, pinning e matriz completa são
M15. Reproduzir o ZIP não fixa downloads de dependências/imagem base.

## Ferramentas offline dentro do contentor

Executar as ferramentas na imagem verificada, com os caminhos **vistos pela
aplicação dentro do contentor**. Não usar caminhos do host como
/var/lib/docker/volumes/.../_data, nem `compose run app`: esse serviço carrega
.env, rede e logs operacionais. O código passa para /opt/folhas; /app/data,
/app/logs e as raízes externas confirmadas mantêm-se. Esta separação permite
que M06/M07 recusem dados dentro do artefacto sem impedir a manutenção do volume.
Não é necessário nem permitido mover os dados para acompanhar o código.

Os exemplos seguintes são Bash para o profissional, depois de confirmar o
inventário e parar **todos** os escritores, incluindo cron e versões anteriores.
Não foram executados em produção. Substituir as variáveis pelos valores privados
confirmados; não guardar a chave ou configuração junto destes comandos.

~~~bash
set -euo pipefail
: "${FS_TARGET_IMAGE:?Imagem verificada da versão alvo}"
: "${FS_RUNTIME_VOLUME:?Nome exato do volume existente, confirmado no projeto}"
: "${FS_RUN_UID:?UID confirmado da instalação}"
: "${FS_RUN_GID:?GID confirmado da instalação}"
: "${FS_WORK_HOST:?Pasta privada local existente, fora dos dados e do artefacto}"
docker volume inspect "$FS_RUNTIME_VOLUME" >/dev/null
test -d "$FS_WORK_HOST"
FS_MOUNTS=(--mount "type=volume,src=$FS_RUNTIME_VOLUME,dst=/app/data"
           --mount "type=bind,src=$FS_WORK_HOST,dst=/maintenance")
FS_EXTRA_ROOTS=()
# Se Excel/cache estiverem FORA de /app/data, acrescentar cada montagem original
# e o MESMO caminho de destino efetivamente usado; exemplo apenas ilustrativo:
# FS_MOUNTS+=(--mount "type=bind,src=$FS_EXCEL_HOST,dst=/srv/folhas/Excel")
# FS_EXTRA_ROOTS+=(--include-root /srv/folhas/Excel)
# Repetir para todas as raízes externas inventariadas. Não duplicar subpastas.
fs_offline() {
  local image="$1"; shift
  docker run --rm --network none --user "$FS_RUN_UID:$FS_RUN_GID" \
    "${FS_MOUNTS[@]}" "$image" python -B "$@"
}
~~~

O workspace e as raízes devem ser graváveis por esse UID/GID e permitir preservar
os atributos existentes. Uma recusa de permissões exige diagnóstico; não trocar
para root nem aplicar chown recursivo para a contornar. As ferramentas não leem
.env nem importam a app, e a imagem não contém configuração privada.

### Volume legado sem contrato: preparar e aplicar M06

Preservar primeiro uma cópia offline independente de **todo** o conjunto original,
incluindo WAL/journals e raízes externas, pelo procedimento do instalador com
escritores parados. M07 exige contrato/formatos atuais e não serve para fazer
backup de uma instalação legada sem os converter. O snapshot original do M06
fica também preservado no workspace, mas é uma evidência distinta do backup M07.

~~~bash
fs_offline "$FS_TARGET_IMAGE" tools/migrate_runtime.py prepare \
  --data-dir /app/data "${FS_EXTRA_ROOTS[@]}" \
  --workspace /maintenance/m06 --writers-stopped --roots-complete
# Conferir state=prepared, inventário e quarentenas antes do próximo comando.
fs_offline "$FS_TARGET_IMAGE" tools/migrate_runtime.py apply \
  --workspace /maintenance/m06 --writers-stopped
fs_offline "$FS_TARGET_IMAGE" tools/prepare_runtime.py inspect --data-dir /app/data
~~~

Esperado: apply state=complete; inspect identifica contrato da versão alvo.
Uma interrupção usa o mesmo workspace/imagem para retomar, conforme M06.
Não executar activate como substituto de uma migração necessária.

### Contrato atual: backup M07 e ativação de outra versão compatível

Para backup usar a imagem verificada correspondente ao **source_sha256 ativo**,
que disponha destas ferramentas e mantenha dados fora do artefacto. Uma imagem
legada sem M07/manifesto ou com dados dentro da sua raiz de código exige a cópia
offline anterior, não uma regeneração de manifesto para a adaptar. Após M06,
a imagem alvo é também a imagem atual e já permite criar o primeiro backup M07.

O ficheiro privado da chave contém apenas o valor ativo de FS_SECRET_KEY em UTF-8,
conforme [M07](BACKUP_RESTAURO.md), e fica fora de dados/workspace/cópias. Montá-lo
só de leitura; nunca passar o valor na linha de comando. Escolher um nome novo
para cada backup (também depois de uma tentativa interrompida).

~~~bash
: "${FS_CURRENT_IMAGE:?Imagem verificada correspondente ao contrato ativo}"
: "${FS_KEY_HOST:?Ficheiro privado existente, apenas com o valor da chave}"
: "${FS_BACKUP_NAME:?Nome novo simples, sem barras, para esta cópia}"
test -f "$FS_KEY_HOST"
[[ "$FS_BACKUP_NAME" =~ ^[A-Za-z0-9_-]+$ ]]
FS_MOUNTS+=(--mount "type=bind,src=$FS_KEY_HOST,dst=/run/secrets/fs-key,readonly")
fs_offline "$FS_CURRENT_IMAGE" tools/backup_runtime.py backup \
  --data-dir /app/data "${FS_EXTRA_ROOTS[@]}" \
  --backup "/maintenance/$FS_BACKUP_NAME" --key-file /run/secrets/fs-key \
  --writers-stopped --roots-complete
fs_offline "$FS_CURRENT_IMAGE" tools/backup_runtime.py verify \
  --backup "/maintenance/$FS_BACKUP_NAME" --key-file /run/secrets/fs-key
# Conferir state=verified / exit 0 e guardar a evidência PRIVADA.
fs_offline "$FS_CURRENT_IMAGE" tools/prepare_runtime.py inspect --data-dir /app/data
~~~

Se for uma atualização compatível (formatos já atuais), copiar de inspect o
runtime.source_sha256 para FS_PREVIOUS_SOURCE e conferir contra a versão anterior.
Isto é o SHA do código, não o SHA do ZIP ou o ID Docker.

~~~bash
: "${FS_PREVIOUS_SOURCE:?source_sha256 do contrato ativo confirmado}"
fs_offline "$FS_TARGET_IMAGE" tools/prepare_runtime.py activate \
  --data-dir /app/data --expected-source "$FS_PREVIOUS_SOURCE" --writers-stopped
~~~

Esperado: command=activate, runtime.source_sha256 igual ao release.source_sha256
da imagem alvo e epoch incrementado. Incompatibilidade de formatos, source
inesperado ou restore_hold recusam a operação. Não editar SQLite para a forçar.

### Restauro M07

Continuar com escritores parados. Preservar o conjunto acidentado por procedimento
do instalador e apresentar raízes vazias nos **mesmos destinos**; não apagar dados
por estes exemplos. Usar a imagem correspondente ao backup, a mesma chave e um
workspace novo. FS_MOUNTS deve continuar a incluir todas as raízes originais.

~~~bash
: "${FS_RESTORE_IMAGE:?Imagem verificada correspondente ao backup}"
fs_offline "$FS_RESTORE_IMAGE" tools/backup_runtime.py restore \
  --backup "/maintenance/$FS_BACKUP_NAME" --workspace /maintenance/recovery \
  --key-file /run/secrets/fs-key --writers-stopped
~~~

Esperado: state=held e reconciliation_required=true. Manter a app parada.
A libertação abaixo só se aplica **depois** da reconciliação externa documentada
pelo responsável, incluindo efeitos posteriores à cópia. O bloqueio e as filas
retidas não devem ser contornados para testar conectividade.

~~~bash
: "${FS_RECOVERY_ACTOR:?Responsável pela decisão}"
: "${FS_RECOVERY_REASON:?Motivo e referência ao relatório privado de reconciliação}"
fs_offline "$FS_RESTORE_IMAGE" tools/backup_runtime.py release \
  --workspace /maintenance/recovery --key-file /run/secrets/fs-key \
  --writers-stopped --external-effects-reconciled \
  --actor "$FS_RECOVERY_ACTOR" --reason "$FS_RECOVERY_REASON"
~~~

Esperado: state=released; os trabalhos individuais não concluídos continuam
retidos. Conservar backup, workspace e decisão. Rollback para outra imagem requer
também compatibilidade/ativação explícita; trocar apenas a etiqueta não basta.

## Reconciliação da fila e marcadores antigos

A atualização não altera os JSON SADI. Na fila existente, uma publicação posterior
confirmada do mesmo rascunho encerra uploads anteriores pendentes/falhados com
`result.superseded_by`, mantendo payload e instantâneos anteriores para auditoria.
Um upload falhado mais recente, ou de outro rascunho, continua pendente de resolução;
não apagar a base SQLite nem marcar trabalhos manualmente como concluídos.
Se uma gravação posterior já iniciou a publicação mas falhou parcialmente, os
retries dos instantâneos anteriores ficam retidos com `blocked_by_newer_upload`.
Repetir apenas a gravação mais recente: a anterior não pode sobrescrever conteúdo
novo já publicado. Quando a mais recente termina, as anteriores ficam substituídas.

Durante o refresh, metadados remotos deixam de ser descarregados para a cache.
Os ficheiros remotos `.fs-local-dirty` e `.fs-local-dirty.tmp`, publicados por
versões antigas, são eliminados individualmente com o eTag obtido na listagem.
Um conflito ou falha adia a limpeza e regista `graph_legacy_marker_cleanup_deferred`;
a próxima atualização volta a tentar. Não se eliminam documentos nem pastas.
Uma cópia local importada do marcador só é removida quando tem a antiga sidecar
Graph e os bytes ainda coincidem com o marcador remoto. Edições locais posteriores
conservam a sua proteção e adiam a limpeza remota até à publicação local confirmada.
Após limpar, a app obtém a versão da pasta antes de voltar a listar os filhos,
para não associar uma versão nova a conteúdo antigo. Não apagar marcadores locais em bloco.
Se o marcador remoto já não existir, não há prova suficiente para apagar a cópia
local antiga automaticamente. O operador deve preservar uma cópia privada do
rascunho, comparar os ficheiros locais e remotos e confirmar a inexistência de
edições por publicar antes de remover apenas esse marcador e a respetiva sidecar.

Novas pastas usam um nome temporário `.fs-upload-<identificador aleatório>`, que
nunca aparece como folha, antes da mudança para o nome final. A prova de propriedade
fica em `.fs-upload-owner.json`, não é enviada ao SharePoint e permite recuperar
respostas POST/PATCH perdidas. Preservar esse ficheiro e os instantâneos da fila.
Uma pasta criada por uma versão antiga cuja resposta se perdeu, sem qualquer
prova local do ID, **não pode ser adotada automaticamente**. Após backup e com
app/worker parados, o operador deve comparar origem, conteúdo e histórico de
versões no SharePoint. Se confirmar que é apenas uma pasta vazia órfã desta
aplicação, movê-la para uma área de recuperação fora de Activas e repetir o
trabalho pela aplicação. Se contiver dados, reconciliá-los antes de repetir.
Nunca eliminar ou adotar uma pasta apenas por ter o mesmo nome.

### Recuperar rascunhos bloqueados na mudança de nome (30/09)

Os logs de 30/09 confirmaram HTTP 412 no PATCH que muda o nome da pasta
`.fs-upload-*`, antes de carregar os ficheiros. A revisão `29489fa` tratava
esse conflito como terminal; a confirmação de gravação era apenas local.
Esta correção relê a versão da pasta antes de cada tentativa, verifica ID,
nome e ausência de filhos e mantém `If-Match`. Faz no máximo três tentativas
imediatas; se a versão continuar a mudar, a fila agenda nova tentativa.
O tratamento excecional só se aplica à pasta temporária comprovadamente própria
e vazia. Ficheiros existentes, arquivos, pastas substituídas/com conteúdo e
colisões de nome continuam protegidos contra sobrescrita.

Após instalar o commit indicado no PR **na web e no worker**, usando a mesma imagem verificada nos dois processos, recuperar cada rascunho afetado desta forma:

1. Preservar o volume, a fila, os instantâneos e `.fs-upload-owner.json`.
   Não apagar as pastas temporárias nem recriar a folha a partir do original.
2. Abrir o rascunho existente (usar o URL que estava aberto se ainda não figura
   no inventário remoto) e clicar **Guardar rascunho**. Isto publica a versão
   atual do mesmo rascunho; não envia email.
3. Confirmar `status=complete` no trabalho `graph_job_id` devolvido pelo save,
   consultando `/api/graph/jobs/<graph_job_id>` na sessão autenticada. A pasta
   temporária é reutilizada e passa ao nome final. Uploads antigos da mesma
   origem ficam substituídos apenas depois da nova publicação concluída.
4. Atualizar a lista e confirmar a folha no SharePoint. Só então finalizar,
   por ação do utilizador. Se continuar a falhar, conservar o erro e pedir
   análise; não marcar trabalhos como concluídos nem repetir a fila em bloco.

A interface distingue agora publicação pendente de gravação completa e,
ao finalizar, distingue falha terminal de um trabalho que ainda vai tentar.
Esta atualização não repete automaticamente jobs antigos terminais, envios de
email ou arquivos. Também não elimina pastas temporárias órfãs sem prova local.

Trabalhos `held` aguardam a confirmação da operação na base de edição. Após
`FS_GRAPH_COMMIT_GUARD_SECONDS` (3600 por defeito, mínimo 60) passam a `failed`
sem repetição automática, com motivo visível. A repetição manual volta a verificar
o commit; o tempo decorrido não autoriza envio, arquivo ou remoção. A expiração
preserva os comprovativos existentes de email aceite, para não repetir envios.

Conflitos reais 409/412 em `archive_and_remove` permanecem terminais. Verificar
as versões e os artefactos já publicados antes da repetição manual; repetir sem
resolver o conflito não altera o resultado. A fila não elimina a origem nem
envia email quando a publicação do arquivo falha. Não retirar `If-Match` nem
forçar a remoção para contornar conflitos. Contenção temporária de estado responde
503 com `Retry-After: 2`; repetir o pedido após esse intervalo.

Para reverter, parar todos os escritores e avaliar contrato/formatos antes de
ativar a versão anterior. Repor apenas a imagem não muda a versão aceite pelos
dados. Seguir PREPARACAO_RUNTIME.md e BACKUP_RESTAURO.md; preservar trabalho
posterior e reconciliar efeitos externos antes de libertar um restauro.
