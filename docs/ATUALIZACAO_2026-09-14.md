# Atualização de 14 de setembro de 2026 — Folhas ativas

Branch: `codex/folhas-ativas-sharepoint-20260914`.
Base: `eaa9e6b80d263be851f1acd13b20730cb7abe256` (`main`).

## Âmbito e compatibilidade

Corrige a permanência de folhas na aplicação depois de o Excel ser retirado
manualmente da pasta de folhas ativas no SharePoint. A lista usa um inventário
confirmado, em vez de considerar todos os Excel existentes na cache local.

O `graph_storage_service.py` fornecido do servidor corresponde exatamente ao da
base acima. A correção foi adaptada e testada sobre essa base para evitar incluir
as alterações independentes da branch local de qualidade e segurança de 8 de
setembro. Os restantes ficheiros instalados no servidor não foram inventariados;
comparar eventuais personalizações antes de os substituir.

Não foram alterados email, Teams, filas de publicação, autenticação, regras de
gravação/finalização, assinaturas, PDF ou dependências. O envio real não foi
testado. A publicação desta branch no Git não instala código no servidor.

## Comportamento

- Só aparecem na lista as folhas da última sincronização completa com sucesso.
- Cópias antigas sem `.graph.json` também deixam de aparecer.
- As cópias locais são preservadas; esta atualização não faz uma limpeza física
  dos Excel antigos. O espaço ocupado por essas cópias mantém-se.
- Uma obra retirada não oculta os seus rascunhos que ainda existam no SharePoint.
- Um rascunho ainda não publicado fica guardado localmente e pode continuar aberto
  no editor, mas só entra na lista após publicação e confirmação no SharePoint.
- Remover apenas o Excel dentro de uma pasta de rascunho também o retira da lista.
- A página visível consulta a lista a cada 30 segundos. O formulário em edição
  e o filtro de pesquisa são mantidos; o botão Atualizar permite forçar a consulta.
- Uma falha de acesso ou paginação preserva o último inventário confirmado. Antes
  da primeira sincronização bem-sucedida, a lista pode estar temporariamente vazia.
- Esta alteração filtra a lista; não é um controlo de acesso que invalide ligações
  diretas a ficheiros locais já abertos ou guardados como favoritos.

O inventário fica em `Activas/.graph_active_files.json`, dentro da cache configurada.
É substituído de forma atómica e partilhado pelos processos web. Na configuração
Docker fornecida, o caminho é `/app/data/graph-cache/Excel/Activas`, salvo um
override de `FS_EXCEL_ROOT` já existente. Não editar o inventário manualmente.

## Ficheiros a instalar

Descarregar esta branch no GitHub. Copiar os oito ficheiros abaixo, mantendo os
caminhos relativos dentro de `app/src/`. Os dois módulos e o template novos são
necessários; substituir apenas `graph_storage_service.py` não é suficiente.

| Ficheiro | Estado |
|---|---|
| `src/services/active_file_index.py` | Novo |
| `src/services/file_mutex.py` | Novo nesta base |
| `src/services/file_service.py` | Alterado |
| `src/services/graph_storage_service.py` | Alterado |
| `src/web/application.py` | Alterado |
| `src/web/static/js/document-editor.js` | Alterado |
| `src/web/templates/field_app.html` | Alterado |
| `src/web/templates/partials/active_file_list.html` | Novo |

Os testes, `tools/test_runtime.py` e as patch notes acompanham o Git para validação;
não são necessários para executar a aplicação no servidor.

## Instalação no Docker/Plesk apresentado

O Compose mostrado pelo administrador contém `./src:/app/src` e o volume persistente
`app-data:/app/data`. Preservar esse Compose: o exemplo do repositório não contém
necessariamente os mesmos mounts, incluindo o mount personalizado dos logs.

1. Escolher um momento sem técnicos a gravar e confirmar o estado dos trabalhos
   pendentes. Parar a aplicação e qualquer worker antes de trocar ficheiros, para
   não executar versões diferentes ao mesmo tempo.
2. Guardar uma cópia identificada da pasta `app/src` atual e dos dados persistentes.
   Confirmar que a cópia contém os ficheiros esperados e pode ser lida/restaurada.
3. Instalar os oito ficheiros indicados. Preservar o `.env`, o Compose, os volumes,
   as credenciais e a configuração dos logs. Não criar uma nova pasta `app/src/src`.
4. Reiniciar o contentor e o worker, se existir, de acordo com a gestão atual do
   alojamento. Na pasta do projeto Compose existente, o operador pode usar:

   ```bash
   docker compose restart app
   ```

5. Abrir a aplicação, carregar em Atualizar e aguardar a conclusão da consulta.
   Confirmar a contagem com os Excel base e os rascunhos efetivamente existentes.

Com o mount `./src:/app/src` ativo e sem alterações de ambiente/dependências,
basta reiniciar: não é necessário reconstruir a imagem. Se a instalação efetiva
não usar esse mount, o operador terá de publicar uma imagem com o código novo.
Não executar `docker compose down -v` nem remover o volume para aplicar esta correção.

## Validação

Resultado nesta branch: **201 testes Python e 10 testes JavaScript passaram**.
Esta contagem corresponde à base do servidor, diferente da branch de desenvolvimento
local em que a correção foi inicialmente ensaiada.

A suite é executada com configuração fictícia criada antes de importar a app,
armazenamento temporário exclusivo, filas de teste e ligações de saída bloqueadas.
O `.env` operacional não é carregado. Não são enviados emails ou mensagens Teams.

Os testes específicos cobrem:

- 65 Excel locais, com 23 Excel base e 2 rascunhos remotos: 25 entradas visíveis,
  mantendo os 65 Excel no disco;
- remoção e reaparecimento, rascunhos locais e remoção do Excel dentro de uma pasta;
- persistência do inventário para outro processo, respostas inválidas e paginação;
- falhas de listagem/download sem substituir o último inventário;
- atualização da lista sem recarregar o formulário nem perder a pesquisa;
- ausência de confirmação de sucesso enquanto a atualização continua em curso.

Comandos usados para reproduzir a validação (a pasta temporária deve ser nova):

```bash
python -B -m pytest tests -q -p no:cacheprovider --basetemp CAMINHO_TEMPORARIO_NOVO
node --test --test-isolation=none tests/js/*.test.cjs
```

Não usar um caminho de dados existente como `--basetemp`: o pytest gere essa pasta
como descartável. Os testes simulados não comprovam o funcionamento em produção.

## Recuperação

Se for necessário regressar à versão anterior, parar a aplicação/worker, restaurar
os ficheiros de `src` da cópia anterior e reiniciar. Os módulos novos e o inventário
não são usados pela versão antiga. Não é necessário apagar Excel nem o volume.
Esse regresso restaura também o comportamento anterior da lista, incluindo a
possibilidade de reaparecerem as cópias antigas.

Se existirem novas gravações depois da instalação, não restaurar indiscriminadamente
uma cópia antiga da base de dados ou dos Excel: isso pode perder trabalho posterior.
