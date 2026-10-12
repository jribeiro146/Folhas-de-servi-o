# Identidade Microsoft — M01 (12/10/2026)

## Contrato e impacto

O callback recebe apenas um código de autorização e confere state/nonce da
sessão. O servidor troca o código diretamente no endpoint v2 Microsoft por TLS,
usando a configuração Graph existente. Só cria uma nova sessão depois de:

- obter ID token e access token não vazio, numa resposta JSON objeto;
- conferir `iss` exato `https://login.microsoftonline.com/<tenant>/v2.0`,
  `aud` GUID igual a GRAPH_CLIENT_ID, tenant igual a GRAPH_TENANT_ID e nonce não vazio
  igual ao guardado para esse login;
- conferir exp numérico finito estritamente posterior ao relógio do servidor;
  se presentes, nbf e iat devem ser numéricos finitos, com tolerância máxima de
  60 segundos de avanço perante o relógio local; booleanos/strings não são
  timestamps; exp não recebe tolerância;
- exigir tenant e oid em formato GUID canónico (maiúsculas são normalizadas);
- obter perfil Graph `/me` com o access token delegado e conferir `/me.id = oid`;
- obter mail ou userPrincipalName do próprio perfil, num domínio autorizado.

Não há fallback para identidade/email do ID token quando falta o perfil. O nome
de apresentação pode usar a claim name porque não concede acesso. Respostas
malformadas e inconsistentes produzem MicrosoftAuthError e não criam sessão.
O GUID normalizado continua em `id`; `tenant_id` é agora transportado no objeto
MicrosoftAuthUser e na sessão. Não se guarda ID token nem access token na sessão.
Audiência, tenant e oid são comparados como GUIDs normalizados, tolerando apenas
diferenças de maiúsculas. Falhas temporais registam no evento auth_callback_failed
o código `identity_claim_time_invalid`, sem timestamps, claims, nonce ou tokens.

Contas válidas mantêm o percurso normal. Respostas que antes eram aceites apenas
com claims/fallback deixam de entrar; é necessário iniciar sessão novamente.
GRAPH_TENANT_ID tem de ser o GUID do tenant, não common/organizations/alias de
domínio. O relógio do servidor deve estar sincronizado; tolera-se um pequeno
desvio em iat/nbf para não recusar respostas novas por latência/subsegundos.
A expiração verificada é
da resposta de login; a duração da sessão Flask permanece a configurada.

## Decisão e limites

Esta alteração valida a coerência das claims no percurso confidencial já
existente, com troca TLS e prova delegada `/me`. Não adiciona verificação de
assinatura/JWKS nem aceita JWTs fornecidos pelo navegador. Não chamar o decoder
ou o validador de claims para autenticar bearer tokens da aplicação. Se houver
esse novo requisito, usar uma biblioteca OIDC/JWT adequada e preparar ensaios
de chaves, assinatura, rotação e indisponibilidade; não implementar criptografia
própria. Não há demonstração de falsificação externa nesta base.

A Microsoft recomenda bibliotecas e validação de assinatura para validar ID
tokens: [ID tokens](https://learn.microsoft.com/en-us/entra/identity-platform/id-tokens).
Os significados de aud, iss, exp, nonce, tid e oid constam da
[referência de claims](https://learn.microsoft.com/en-us/entra/identity-platform/id-token-claims-reference).
As referências foram consultadas em 12/10/2026. A verificação de assinatura é
uma opção separada no âmbito da passagem P2, não um ensaio executado aqui.

O M02 complementa este contrato com a configuração de tenant/oid do administrador,
documentada em [IDENTIDADE_ADMINISTRATIVA.md](IDENTIDADE_ADMINISTRATIVA.md).
Sessões antigas sem tenant são reconstruídas com tenant vazio e não obtêm o
privilégio elevado, preservando o acesso próprio SADI. M09 ainda tem de sanitizar erros
HTTP/Graph/callback preexistentes. Não interpretar M01 como fecho desses pontos.

## Validação sintética

`tests/test_microsoft_auth.py` cobre login válido, claims ausentes/inválidas,
expiração e tipos, nonce ausente, formato JWT/JSON inválido, access token ausente,
perfil ausente/divergente, falha Graph, email/domínio e criação de sessão pelo
callback. As respostas e os identificadores são inventados. Os JWTs deliberadamente
não possuem assinatura válida: não são prova de validação criptográfica.
`tests/conftest.py` configura armazenamento exclusivo fora do OneDrive antes de
importar a aplicação e bloqueia rede; não se usam credenciais nem filas reais.

Primeiro grupo dirigido: **124 aprovados**, 9,13 s, em 12/10/2026:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -B -m pytest -p no:cacheprovider tests/test_microsoft_auth.py tests/test_operational_admin.py tests/test_config.py tests/test_hosting_readiness.py -q
```

Log local: `qa-output/consolidacao-20261007/p2-m01-tests-r1-authorized.txt` na raiz
Migração. A tentativa inicial falhou antes da recolha por acesso da sandbox à
pasta temporária; o log `p2-m01-tests-r1.txt` foi preservado. A execução autorizada
manteve o isolamento. Regressão alargada: **188 aprovados, 1 ignorado**, 42,76 s,
acrescentando `tests/test_maintenance_access.py`, `tests/test_archive_and_web.py`
e `tests/test_work_folder_web.py` ao comando anterior. O único skip é o caso
de permissões POSIX SADI em Windows. Log `p2-m01-regression-r1.txt` na mesma
pasta. Não foi realizado login Microsoft real nem ensaio remoto do código M01.
O parecer Claude é registado no REGISTO/ESTADO e na passagem P2 após execução;
esta nota não afirma aprovação.

Após a primeira revisão, corrigiu-se a recusa por pequeno desvio de relógio.
A margem de 60s é limitada a iat/nbf e não prolonga a validade exp. Acrescentou-se
normalização do GUID de audiência e diagnóstico temporal sem dados privados.
Regressão da segunda ronda: **212 aprovados, 1 ignorado**, 45,44s, com os mesmos
grupos e ainda `tests/test_file_diagnostics.py`/`tests/test_logging_config.py`.
Log `p2-m01-regression-r2.txt`. Inclui fronteiras de 2s/60s aceites, 61s recusados,
expiração recusada e privacidade do diagnóstico no callback. Nenhum transporte
Microsoft real foi usado. A primeira versão e respetiva evidência permanecem no
histórico; a aprovação válida deve corresponder à versão corrigida.
