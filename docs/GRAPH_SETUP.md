# IntegraÃ§Ã£o Microsoft Graph

Esta aplicaÃ§Ã£o pode trabalhar em dois modos:

- `local`: lÃª e grava nas pastas locais do projeto.
- `graph`: descarrega os Excels de uma pasta SharePoint/OneDrive `Activas` para cache local e envia os finalizados para `Arquivadas`.

O Graph sÃ³ Ã© usado pelo servidor Flask. O segredo nunca deve ir para o browser.

## 1. Criar a App Registration

1. Abrir Microsoft Entra admin center.
2. Entrar em `Applications` > `App registrations` > `New registration`.
3. Nome sugerido: `Sensorpoint Folhas de ServiÃ§o`.
4. Supported account types: apenas a organizaÃ§Ã£o Sensorpoint.
5. Guardar estes valores:
   - `Application (client) ID`
   - `Directory (tenant) ID`

## 2. Criar o segredo

1. Na App Registration, abrir `Certificates & secrets`.
2. Criar um `Client secret`.
3. Guardar imediatamente o `Value`, nÃ£o apenas o `Secret ID`.

## 3. PermissÃµes Microsoft Graph

1. Na App Registration, abrir `API permissions`.
2. Adicionar `Microsoft Graph`.
3. Escolher `Application permissions`.
4. Adicionar `Sites.Selected`.
5. Clicar em `Grant admin consent`.

`Sites.Selected` Ã© intencional: permite limitar a aplicaÃ§Ã£o apenas ao site/document library das folhas.

## 4. Criar estrutura no SharePoint

Na biblioteca/document library escolhida, criar:

```text
Activas/
Arquivadas/
```

Para o primeiro teste, coloca 1 ou 2 ficheiros `.xlsx` vÃ¡lidos em `Activas`.

## 5. Obter siteId e driveId

Com Microsoft Graph Explorer, PowerShell ou outra ferramenta Graph:

```http
GET https://graph.microsoft.com/v1.0/sites/{tenant}.sharepoint.com:/sites/{nome-do-site}
```

Guardar o campo `id` como `GRAPH_SITE_ID`.

Depois listar as bibliotecas/document drives:

```http
GET https://graph.microsoft.com/v1.0/sites/{GRAPH_SITE_ID}/drives
```

Guardar o `id` da biblioteca correta como `GRAPH_DRIVE_ID`.

## 6. Dar acesso da app ao site

Com uma conta/admin com permissÃµes suficientes, executar:

```http
POST https://graph.microsoft.com/v1.0/sites/{GRAPH_SITE_ID}/permissions
Content-Type: application/json

{
  "roles": ["write"],
  "grantedToIdentities": [
    {
      "application": {
        "id": "{GRAPH_CLIENT_ID}",
        "displayName": "Sensorpoint Folhas de ServiÃ§o"
      }
    }
  ]
}
```

## 7. Configurar a app local

Copiar `.env.example` para `.env` e preencher:

```env
FS_STORAGE_BACKEND=graph
GRAPH_TENANT_ID=
GRAPH_CLIENT_ID=
GRAPH_CLIENT_SECRET=
GRAPH_SITE_ID=
GRAPH_DRIVE_ID=
GRAPH_ACTIVE_PATH=Activas
GRAPH_ARCHIVE_PATH=Arquivadas
GRAPH_CACHE_DIR=C:\Sensorpoint\FolhasServico\graph-cache
```

Para produÃ§Ã£o, `GRAPH_CACHE_DIR` deve ficar fora do OneDrive.

## 8. Testar na app local

Arrancar a app e abrir:

```text
http://localhost:5001/api/graph/status
http://localhost:5001/api/graph/test
```

Se o teste estiver OK, abrir:

```text
http://localhost:5001/
```

A app deve descarregar os Excels de `Activas` para a cache local. Quando fizeres `Guardar e enviar`, a pasta final da folha Ã© enviada para `Arquivadas/{nome-da-folha}/`.

## Dados que preciso para ligar aqui

NÃ£o coloques o `GRAPH_CLIENT_SECRET` em mensagens se puderes evitar. O ideal Ã© colocares diretamente no `.env`.

Valores necessÃ¡rios:

```text
GRAPH_TENANT_ID
GRAPH_CLIENT_ID
GRAPH_CLIENT_SECRET
GRAPH_SITE_ID
GRAPH_DRIVE_ID
GRAPH_ACTIVE_PATH
GRAPH_ARCHIVE_PATH
GRAPH_CACHE_DIR
```
