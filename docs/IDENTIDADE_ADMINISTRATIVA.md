# Administração operacional — identidade estável (M02)

## Comportamento

A administração operacional exige as três correspondências em conjunto:

1. Email Microsoft exato `jribeiro@sensorpoint.pt` (espaços exteriores e maiúsculas normalizados).
2. `tenant_id` da identidade autenticada igual a `FS_ADMIN_TENANT_ID`.
3. `id`/object ID da identidade autenticada igual a `FS_ADMIN_OBJECT_ID`.

Tenant/object ID são GUIDs em formato canónico, com diferenças de maiúsculas
aceites. O M01 obtém estes valores da resposta de login e confere a identidade
com `/me`; o tenant é guardado na sessão assinada. O privilégio é recalculado
em cada leitura de sessão e em cada verificação no serviço/rotas. O campo role
guardado no cookie não concede acesso. Não existe autorregisto, fallback por
email ou privilégio para o primeiro utilizador que entrar.

Outro object ID/tenant, Ana e outros técnicos não obtêm administração, mesmo
com role guardado como special_admin. Sessões antigas sem tenant não obtêm o
privilégio; João precisa de novo login para administrar. A falta de configuração
não impede o login normal nem o acesso SADI próprio de João/Ana. O allowlist
SADI continua em `src/services/maintenance_access.py`, sem depender de special_admin.

## Configuração pelo instalador

Configurar explicitamente no ambiente privado do web os GUIDs do tenant e do
objeto de utilizador de João no Microsoft Entra:

```text
FS_ADMIN_TENANT_ID=<GUID_DO_TENANT_DE_JOAO>
FS_ADMIN_OBJECT_ID=<GUID_DO_UTILIZADOR_JOAO_NESSE_TENANT>
```

O tenant deve ser o tenant organizacional usado em GRAPH_TENANT_ID. O object
ID é o do utilizador, não o client ID/object ID da aplicação nem um email.
Obter os identificadores no Entra e conferir a conta; não são necessárias
passwords, tokens, nova autenticação do CLI ou alteração de permissões Graph.
Não guardar os valores reais em Git, fixtures, revisão Claude ou documentação
pública. As variáveis são lidas ao carregar a configuração; o instalador aplica
o ambiente na instalação autorizada e confirma o novo login. Nenhum serviço foi
reiniciado ou configurado por esta implementação.

Variável ausente recusa privilégio com `admin_identity_not_configured`; GUID
inválido usa `admin_identity_config_invalid`. O arranque regista o evento seguro
`admin_identity_configuration`. As rotas administrativas devolvem 403 e um
diagnóstico que pede ao instalador conferir as duas variáveis, sem expor valores.
Uma identidade recusada com configuração válida usa `admin_access_denied`.
O botão administrativo só aparece quando a identidade completa é autorizada;
`/admin` e `/api/admin/overview` permitem conferir o diagnóstico de recusa.

Conferir os GUIDs, fazer novo login de João e verificar o acesso ao painel.
Confirmar que Ana continua com SADI e sem painel administrativo. Não alterar
cookies, repetir envios incertos ou libertar reservas reais como forma de teste.
As proteções CSRF, estado incerto, escrita em curso e auditoria mantêm os seus
contratos e são verificadas pelas regressões sintéticas.

## Evidência e limites

`tests/test_admin_identity.py` cobre configuração ausente/inválida, identidade
exata/divergente, role forjado, sessão legada, SADI próprio e diagnósticos sem
GUID/email. `tests/test_microsoft_auth.py` comprova o cálculo de role no login e
na retoma de sessão; `tests/test_operational_admin.py` usa configuração/GUIDs
inventados e mantém as regressões CSRF/revogação/fila/auditoria.

Em 12/10/2026 passaram **231 testes, 1 ignorado** (permissões POSIX SADI no
Windows), 68,59s. Comando:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -B -m pytest -p no:cacheprovider tests/test_admin_identity.py tests/test_microsoft_auth.py tests/test_operational_admin.py tests/test_config.py tests/test_hosting_readiness.py tests/test_maintenance_access.py tests/test_archive_and_web.py tests/test_work_folder_web.py tests/test_logging_config.py -q
```

Log local `qa-output/consolidacao-20261007/p2-m02-regression-r1.txt` na raiz Migração.
Os ensaios usam `tests/conftest.py`, runtime temporário antes de importar a app
e saída de rede bloqueada. Resultados, commit e parecer/check são registados na
passagem P2 e no ESTADO/REGISTO. Não houve configuração real, login real, envio
ou deploy. A aprovação M01 não é aprovação automática desta mudança M02.
