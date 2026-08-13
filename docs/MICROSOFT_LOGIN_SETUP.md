# Login Microsoft Entra

A app suporta login com contas Microsoft da empresa atravÃ©s de `FS_AUTH_PROVIDER=microsoft`.

## DomÃ­nios permitidos

Por defeito, a app sÃ³ aceita e-mails com estes domÃ­nios:

```env
MICROSOFT_AUTH_ALLOWED_DOMAINS=sensorpoint.pt,sensorpoint.com
```

Mesmo que uma conta externa consiga autenticar no Microsoft, a app bloqueia o acesso se o e-mail nÃ£o terminar num destes domÃ­nios.

## Redirect URI local

Na App Registration `Sensorpoint Folhas de ServiÃ§o`, abrir:

```text
Authentication
Add a platform
Web
```

Adicionar:

```text
http://localhost:5001/auth/microsoft/callback
```

Para produÃ§Ã£o, adicionar tambÃ©m o URL final:

```text
https://subdominio.sensorpoint.pt/auth/microsoft/callback
```

## PermissÃµes necessÃ¡rias

Para login apenas Ã© suficiente:

```text
Microsoft Graph
User.Read
Tipo: Delegada
```

Esta permissÃ£o normalmente jÃ¡ existe por defeito.

## VariÃ¡veis

No `.env`:

```env
FS_AUTH_PROVIDER=microsoft
GRAPH_TENANT_ID=...
GRAPH_CLIENT_ID=...
GRAPH_CLIENT_SECRET=...
MICROSOFT_AUTH_REDIRECT_URI=http://localhost:5001/auth/microsoft/callback
MICROSOFT_AUTH_ALLOWED_DOMAINS=sensorpoint.pt,sensorpoint.com
```

Estas mesmas credenciais `GRAPH_*` são usadas pelo login, SharePoint e envio de email.
As antigas variáveis de credenciais `MICROSOFT_AUTH_*` são ignoradas; apenas o redirect
URI e os domínios autorizados mantêm o prefixo `MICROSOFT_AUTH_`.
