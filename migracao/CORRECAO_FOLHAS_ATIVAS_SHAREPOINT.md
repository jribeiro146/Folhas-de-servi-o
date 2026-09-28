# Correção da lista de folhas ativas do SharePoint

Data: 28/09/2026  
Estado: diagnóstico e instruções para implementação. Este documento não altera a aplicação nem confirma uma correção instalada no servidor.

## 1. Objetivo e regra funcional

O SharePoint é a fonte da lista de folhas ativas. Quando a administrativa retira um Excel base da pasta `Activas`, esse Excel deve desaparecer da lista da aplicação após uma atualização bem-sucedida do inventário remoto.

Uma cópia antiga na cache do servidor não deve fazer uma folha aparecer como `Pronta`. A existência de dados locais e a autorização para apresentar uma folha na lista são decisões distintas.

Os rascunhos são entradas independentes: uma pasta de rascunho que continua no SharePoint pode continuar visível como `Em execução`, mesmo que o Excel base tenha sido retirado. Não apagar os rascunhos, os anexos ou os trabalhos pendentes para resolver a listagem.

Na imagem fornecida em 28/09/2026 existem **19 Excel base e 2 pastas de rascunhos**. Esperam-se 19 folhas base como `Pronta`; as duas pastas só correspondem a duas entradas adicionais se contiverem folhas de rascunho reconhecidas pela aplicação. Estes números são uma referência daquele momento, nunca valores a fixar no código.

## 2. Factos confirmados e limites do diagnóstico

### Problema anterior

- O inventário antigo tinha 63 Excel base na cache: 23 com metadados Graph e 40 sem esses metadados, além de 2 folhas de rascunho.
- A limpeza antiga dependia dos ficheiros auxiliares `.graph.json`. A ordem de processamento podia retirar os metadados antes de verificar o Excel, deixando o Excel antigo permanentemente na cache.
- A correção preparada passou a usar `.graph_active_files.json`, um inventário remoto persistido, para filtrar a lista sem depender de apagar as cópias locais.

### Recorrência atual

- O utilizador relata que folhas base já retiradas voltaram a aparecer como `Pronta`.
- Foram analisados `09.zip` e `app logs (1).zip`. Contêm logs HTTP do alojamento, não o log interno `folhas-servico.jsonl`.
- Em 25/09, os registos mostram 143 respostas HTTP 200 a `/api/files`, 140 a `/api/files?refresh=0`, 86 a `/api/graph/status` e 4 a `/api/files?refresh=1`.
- Em 26/09 e 28/09 surge a versão de recursos `20260926113932`, incluindo `document-validation.js`. Nesses dias não aparecem os pedidos regulares anteriores; em 28/09 aparece um pedido manual a `/api/files?refresh=1`.
- Esta mudança é um **indício de regressão ou desativação da atualização automática**, não prova de qual ficheiro ou alteração causou o problema. Pode depender do JavaScript, do sinalizador enviado pelo servidor ou de outro erro de execução.
- HTTP 200 no pedido de atualização não prova que a tarefa de sincronização terminou com sucesso.
- Os ZIP não permitem saber quais os ficheiros devolvidos pelo Graph, o conteúdo do inventário local ou a versão Python efetivamente executada no servidor.

**Não atribuir a causa aos campos obrigatórios sem comparar os ficheiros instalados.** O carregamento do validador apenas ajuda a localizar temporalmente uma atualização.

## 3. Base de trabalho e controlo de versões

A pasta principal contém alterações de outras funcionalidades. Não a copiar integralmente para produção e não substituir essas alterações por uma versão antiga para recuperar esta correção.

Referências locais disponíveis para comparação:

- Commit `825929f23ec12876ffb4ece6c199709336860c95`: correção anterior da listagem SharePoint; foi publicado na branch `codex/folhas-ativas-sharepoint-20260914` na sessão anterior. Confirmar o estado remoto atual antes de preparar nova entrega.
- Commit `93be27fdb52b488e6bccf6983af3f2e6df0d7510`: acrescenta campos obrigatórios sobre essa correção. Existe localmente; não há confirmação nesta análise de que tenha sido publicado.
- Worktree de referência: `.codex_tmp/sharepoint-release`. Serve para comparação; não é prova da versão instalada.

Antes de implementar, obter a versão efetiva do servidor, criar uma base de trabalho identificada e aplicar apenas as diferenças necessárias. Preservar os campos obrigatórios e as restantes funcionalidades em utilização. Não fazer um cherry-pick ou uma substituição integral de ficheiros sem rever os conflitos e as diferenças.

## 4. Evidências a recolher do servidor

Recolher, sem alterar dados operacionais:

1. Os ficheiros em execução indicados na tabela da secção seguinte, especialmente `file_service.py`, `graph_storage_service.py`, `application.py`, `document-editor.js` e `field_app.html`.
2. O log atualizado após uma tentativa de `Atualizar`: no Plesk, `Home directory → private → folhas-servico → logs → folhas-servico.jsonl`.
3. O conteúdo e a data de modificação de `.graph_active_files.json`, no diretório de folhas ativas usado pelo processo.
4. Backend e caminhos efetivos de armazenamento e cache, sem exportar segredos do `.env`; confirmar também a pasta SharePoint consultada.
5. O número de pelo menos uma folha indevidamente apresentada, para cruzar a interface, o inventário persistido e a listagem remota.

No Docker Compose anteriormente fornecido, o volume `app-data` estava montado em `/app/data`, e `GRAPH_CACHE_DIR` era `/app/data/graph-cache`. O caminho esperado do inventário era `/app/data/graph-cache/Excel/Activas/.graph_active_files.json`, **sujeito à configuração efetiva**, incluindo `FS_EXCEL_ROOT`. O gestor de ficheiros do Plesk não expõe necessariamente esse volume.

Interpretação das evidências:

| Resultado | Verificação necessária |
| --- | --- |
| A folha não consta do inventário, mas aparece na aplicação | Filtro de listagem, processo com código antigo, diretório diferente ou resposta antiga da interface. |
| A folha consta de um inventário antigo e os refreshes falham | Falha de atualização e apresentação indevida da lista como atual. |
| A folha consta de um inventário recém-atualizado | Confirmar o que o Graph devolveu e se consulta a pasta correta. |
| Não há inventário, mas a cache inteira aparece | Filtro ausente ou backend Graph a permitir listagem sem inventário. |

## 5. Alterações a garantir na implementação

Algumas já existem na correção de referência. Restaurar o que estiver ausente e corrigir as limitações verificadas; não duplicar mecanismos.

| Ficheiro ou componente | Comportamento necessário |
| --- | --- |
| `src/services/graph_storage_service.py` | Ler todas as páginas da pasta ativa e das pastas de rascunho relevantes. Construir um inventário completo de caminhos relativos e publicá-lo atomicamente, com exclusão mútua entre processos. |
| `src/services/active_file_index.py` | Centralizar a leitura e validação do inventário. Em modo Graph, inventário ausente ou inválido nunca deve significar listar toda a cache. |
| `src/services/file_service.py` | Filtrar os Excel base e os Excel dentro de rascunhos pelo inventário em cada listagem. A cache de resumos não pode reintroduzir entradas excluídas. Preservar o comportamento do backend local. |
| `src/services/file_mutex.py` ou bloqueio equivalente | Impedir refreshes concorrentes de publicarem inventários fora de ordem. Reutilizar um mecanismo compatível com a base escolhida. |
| `src/services/graph_sync_coordinator.py` | Distinguir atualização em curso, sucesso e erro. Se houver vários processos web, o estado consultado deve identificar a mesma atualização que foi pedida. |
| `src/web/application.py` | Usar a listagem filtrada na página inicial e na API. Enviar corretamente `graph_enabled`; manter os contratos de atualização e devolver a lista após a atualização correspondente. |
| `src/web/templates/field_app.html` | Passar `graphEnabled` ao JavaScript e usar a mesma origem de dados para os cartões e a contagem. |
| `src/web/templates/partials/active_file_list.html` | Centralizar a renderização dos cartões para que a página inicial e a atualização parcial apliquem o mesmo filtro. |
| `src/web/static/js/document-editor.js` | Atualizar ao abrir, periodicamente enquanto a página está visível e ao regressar à página. O botão manual deve aguardar o resultado real, atualizar cartões e contagem, preservando pesquisa e edição em curso. |
| `src/web/static/service-worker.js` e versão dos recursos | Garantir que os recursos novos chegam aos clientes e que respostas de listagem/API não são servidas a partir de uma cache antiga. |

### Separar inventário remoto de downloads

Na implementação de referência, o inventário só é publicado depois de todos os downloads terminarem. Assim, a falha num único download pode impedir a retirada da lista de uma folha cuja ausência já foi confirmada.

Melhoria a implementar e testar: depois de concluir e validar **todas as listagens necessárias**, publicar o inventário remoto independentemente do sucesso dos downloads. Uma falha na descarga de uma folha existente não deve manter visíveis outras folhas que já desapareceram do SharePoint. A disponibilidade do conteúdo deve ter um estado separado; uma folha nova ainda sem conteúdo local não deve ser apresentada como pronta para utilização.

Se alguma página da listagem falhar, não publicar um inventário parcial como se fosse completo. Uma listagem completa e vazia, porém, é válida e deve retirar todas as entradas remotas da lista ativa.

### Tornar o estado da atualização explícito

Guardar ou disponibilizar data, identificador e resultado da última atualização confirmada. Uma falha deve ser visível; não mostrar “atualizado” apenas porque a API respondeu HTTP 200.

Durante indisponibilidade do SharePoint não é possível confirmar a lista atual. Se a aplicação mantiver a última lista conhecida, deve identificá-la como desatualizada, com data e aviso. Nunca reconstruir a lista a partir de todos os Excel da cache.

O estado atual do coordenador de referência vive em memória por processo. Com vários processos, consultar outro processo pode aparentar que a tarefa acabou antes de terminar. Correlacionar pedido e conclusão por um identificador partilhado, ou adotar outra solução equivalente e testada.

## 6. Preservação de dados e âmbito

- A correção é de visibilidade e sincronização do inventário. Não exige apagar toda a cache ou o volume Docker.
- Não apagar rascunhos, anexos, assinaturas, filas ou dados pendentes para fazer a contagem coincidir.
- Não reenviar ficheiros antigos para o SharePoint para resolver divergências de listagem.
- Preservar validações de campos obrigatórios, gravação de rascunhos, finalização, PDF, autenticação e integrações existentes.
- Não alterar credenciais, permissões Graph ou `.env` operacional para facilitar testes.
- O filtro da lista não equivale a bloquear acesso direto a uma folha por URL. Se for necessário alterar esse acesso, tratar como regra própria, preservando documentos já abertos e trabalho pendente.

## 7. Testes de aceitação

Executar com dados sintéticos, diretórios temporários fora do OneDrive e Graph simulado. Cumprir o `AGENTS.md`: bloquear email e Teams, não iniciar workers operacionais nem consumir filas reais.

1. Cache com 65 ficheiros e inventário remoto menor: aparecem apenas as entradas confirmadas; as restantes cópias locais não influenciam a contagem.
2. Retirar uma base do Graph simulado: desaparece após refresh, mesmo mantendo Excel e `.graph.json` locais.
3. A folha removida continua ausente após reiniciar a aplicação, abrir outra sessão e efetuar novos refreshes.
4. Base retirada e rascunho ainda remoto: a base desaparece e o rascunho mantém-se. Rascunho retirado: também deixa a lista, sem apagar trabalho local.
5. Inventário ausente, corrompido ou ilegível em modo Graph: nenhuma recuperação automática da lista completa da cache; estado de indisponibilidade identificável.
6. Listagem com várias páginas: todas são consideradas. Falha numa página: não publicar resultado parcial. Listagem completa vazia: nenhuma folha ativa.
7. Inventário completo confirma uma remoção, mas outro Excel falha no download: a folha removida não continua visível por causa dessa falha.
8. Refreshes concorrentes e consultas através de processos diferentes: não publicar dados fora de ordem nem indicar conclusão de outra tarefa.
9. Página inicial, atualização automática e botão manual apresentam o mesmo conjunto e contagem. Preservam pesquisa e alterações não gravadas no documento aberto.
10. Erro ou timeout: a interface não apresenta mensagem de sucesso nem esconde o facto de os dados estarem desatualizados.
11. Nova versão dos recursos carregada por clientes existentes, incluindo PWA; ausência de erros JavaScript que interrompam a atualização.
12. Backend local, campos obrigatórios e gravação de rascunhos continuam a funcionar. Validar efeitos de finalização com mocks, sem comunicações ou alterações operacionais.

Testes existentes de referência: `tests/test_active_file_index.py` e `tests/js/active-file-list.test.cjs`. Rever a configuração de isolamento antes de executar e acrescentar os casos ainda não cobertos. Nenhum destes testes foi executado para criar este documento.

## 8. Entrega e instalação

1. Preparar uma alteração isolada sobre a versão confirmada do servidor, com revisão do diff e testes proporcionais. Não incluir trabalho incidental da pasta principal.
2. Entregar commit identificado, patchnotes, lista exata de ficheiros alterados e procedimento de recuperação. Distinguir claramente código local, código publicado no Git e código instalado.
3. Antes da instalação autorizada, guardar uma cópia verificável dos ficheiros substituídos. Se houver alteração de formato de dados, preparar também a recuperação desses dados.
4. Confirmar o Docker Compose efetivo. No anteriormente fornecido, `./src:/app/src` estava montado diretamente: alterações apenas em `src`, sem dependências novas, exigem normalmente reiniciar o serviço para carregar Python, sem reconstruir a imagem. Se esse volume já não existir, seguir a instalação real e reconstruir quando necessário.
5. Após instalação autorizada, confirmar a versão carregada pelo processo e pelo navegador; verificar um refresh completo e cruzar o inventário com o SharePoint atual, sem apagar folhas reais para testar.
6. Observar várias atualizações e uma nova sessão. Confirmar que as folhas retiradas não reaparecem e que os rascunhos válidos continuam acessíveis.

**Condição de conclusão:** após um inventário remoto completo e bem-sucedido, nenhum Excel base ausente desse inventário aparece como folha ativa, independentemente das cópias antigas existentes no servidor. Falhas de atualização ficam explícitas e não são apresentadas como confirmação da lista atual.
