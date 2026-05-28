# Login Microsoft Entra

A app suporta login com contas Microsoft da empresa através de `FS_AUTH_PROVIDER=microsoft`.

## Domínios permitidos

Por defeito, a app só aceita e-mails com estes domínios:

```env
MICROSOFT_AUTH_ALLOWED_DOMAINS=sensorpoint.pt,sensorpoint.com
```

Mesmo que uma conta externa consiga autenticar no Microsoft, a app bloqueia o acesso se o e-mail não terminar num destes domínios.

## Redirect URI local

Na App Registration `Sensorpoint Folhas de Serviço`, abrir:

```text
Authentication
Add a platform
Web
```

Adicionar:

```text
http://127.0.0.1:5001/auth/microsoft/callback
```

Para produção, adicionar também o URL final:

```text
https://subdominio.sensorpoint.pt/auth/microsoft/callback
```

## Permissões necessárias

Para login apenas é suficiente:

```text
Microsoft Graph
User.Read
Tipo: Delegada
```

Esta permissão normalmente já existe por defeito.

## Variáveis

No `.env`:

```env
FS_AUTH_PROVIDER=microsoft
MICROSOFT_AUTH_TENANT_ID=...
MICROSOFT_AUTH_CLIENT_ID=...
MICROSOFT_AUTH_CLIENT_SECRET=...
MICROSOFT_AUTH_REDIRECT_URI=http://127.0.0.1:5001/auth/microsoft/callback
MICROSOFT_AUTH_ALLOWED_DOMAINS=sensorpoint.pt,sensorpoint.com
```

Se `MICROSOFT_AUTH_TENANT_ID`, `MICROSOFT_AUTH_CLIENT_ID` e `MICROSOFT_AUTH_CLIENT_SECRET`
não forem definidos, a app usa os valores `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID` e
`GRAPH_CLIENT_SECRET`.
