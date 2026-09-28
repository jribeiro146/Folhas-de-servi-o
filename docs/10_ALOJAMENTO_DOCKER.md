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

Para reverter: parar app e worker, repor checkout/src, configuração e imagem
anteriores e arrancar sem reconstruir. Os ficheiros SADI mantêm o formato e
100:101. Não restaurar um backup sobre trabalho posterior sem avaliação do operador;
uma restauração de dados pode perder edições entretanto efetuadas.
