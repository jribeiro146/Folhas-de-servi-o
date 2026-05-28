# Implementacao em servidor - Folhas de Servico

Este documento serve como checklist tecnica para colocar a webapp de Folhas de Servico num servidor Plesk ou equivalente, usando Microsoft Graph como armazenamento e Microsoft Entra ID como autenticacao.

## Objetivo do deploy

A aplicacao Flask fica alojada no servidor e acessivel por um subdominio HTTPS, por exemplo:

```text
https://service.sensorpoint.pt/
```

Os ficheiros operacionais continuam no SharePoint/OneDrive atraves do Microsoft Graph:

```text
Aplicacao/
Activas/
Arquivadas/
Canceladas/
```

O servidor nao deve depender do OneDrive Desktop. A app comunica diretamente com Microsoft Graph para ler folhas ativas, guardar rascunhos e arquivar folhas finalizadas.

## Versoes e dependencias

Versao local testada:

```text
Python 3.13.1
Flask 3.1.3
openpyxl 3.1.5
Pillow 12.1.1
Werkzeug 3.1.8
```

Para servidor, usar preferencialmente Python 3.13.x. Se o Plesk nao disponibilizar Python 3.13, usar Python 3.12.x e testar antes de producao.

Instalar dependencias de servidor com:

```bash
pip install -r requirements-server.txt
```

Notas:

- O ficheiro `requirements.txt` inclui `pywin32>=306`, que e apenas para ambiente Windows/legado.
- Em Linux/Plesk, usar `requirements-server.txt`, porque `pywin32` nao instala em Linux.
- A app web atual nao precisa de Microsoft Excel instalado no servidor.
- A escrita em Excel e feita com `openpyxl`.

## Estrutura a publicar

Publicar estes elementos no servidor:

```text
src/
requirements-server.txt
passenger_wsgi.py
docs/
README.md
```

Evitar publicar como codigo operacional:

```text
.env local
.runtime/
__pycache__/
*.pyc
build/
dist/
data/
Excel/ com ficheiros locais de teste
PDF/ antigo
manual-test-*.log
```

O `.gitignore` ja protege `.env`, `.runtime`, `.tmp-tests`, `__pycache__` e `*.pyc`.

## Configuracao Plesk recomendada

Configuracao da aplicacao Python:

```text
Application root: pasta raiz do projeto no servidor
Application startup file: passenger_wsgi.py
Application entry point: application
Python version: 3.13.x, preferencialmente
```

O ficheiro WSGI criado para Plesk esta em `passenger_wsgi.py`. Ele expoe a variavel `application`, que aponta para a app Flask.

Referencias no codigo:

```text
passenger_wsgi.py:16 - importa a app Flask como application
src/web/app.py:8 - exporta app e create_app
src/web/application.py:649 - instancia app = create_app()
```

Nao usar `src/main.py` como servidor de producao. Esse ficheiro e util para arranque local/desenvolvimento.

Referencias do modo local:

```text
src/main.py:25 - FS_HOST
src/main.py:26 - FS_PORT
src/main.py:44 - FS_NO_BROWSER
src/main.py:63 - app.run(), servidor Flask local
```

## Variaveis de ambiente obrigatorias

No Plesk, configurar as variaveis de ambiente da app Python. Nao colocar segredos no codigo.

Exemplo de configuracao:

```bash
FS_STORAGE_BACKEND=graph
FS_AUTH_PROVIDER=microsoft
FS_SECRET_KEY=<gerar-chave-aleatoria-longa>

FS_APP_DATA_DIR=/var/www/vhosts/service.sensorpoint.pt/private/folhas-servico
GRAPH_CACHE_DIR=/var/www/vhosts/service.sensorpoint.pt/private/folhas-servico/graph-cache

GRAPH_TENANT_ID=afc2679b-0121-4a82-ab15-59c783daedc9
GRAPH_CLIENT_ID=b233caa9-f51f-4d06-91fe-aa6cf26a982a
GRAPH_CLIENT_SECRET=<introduzir-no-plesk-nao-incluir-no-pacote>
GRAPH_SITE_ID=sensorpointpt.sharepoint.com,8f709e50-0cf3-4a4e-abcf-f13fbdd160fd,27bb0914-7252-4a37-aea3-901d38646005
GRAPH_DRIVE_ID=b!UJ5wj_MMTkqrz_E_vdFg_RQJuydScjdKrqOQHThkYAUGXH3i2HPlT5uigZIXMAjU

GRAPH_ACTIVE_PATH=Aplicação/Activas
GRAPH_ARCHIVE_PATH=Aplicação/Arquivadas

MICROSOFT_AUTH_REDIRECT_URI=https://service.sensorpoint.pt/auth/microsoft/callback
MICROSOFT_AUTH_ALLOWED_DOMAINS=sensorpoint.pt,sensorpoint.com
```

Opcional, mas recomendado para separar login e Graph se no futuro forem criadas apps diferentes:

```bash
MICROSOFT_AUTH_TENANT_ID=afc2679b-0121-4a82-ab15-59c783daedc9
MICROSOFT_AUTH_CLIENT_ID=b233caa9-f51f-4d06-91fe-aa6cf26a982a
MICROSOFT_AUTH_CLIENT_SECRET=<introduzir-no-plesk-nao-incluir-no-pacote>
```

Se estas variaveis `MICROSOFT_AUTH_*` nao forem definidas, a app reutiliza as credenciais `GRAPH_*`.

Referencias no codigo:

```text
src/config.py:13 - carrega ficheiro .env simples se existir
src/config.py:16 - permite mudar o ficheiro via FS_ENV_FILE
src/config.py:72 - FS_APP_DATA_DIR
src/config.py:79 - FS_AUTH_PROVIDER
src/config.py:80 - MICROSOFT_AUTH_TENANT_ID
src/config.py:84 - MICROSOFT_AUTH_CLIENT_ID
src/config.py:88 - MICROSOFT_AUTH_CLIENT_SECRET
src/config.py:92 - MICROSOFT_AUTH_REDIRECT_URI
src/config.py:93 - MICROSOFT_AUTH_ALLOWED_DOMAINS
src/config.py:106 - FS_STORAGE_BACKEND
src/config.py:108 - GRAPH_TENANT_ID
src/config.py:109 - GRAPH_CLIENT_ID
src/config.py:110 - GRAPH_CLIENT_SECRET
src/config.py:111 - GRAPH_SITE_ID (referencia do site SharePoint)
src/config.py:112 - GRAPH_DRIVE_ID
src/config.py:113 - GRAPH_ACTIVE_PATH
src/config.py:114 - GRAPH_ARCHIVE_PATH
src/config.py:115 - GRAPH_CACHE_DIR
```

## Onde mudar o URL

O URL de producao deve ser mudado em dois locais: Entra ID e variavel de ambiente da app.

No servidor/app:

```bash
MICROSOFT_AUTH_REDIRECT_URI=https://service.sensorpoint.pt/auth/microsoft/callback
```

No Microsoft Entra ID:

```text
App registrations
Sensorpoint Folhas de Servico
Authentication
Web redirect URI
https://service.sensorpoint.pt/auth/microsoft/callback
```

Para testes locais, o redirect atual e:

```text
http://localhost:5001/auth/microsoft/callback
```

Para producao, deve ficar por exemplo:

```text
https://service.sensorpoint.pt/auth/microsoft/callback
```

Referencias no codigo:

```text
src/web/application.py:128 - microsoft_redirect_uri()
src/web/application.py:129 - usa MICROSOFT_AUTH_REDIRECT_URI ou url_for externo
src/web/application.py:212 - rota /auth/microsoft
src/web/application.py:233 - rota /auth/microsoft/callback
src/services/microsoft_auth_service.py:66 - build_authorization_url()
src/services/microsoft_auth_service.py:150 - troca authorization code por token
```

Importante: o valor no Entra tem de bater exatamente certo com o valor da variavel `MICROSOFT_AUTH_REDIRECT_URI`.

## Microsoft Entra ID

Identificadores ja criados para esta app (nao sao segredos):

```text
Tenant ID: afc2679b-0121-4a82-ab15-59c783daedc9
Application / Client ID: b233caa9-f51f-4d06-91fe-aa6cf26a982a
Object ID: 759e9170-a699-4634-b23b-e4b8ecad2b8e
Callback / Redirect URI: https://service.sensorpoint.pt/auth/microsoft/callback
```

Configuracao minima da App Registration:

```text
Supported account types: Single tenant
Redirect URI Web: https://service.sensorpoint.pt/auth/microsoft/callback
Client secret: criado e guardado no Plesk como variavel
```

Permissoes para login Microsoft:

```text
Microsoft Graph delegated permission:
User.Read
```

Permissoes para armazenamento no SharePoint:

Opcao simples:

```text
Microsoft Graph application permission:
Sites.ReadWrite.All
Admin consent: granted
```

Opcao mais restrita, recomendada para producao quando estiver estabilizado:

```text
Microsoft Graph application permission:
Sites.Selected

Depois conceder permissao apenas ao site/biblioteca usados pela app.
```

Nota de seguranca: o segredo de cliente ja foi usado em testes. Antes de producao, criar um novo segredo no Entra, atualizar o Plesk e remover/revogar o segredo antigo.

## Funcionamento tecnico da app

Fluxo resumido:

1. O utilizador abre a app e faz login com Microsoft.
2. A app valida que o email pertence a `sensorpoint.pt` ou `sensorpoint.com`.
3. A app sincroniza `Aplicacao/Activas` via Graph para uma cache local do servidor.
4. O tecnico edita a folha em HTML.
5. `Guardar rascunho` cria/atualiza uma pasta de rascunho em `Activas`.
6. `Guardar e enviar` cria uma pasta final em `Arquivadas`.
7. Se o envio for feito a partir da folha original, a folha original desaparece das `Activas`.
8. Se o envio for feito a partir de um rascunho, apenas o rascunho desaparece das `Activas`; a folha original mantem-se visivel.

Referencias no codigo:

```text
src/web/application.py:303 - sync_graph_active_files()
src/web/application.py:399 - /api/files sincroniza antes de listar
src/web/application.py:521 - upload de rascunho para Graph
src/web/application.py:578 - arquivo local da folha final
src/web/application.py:598 - upload final para Graph
src/web/application.py:599 - remocao do item ativo original/rascunho
```

Graph storage:

```text
src/services/graph_storage_service.py:107 - sync_active_files()
src/services/graph_storage_service.py:155 - upload_archive_bundle()
src/services/graph_storage_service.py:173 - upload_active_bundle()
src/services/graph_storage_service.py:197 - remove_active_entry()
src/services/graph_storage_service.py:456 - remove cache local obsoleta
src/services/graph_storage_service.py:480 - repara cache local antes de sincronizar
src/services/graph_storage_service.py:600 - limpa marcadores de arquivo antigos
```

Login Microsoft:

```text
src/services/microsoft_auth_service.py:19 - scopes usados
src/services/microsoft_auth_service.py:45 - tenant id
src/services/microsoft_auth_service.py:46 - client id
src/services/microsoft_auth_service.py:47 - client secret
src/services/microsoft_auth_service.py:112 - validacao de dominio do email
src/services/microsoft_auth_service.py:205 - comparacao do dominio permitido
```

## Cache e self-healing

O servidor precisa de uma pasta persistente e gravavel para cache e dados operacionais.

Exemplo:

```text
/var/www/vhosts/<dominio>/private/folhas-servico/
```

Dentro dessa pasta, a app pode criar:

```text
graph-cache/
secret.key, se FS_SECRET_KEY nao estiver definido
```

Recomendado definir sempre `FS_SECRET_KEY`, para nao depender de ficheiro `secret.key`.

A cache e auto-corrigida durante a sincronizacao. A app tenta reparar:

- marcadores `.fs_archived` antigos;
- metadados `.graph.json` orfaos;
- pastas de rascunho locais que ja nao existem no SharePoint;
- ficheiros locais que foram removidos ou substituidos no SharePoint.

Referencias:

```text
src/web/application.py:98 - cabecalhos anti-cache para UI critica
src/web/application.py:111 - Cache-Control no-store/no-cache
src/services/graph_storage_service.py:456 - limpeza de cache obsoleta
src/services/graph_storage_service.py:480 - reparacao preventiva de cache
src/services/graph_storage_service.py:541 - remocao robusta de ficheiros locais
```

## Requisitos do servidor

Para cerca de 20 tecnicos:

```text
CPU: 2 vCPU minimo, 4 vCPU recomendado
RAM: 4 GB minimo, 8 GB recomendado
Disco: 20 GB minimo, SSD recomendado
Rede: acesso HTTPS outbound para Microsoft Graph e login.microsoftonline.com
Backup: diario da pasta de configuracao e logs
```

O servidor deve permitir:

- HTTPS no subdominio;
- processo Python persistente;
- escrita numa pasta privada fora do document root publico;
- acesso outbound a `https://graph.microsoft.com`;
- acesso outbound a `https://login.microsoftonline.com`;
- variaveis de ambiente para guardar secrets;
- logs de stdout/stderr da app.

## Checklist de deploy

1. Criar subdominio no Plesk: `service.sensorpoint.pt`.
2. Ativar certificado SSL/TLS.
3. Criar app Python no Plesk.
4. Apontar startup file para `passenger_wsgi.py`.
5. Apontar entry point para `application`.
6. Instalar dependencias com `requirements-server.txt`.
7. Criar pasta privada gravavel para `FS_APP_DATA_DIR` e `GRAPH_CACHE_DIR`.
8. Configurar variaveis de ambiente no Plesk.
9. Atualizar Redirect URI no Entra para o URL final HTTPS.
10. Confirmar permissoes Graph e admin consent.
11. Reiniciar a app Python no Plesk.
12. Entrar com conta Microsoft Sensorpoint.
13. Testar `Atualizar`/lista de folhas.
14. Testar `Guardar rascunho`.
15. Verificar rascunho em SharePoint `Activas`.
16. Testar `Guardar e enviar`.
17. Verificar final em SharePoint `Arquivadas`.

## Testes funcionais apos deploy

Teste 1 - Login:

```text
Abrir https://service.sensorpoint.pt/
Clicar login Microsoft
Entrar com email @sensorpoint.pt ou @sensorpoint.com
Confirmar que a app abre
```

Teste 2 - Sincronizacao:

```text
Colocar uma folha Excel em Aplicacao/Activas
Clicar Atualizar
Confirmar que aparece na app
```

Teste 3 - Rascunho:

```text
Abrir folha
Alterar um campo simples
Guardar rascunho
Confirmar pasta nova em Aplicacao/Activas
Confirmar que a folha original continua visivel
```

Teste 4 - Envio final:

```text
Abrir folha ou rascunho
Guardar e enviar
Confirmar pasta final em Aplicacao/Arquivadas
Confirmar remocao correta em Activas
```

Teste 5 - Cache:

```text
Apagar uma folha em SharePoint
Clicar Atualizar
Confirmar que a app deixa de listar a folha removida
Voltar a colocar a folha em Activas
Clicar Atualizar
Confirmar que reaparece
```

## Pontos a nao esquecer antes de producao

- Trocar o client secret antes de producao.
- Garantir que `FS_SECRET_KEY` esta definido e e longo/aleatorio.
- Garantir que o redirect URI no Entra usa HTTPS final.
- Confirmar que `MICROSOFT_AUTH_ALLOWED_DOMAINS` so contem dominios Sensorpoint.
- Confirmar que a pasta privada de cache nao e publica.
- Confirmar backups e logs.
- Confirmar que o servidor tem hora correta/NTP ativo.
- Confirmar que o subdominio usa HTTPS valido.
- Confirmar que a app nao esta a correr pelo servidor Flask local de `src/main.py`.

## Comandos uteis

Gerar `FS_SECRET_KEY`:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Instalar dependencias:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements-server.txt
```

Teste rapido de import WSGI:

```bash
python -c "from passenger_wsgi import application; print(application.name)"
```

Verificar configuracao Graph pela app, depois de login:

```text
https://service.sensorpoint.pt/api/graph/status
https://service.sensorpoint.pt/api/graph/test
https://service.sensorpoint.pt/api/graph/sync
```

Estas rotas exigem sessao autenticada.
