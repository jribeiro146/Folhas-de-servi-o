# Docker/Plesk — entrega reconciliada de 28/09/2026

Base de produção: `1ede4d8a690593a5f2b1d2908f3670fc1d038d7d`.
Construir a raiz desta branch. O pacote `migracao/App_Folhas_Checklists/aplicacao/`
da entrega anterior não é a base desta instalação.

## Identidade e dados existentes do hs4

O implementador confirmou `appuser` com UID **100** e GID **101**, proprietário
de todo `/app/data`. O volume `app_app-data` contém a SADI em utilização real.
Esta entrega mantém o formato JSON, a chave do nome dos ficheiros, a localização
`FS_APP_DATA_DIR/sadi` e a leitura do armazenamento de `1ede4d8`.
Não existe migração de checklists, alteração automática de donos ou limpeza no arranque.

O Dockerfile admite `ARG APP_UID=10001` e `ARG APP_GID=10001`. Os valores por
defeito servem instalações novas. No hs4 usar obrigatoriamente **100:101**:

```sh
docker compose -p app build --build-arg APP_UID=100 --build-arg APP_GID=101
```

Também se podem definir `APP_UID=100` e `APP_GID=101` no `.env` existente para
preservar a escolha em builds posteriores. São argumentos de build; alterar apenas
variáveis no container não muda a identidade da imagem. O `chown` do Dockerfile
afeta apenas diretórios criados durante o build, antes de montar volumes.
Não executar `chown -R` no volume existente. UID/GID iguais permitem voltar à
imagem anterior sem conversão dos dados nem das permissões.

Manter **700** nos diretórios SADI e **600** nos JSON/PDF/manifestos privados.
O formato do armazenamento privado existente e o respetivo schema permanecem sem alterações.
Na primeira finalização sem rascunho prévio, a pasta SADI é também criada
explicitamente com 700; pastas existentes não são regravadas nem têm o dono alterado.
Os testes Linux verificam as permissões dos rascunhos e dos conjuntos PDF.
A fixture `tests/fixtures/sadi-private-1ede4d8.json` foi gravada pelo código exato
da base, com dados fictícios; o teste copia esses bytes para o nome histórico e
lê pela API autorizada sem regravar, mudar timestamps ou criar PDFs.
Isso não substitui a verificação local, pelo implementador, da checklist real.

## Compose de referência

Manter a pasta `/var/www/vhosts/service.sensorpoint.pt/app` e o projeto Compose
`app`, para continuar a usar **app_app-data**. Não executar `down -v`, renomear
o projeto ou criar um volume vazio para contornar permissões.

| Serviço | Nome do container | Execução |
|---|---|---|
| app | `folhas-servico` | Gunicorn, `127.0.0.1:8000:8000` |
| worker | `folhas-servico-worker` | `python -B -m src.queue_worker --watch --interval 5` |

Os dois serviços partilham imagem, `.env`, volume e bind `./src:/app/src`.
O código do checkout montado sobrepõe-se ao da imagem: **ambos têm de corresponder
ao commit da entrega**. O rollback tem de repor também `src`, além da imagem.

O bind de logs é mantido para ambos os serviços. O caminho de referência do host é
`/var/www/vhosts/service.sensorpoint.pt/private/folhas-servico/logs`, configurável
por `FS_LOG_HOST_DIR`; o destino interno explícito é `/app/logs`, com
`FS_LOG_DIR=/app/logs`. Se o servidor usar outro caminho privado, manter essa origem
em `FS_LOG_HOST_DIR`. Confirmar que o diretório existente é acessível a 100:101;
não o substituir por um diretório criado implicitamente como root.

Preservar o `.env` operacional e a mesma `FS_SECRET_KEY`, usada também para
validar assinaturas já gravadas. Não copiar `.env.example` sobre a configuração.
Manter estes valores no hs4:

```dotenv
FS_ENVIRONMENT=production
FS_MAINTENANCE_ENABLED=true
FS_GRAPH_QUEUE_IN_WEB=false
FS_PDF_BROWSER_NO_SANDBOX=true
APP_UID=100
APP_GID=101
```

Preservar autenticação Microsoft, caminhos Graph existentes e acesso SADI apenas
às contas autorizadas `acarvalho@sensorpoint.pt` e `jribeiro@sensorpoint.pt`.
O `HOME` da imagem passa a `/home/appuser`, criado e gravável por esse utilizador.
Se o `.env` atual ainda definir `HOME=/tmp`, remover essa sobreposição para usar
o HOME testado. O Chromium é `/usr/bin/chromium`.

## Validação da imagem antes de trocar os containers

```sh
docker run --rm --network none folhas-servico:latest id
docker run --rm --network none -e FS_PDF_BROWSER_NO_SANDBOX=true \
  folhas-servico:latest python -B tools/smoke_pdf.py
```

Se `FS_IMAGE` usar outra etiqueta, usar essa mesma imagem nos comandos. O teste
cria um PDF fictício de duas páginas, confirma o texto com `pypdf` e imprime
UID, GID, HOME e estado do sandbox. Não fornecer `--env-file`, volumes operacionais
ou credenciais ao smoke. O sandbox desligado é necessário no hs4; a imagem não o
desliga por defeito para outras instalações.

O workflow `Production reconciliation` executa testes Linux e constrói imagens
com 10001:10001 e 100:101. Os artefactos `image-evidence-default` e
`image-evidence-hs4` registam commit, ID da imagem e saída real de `smoke_pdf.py`.
Não publicam imagens nem fazem deploy.

## Sequência de instalação e reversão

1. Registar commit e ID/etiqueta da imagem atual, configuração Compose efetiva,
   montagens e identidade do container. Guardar também a configuração e checkout
   anteriores, sem colocar segredos no Git ou no comentário de entrega.
2. Construir a candidata com 100:101 e concluir os testes da imagem acima.
3. Num período sem edições, parar app e worker e fazer backup consistente de
   `app_app-data`, incluindo SADI e SQLite/WAL, preservando donos e modos.
   Guardar o backup num destino privado. Não copiar só os JSON SADI.
4. Atualizar o checkout para o commit da entrega, manter `.env` e montagens
   operacionais, confirmar UID/GID, e executar `docker compose -p app up -d --no-build`.
5. Confirmar ambos os containers, manifest público HTTP 200, login e acesso SADI.
   Abrir a checklist preenchida existente e as duas vazias; comparar conteúdo,
   donos e modos com o backup. Esta verificação não requer finalizar nem enviar email.
6. Confirmar logs e processamento da fila existente. Não iniciar um segundo worker
   por cron se o serviço Compose já o processar continuamente.
7. Fazer login na aplicação, abrir a lista de folhas e usar **Atualizar**.
   A primeira atualização completa cria `.graph_active_files.json` no diretório
   local de Activas. Até existir inventário confirmado a lista está vazia por
   segurança; não copiar um índice de outra instalação nem mostrar a cache antiga.
   Acompanhar `/api/graph/status` na sessão autenticada e confirmar que terminou;
   conferir a lista e que não existem ficheiros indisponíveis no índice.
   Um nome inválido é omitido com o evento `graph_inventory_item_skipped`
   (`reason=invalid_name`); os restantes ficheiros continuam disponíveis.
   Corrigir o nome de origem e atualizar novamente para o incluir.

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

Para reverter: parar app e worker, repor checkout/src, configuração e imagem
anteriores e arrancar sem reconstruir. Os ficheiros SADI mantêm o formato e
100:101. Não restaurar um backup sobre trabalho posterior sem avaliação do operador;
uma restauração de dados pode perder edições entretanto efetuadas.
