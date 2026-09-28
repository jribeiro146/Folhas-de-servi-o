# Patch Notes

Registo das principais alteracoes por versao da aplicacao Folhas de Servico.

As versoes sem tag formal usam o commit Git como referencia. A versao marcada para servidor continua a ser `v1.0.0-servidor`, salvo indicacao posterior.

## 2026-09-28 — Pacote de migração para revisão e integração

- Adicionado o pacote revisto em `migracao/App_Folhas_Checklists/`, preservando o código da aplicação existente na raiz.
- O pacote melhora o inventário/refresco SharePoint, a interface móvel, as pendências e assinaturas SADI de demonstração, a impressão dos 12 materiais, a paginação PDF, a PWA e a preparação Docker/worker.
- Validação local da implementação: 396 testes Python, 47 JavaScript, PDFs reais e ensaios de interface; revisão independente concluída sem achados P1/P2 abertos no âmbito revisto.
- O manifesto SHA-256 acompanha o pacote. A [passagem ao implementador Plesk](migracao/ENTREGA_PLESK_2026-09-28.md) identifica limitações, configuração a reconfirmar, teste isolado da imagem e recuperação. Esta entrega não altera a versão recomendada para servidor, não torna SADI operacional e não executa deploy.

## Em desenvolvimento - confirmação e segurança do envio de e-mail

- Foi adicionado logging operacional JSON Lines para web, servidor local e worker,
  com timestamps UTC, níveis, eventos estruturados, stack traces e correlação por
  `X-Request-ID`.
- O log fica por defeito em `FS_APP_DATA_DIR/logs`, usa rotação multiprocesso por
  tamanho, mantém dez cópias comprimidas e é espelhado em stderr.
- Query strings, payloads, cookies, autorização, assinaturas, fotografias e
  destinatários não entram nos eventos; campos e padrões sensíveis recebem redação.
- A localização, permissões, configuração, consulta, rotação e checklist de deploy
  estão documentadas em `docs/LOGGING.md`.
- A validação inclui 189 testes Python, 6 testes JavaScript e um ensaio de arranque
  WSGI que confirmou o ficheiro, os eventos JSON e o `X-Request-ID`.
- A sessão de edição é retomada automaticamente quando o telemóvel ou a PWA regressa de suspensão; a interface deixa de enviar pedidos sem `lease_token` e já não apresenta ao utilizador o erro interno de metadados.
- Os botões de guardar, finalizar e cancelar começam bloqueados até a sessão estar pronta, e a cache PWA foi renovada para distribuir a correção aos dispositivos instalados.
- O Plesk/Passenger pode deixar o trabalho pesado fora dos processos WSGI: `FS_GRAPH_QUEUE_IN_WEB=false` e `python -m src.queue_worker` processam a mesma fila SQLite persistente numa tarefa independente.
- SharePoint, conversão PDF, login e email passam a usar obrigatoriamente a única credencial `GRAPH_*`; overrides antigos `GRAPH_MAIL_*` e credenciais `MICROSOFT_AUTH_*` deixam de ser lidos, eliminando a possibilidade de o email usar um secret local ou expirado diferente do servidor.
- O tempo de reconciliação recomendado para emails em produção passou para 900 segundos, evitando marcar como parado um arquivo grande ainda em conversão ou upload.
- Em Graph/Plesk, o PDF passa a ser convertido exclusivamente a partir do HTML final pelo Microsoft Graph; foi removida a conversão do `.xlsx`. O modo local mantém Chrome/Edge.
- O mesmo PDF validado e publicado em `Arquivadas` e o ficheiro anexado ao email.
- A interface aguarda pela aceitação do e-mail pelo Microsoft Graph em vez de tratar um trabalho pendente como sucesso.
- Os erros finais da criação do PDF, autenticação, permissões e Graph ficam visíveis ao utilizador.
- Falhas temporárias respeitam `Retry-After` e têm um máximo de três tentativas por defeito.
- Trabalhos que esgotem as tentativas ou tenham uma falha permanente ficam parados; só podem ser repetidos individualmente e após confirmação explícita.
- Trabalhos de e-mail criados por versões anteriores ficam retidos para revisão na primeira atualização, evitando envios acumulados inesperados.
- Foi adicionado um diagnóstico de credenciais e da permissão `Mail.Send` que não envia mensagens.
- O e-mail do cliente é opcional: sem endereço, a folha é arquivada sem consultar o Graph, sem criar um envio e sem publicar o aviso de envio no Teams.
- A conversão local para PDF deixa de prender a fila em pipes de subprocessos Chromium; toda a árvore headless fica isolada e é terminada em timeout, interrupção ou reinício do servidor.
- O Chromium produz o PDF numa pasta temporária local e só publica o ficheiro validado no arquivo local/SharePoint, reduzindo bloqueios durante a impressão.
- A fila regista as fases `generating_pdf`, `pdf_ready`, `sending_mail` e `mail_accepted`; um trabalho sem atividade deixa de ser repetido automaticamente e fica disponível apenas para repetição manual.
- A interface acompanha o envio por mais tempo do que o limiar de inatividade configurado, permitindo apresentar a repetição manual em vez de terminar cedo com um erro genérico.
- Secrets temporários continuam exclusivamente no `.env` local ignorado pelo Git.

## v1.3.0 - Fotografias, entrega digital e número de obra

Data: 2026-08-07
Commit: incluído nesta versão

### Adicionado

- Área de fotografias entre os técnicos e o fecho, adaptada a telemóvel, tablet e desktop.
- Suporte para JPEG, PNG, WebP, HEIC/HEIF, AVIF, GIF, BMP, TIFF e DNG, com seleção múltipla e remoção antes do fecho.
- Leitura do campo `N.º de obra` na folha `LINK`, descobrindo a coluna pelo cabeçalho da linha 2 e lendo o valor da linha 3.
- Botão do número de obra entre `Local / loja` e `Contrato`, com abertura da pasta correspondente no SharePoint num novo separador.
- Resolvedor independente do backend de armazenamento para procurar a pasta em `07-Obras/Obras a realizar`, com cache, paginação Graph e correspondência exata pelo prefixo de quatro dígitos.
- Endpoint autenticado `GET /work-folder/<numero>` para validar o código e redirecionar apenas para URLs HTTPS de `sensorpointpt.sharepoint.com`.
- Envio da folha final em PDF por Microsoft Graph, com o cliente em Para e o técnico autenticado em CC.
- Conversão do HTML final para PDF: Microsoft Graph no servidor/Plesk e Chrome ou Edge apenas no armazenamento local.
- Notificação opcional no Teams por Workflow webhook, executada depois de o Graph aceitar o email.
- Primeiro e último nome de quem assinou e data da assinatura no formulário e no relatório final.
- Documentação de configuração do envio Graph em `docs/EMAIL_GRAPH_SETUP.md`.

### Alterado

- As fotografias ficam numa subpasta interna `Fotografias` dentro da pasta da folha no SharePoint.
- Guardar rascunho, finalizar, arquivar e cancelar preservam a subpasta de fotografias.
- A sincronização Graph passou a tratar subpastas e metadados aninhados, incluindo remoção de fotografias já apagadas localmente.
- A fila assíncrona passou a encadear arquivo, PDF, email e aviso Teams de forma persistente e repetível.
- Ao finalizar, o relatório HTML e o PDF são produzidos antes do envio ao cliente.
- O Excel final abre a folha `FS` e mantém a folha técnica `LINK` oculta.
- O e-mail do cliente é opcional; quando preenchido ativa o envio, e o modo de teste apresenta um aviso visível sobre a substituição do destinatário.
- O corpo português do email passou a indicar que a folha segue num único endereço e pode ser reencaminhada internamente.
- O valor Excel do número de obra tem prioridade; rascunhos antigos continuam compatíveis através do metadado JSON.
- Valores como `22` são apresentados como `0022`; valores com letras, negativos ou mais de quatro dígitos são rejeitados.
- `Armando Correia` utiliza a sigla `ARC`; `Artur Carvalho` mantém a sigla `AC`.

### Corrigido

- As fotografias deixam de entrar no JSON do documento, Excel, HTML, PDF, pré-visualização ou email do cliente.
- Metadados Graph das fotografias mantêm os caminhos relativos durante staging, publicação e recuperação.
- Rascunhos com fotografias continuam recuperáveis depois de reiniciar ou sincronizar a aplicação.
- O novo `N.º de obra` mantém-se depois de guardar e reabrir o rascunho.
- Cabeçalhos `Nº Obra`, `Nº de Obra`, `N.º obra`, `Número de obra` e `Numero de obra` passam a ser reconhecidos em qualquer coluna, incluindo `AH`.
- A inserção do número de obra em `AH` preserva a leitura de campos deslocados, como o código postal.
- A pesquisa da pasta segue todas as páginas Graph, atualiza a cache perante uma ausência e recusa códigos duplicados.
- Pedidos Graph limitados ou temporariamente indisponíveis repetem uma vez, respeitando `Retry-After` com espera limitada.
- A geração local do PDF deixa de recomendar um perfil temporário dentro do OneDrive, evitando falhas do lock do Chrome.
- O layout da assinatura acomoda nomes longos sem afetar a data.
- A lista de técnicos passa a usar `Luís Califórnia`.

### Limites e compatibilidade

- Não existe limite de quantidade; mantém-se o máximo de 10 MB por ficheiro e 50 MB no total.
- Formatos sem pré-visualização nativa no browser continuam aceites e são guardados normalmente.
- O número de obra é apenas de leitura: nenhum URL é guardado no Excel e a aplicação não altera o valor de origem.
- Sem número válido, a interface apresenta `----` e mantém a ligação desativada com indicação acessível.
- O acesso efetivo à pasta continua sujeito às permissões SharePoint do utilizador.

### Configuração e deploy

- Novas variáveis desta versão original: `GRAPH_WORKS_PATH`, `GRAPH_WORKS_CACHE_SECONDS`, `GRAPH_SHAREPOINT_HOSTNAME`, `FS_MAIL_ENABLED`, `FS_MAIL_SENDER`, `FS_MAIL_TEST_RECIPIENT`, `FS_PDF_BROWSER_PATH`, `FS_PDF_TEMP_DIR`, `FS_TEAMS_NOTIFICATIONS_ENABLED` e `FS_TEAMS_WEBHOOK_URL`. As credenciais separadas `GRAPH_MAIL_*` foram entretanto descontinuadas.
- A aplicação Microsoft Graph usada no envio necessita da permissão de aplicação `Mail.Send` com consentimento de administrador.
- O webhook Teams deve ser HTTPS e é considerado um segredo de produção.
- Chrome ou Edge só tem de estar disponível quando o backend de armazenamento for local; o backend Graph/Plesk não depende de browser no servidor.
- Em modo local, `FS_PDF_TEMP_DIR` deve ficar vazio para usar os temporários do sistema ou apontar para disco local gravável fora do OneDrive.
- Recomenda-se uma atualização forçada do browser depois do deploy para substituir o service worker e os assets anteriores.

### Validação

- Suite integral: `155 passed` em Python 3.13.
- Testes de formatos móveis, limites, deduplicação, remoção, arquivo, cancelamento e sincronização da subpasta `Fotografias`.
- Testes de leitura do número de obra em colunas variáveis, aliases, normalização, paginação, cache, duplicados, validação do domínio e respostas HTTP de erro.
- Validação em browser real nos formatos desktop, tablet e telemóvel.
- Teste completo de adicionar fotografia, guardar rascunho, reabrir e recuperar a fotografia.
- Teste real do botão `0022`, redirecionamento para a pasta SharePoint e abertura num novo separador.
- Teste real da conversão local para PDF e aceitação de um único email pelo Microsoft Graph, com cancelamento controlado de uma tentativa duplicada.
- JavaScript validado sintaticamente com `node --check`.

## v1.2.0 - Sincronizacao transacional e publicacao assincrona

Data: 2026-07-21
Commit: incluido nesta versao

### Adicionado

- Estado de edicao transacional em SQLite/WAL, substituindo os ficheiros JSON com locks manuais.
- Bootstrap unico que devolve Excel, autosave efetivo, assinaturas, identidade e sessao de edicao no primeiro pedido.
- Fila persistente para publicar, arquivar e remover itens no Microsoft Graph com retry exponencial.
- Snapshot local imutavel de cada rascunho antes de o trabalho ser colocado na fila.
- Endpoints de estado `/api/graph/status` e `/api/graph/jobs/<job_id>` para operacao e diagnostico.
- Coordenacao entre abas do mesmo browser para manter apenas uma aba editavel por rascunho.
- Checklist de administracao em `docs/SINCRONIZACAO_V2_ADMIN.md`.

### Alterado

- A abertura usa imediatamente a cache local e pede o refresh do SharePoint em segundo plano.
- O autosave local inicia apos 150 ms e o autosave no servidor apos 750 ms.
- O indicador distingue copia no dispositivo, gravacao no servidor e publicacao pendente no SharePoint.
- Sair, atualizar ou mudar de separador fecha apenas a sessao do browser; a propriedade do rascunho permanece com o tecnico que o criou.
- Folhas originais continuam a criar areas privadas independentes por tecnico/sessao, evitando escrita cruzada antes da criacao dos rascunhos.
- Guardar, finalizar e cancelar concluem primeiro no armazenamento local; as operacoes Graph deixam de bloquear o pedido web.
- O refresh da lista deixa de substituir ou recarregar o formulario que esta a ser editado.

### Corrigido

- A primeira abertura deixa de mostrar uma folha vazia antes de recuperar os dados.
- Uma unica atualizacao passa a recuperar diretamente o autosave efetivo do servidor.
- A troca de visibilidade da pagina deixa de libertar e readquirir repetidamente a edicao.
- A ausencia temporaria do Graph deixa de impedir o tecnico de guardar trabalho no servidor local.
- Finalizar imediatamente depois de criar um rascunho deixa de depender de o upload anterior ja ter terminado.
- A fila preserva o nome do item ativo antes de o bundle local ser movido para Arquivadas ou Canceladas.
- Retries parciais sao idempotentes e os snapshots so sao removidos depois da conclusao confirmada.
- Os caminhos internos da fila foram encurtados para compatibilidade com os limites tradicionais do Windows.

### Migracao e administracao

- Estados JSON antigos sao importados automaticamente para SQLite sem apagar os originais.
- `FS_APP_DATA_DIR` tem de apontar para disco local persistente e gravavel, fora de OneDrive/NFS/SMB.
- Novas variaveis recomendadas: `FS_EDIT_SESSION_SECONDS=2592000`, `FS_STATE_DB_BUSY_MS=10000`, `FS_GRAPH_REFRESH_SECONDS=30` e `FS_GRAPH_JOB_STALE_SECONDS=900`.
- Varios workers no mesmo host sao suportados. Varias replicas em hosts diferentes exigem PostgreSQL e nao fazem parte desta versao.

### Validacao
- Validacao direta no Chrome: a folha abriu com os dados na primeira tentativa e recuperou o marcador de QA depois de uma unica atualizacao.
- A acao `Recarregar` preservou a edicao e os tres separadores terminaram sem erros ou avisos na consola.
- Duas sessoes sobre o mesmo Excel original mantiveram conteudos isolados e criaram rascunhos distintos com os sufixos `Sem_Tecnico` e `Sem_Tecnico_1`.
- O mesmo rascunho ficou editavel numa unica aba; a aba em consulta recuperou a edicao cerca de um segundo depois da libertacao.
- Materiais recolhidos, total de horas manual, lista de sistemas e dispensa de assinatura foram confirmados visualmente no formulario.
- Testes de fila confirmam ETags propagados e execucao serial entre dois workers sobre a mesma base de dados.

- Suite integral: `64 passed` em Python 3.13.
- Teste de 24 transacoes concorrentes sobre o mesmo documento sem perda de atualizacoes nem ficheiros `.lock`.
- JavaScript validado sintaticamente com `node --check`.
## v1.1.2 - Rascunhos privados por tecnico

Data: 2026-07-14
Commit: incluido nesta versao

### Adicionado

- Area de trabalho privada por tecnico e separador enquanto a folha original continua no estado "Pronta".
- Identidade propria para cada rascunho criado, separando definitivamente autosave, revisao e reserva.
- Indicacao visual "Novo rascunho privado" e estados de gravacao que explicam que so o tecnico atual ve as alteracoes.

### Alterado

- A folha original deixou de ter uma reserva exclusiva partilhada; varios tecnicos podem agora iniciar trabalho sobre a mesma obra em simultaneo.
- "Guardar rascunho" cria uma copia individual e unica; colisoes simultaneas recebem sufixos como "_JF" e "_JF_1".
- "Guardar e enviar" e "Cancelar folha" ficam indisponiveis na folha original e so podem ser usados depois de criar o rascunho individual.
- Os autosaves partilhados do fluxo antigo deixam de ser herdados por novas areas privadas, garantindo um arranque limpo a partir do Excel original.
- A reserva exclusiva continua ativa dentro de cada rascunho ja criado, protegendo a sua edicao contra sobrescritas.

### Corrigido

- Dois tecnicos na mesma obra deixam de escrever sobre o mesmo autosave antes de criarem as respetivas folhas.
- Uma atualizacao recupera logo a area privada correta, sem mostrar primeiro uma folha em branco nem exigir uma segunda atualizacao.
- A criacao concorrente de rascunhos passou a reservar a pasta de destino de forma atomica.
- A resposta da criacao passa imediatamente para a identidade do novo rascunho, evitando misturar revisoes da folha original e da copia.
- A folha original deixa de poder ser finalizada ou cancelada por engano.

### Validacao

- Suite integral: 57 testes aprovados em Python 3.13.
- JavaScript validado sintaticamente com node --check.
- Teste real no browser com dois separadores sobre "2026_4769 Casa Saurimo": os textos A e B ficaram isolados e foram recuperados com uma unica atualizacao.
- Os dois tecnicos criaram "2026_4769 Casa Saurimo_2026-07-14_JF" e "2026_4769 Casa Saurimo_2026-07-14_JF_1"; ambos abriram logo com o conteudo correto.
- Os quatro Excel originais mantiveram os hashes SHA-256 anteriores; a folha base QA permaneceu byte a byte igual ao original.
- O servidor QA curto terminou sem Traceback, ERROR ou Exception.

## v1.1.1 - Sincronizacao e recuperacao

Data: 2026-07-14
Commit: incluido nesta versao

### Alterado

- A copia local e agora guardada apos 250 ms e a sincronizacao com o servidor inicia apos 900 ms.
- O indicador distingue `guardado neste dispositivo`, `a sincronizar` e `sincronizado no servidor`.
- O arranque da edicao deixou de depender da limpeza previa do IndexedDB.
- Copias recuperaveis inequivocas sao repostas automaticamente; so um conflito real de revisoes exige escolha.
- A reserva e libertada num unico pedido de fecho, juntamente com o ultimo autosave quando necessario.
- A selecao de uma folha e a acao `Recarregar` passam a usar uma navegacao completa e unica, iniciando sempre o mesmo ciclo de reserva, recuperacao e sincronizacao.
- O modo de consulta tenta retomar a edicao automaticamente a cada segundo.
- A reserva de emergencia expira em 10 segundos e e renovada a cada 3 segundos enquanto a folha esta ativa.
- Ao mudar de separador ou minimizar o browser, a folha e guardada e libertada; ao regressar, a edicao e retomada automaticamente se continuar livre.

### Corrigido

- A recuperacao deixa de escolher sempre a copia local, mesmo quando esta era antiga ou vazia.
- Copias locais identicas ao autosave do servidor sao deduplicadas no arranque.
- As colunas historicas do Excel `CCTV`, `PA/VA`, `SAI`, `EXT` e `OTHER` passam a representar corretamente `VSS`, `SADCO`, `SADIR`, `SADEI` e `OTHER`, preservando as selecoes existentes na leitura e na escrita.
- A primeira abertura deixa de ficar presa em `A preparar edicao segura` por espera do armazenamento local.
- Fechar, recarregar ou mudar de folha deixa de manter a reserva ativa ate ao timeout.
- A primeira folha aberta a partir da lista deixa de ficar presa em `A preparar edicao segura`; o coordenador inicia logo na primeira abertura, sem exigir uma atualizacao manual.
- O fecho da sessao e agora detetado por `pagehide`, `beforeunload` e pelo evento de congelamento do browser; se nenhum evento for entregue, o fallback liberta a folha em ate 10 segundos.
- Assinaturas recuperaveis continuam guardadas localmente mesmo depois de os restantes campos sincronizarem.
- A folha deixa de permanecer marcada como pendente depois de o mesmo conteudo ser confirmado pelo servidor.
- Alteracoes feitas enquanto um autosave esta em curso sao enviadas em seguida, sem respostas concorrentes fora de ordem.
- O indicador deixa de permanecer em `Sem ligacao` depois de o heartbeat confirmar que o servidor voltou a responder.
- Uma copia recuperada deixa de aparecer temporariamente como `sincronizada` quando a folha continua reservada por outra sessao.

### Validacao

- Suite integral: `52 passed` em Python 3.13.
- JavaScript validado sintaticamente com `node --check`.
- Os quatro Excel reais fornecidos foram importados e renderizados em copias isoladas; as formulas nao apresentam erros e os hashes SHA-256 dos originais permaneceram inalterados.
- Nos quatro ficheiros, o autosave concluiu entre 69 e 105 ms e a passagem para outro utilizador entre 117 e 155 ms, com libertacao imediata da reserva.
- No Chrome, a primeira abertura recuperou os dados em cerca de 1,6 s sem refresh manual; uma unica acao `Recarregar` preservou imediatamente o relatorio recuperado.
- No browser foram confirmados o mapeamento `SADI`/`SADIR`, a edicao manual para `8 h`, a dispensa de assinatura por ausencia do cliente e o indicador final `Sincronizado`.

## v1.1.0 - Integridade de dados e multiutilizador

Data: 2026-07-14
Commit: incluido nesta versao

### Adicionado

- Seletor compacto `Nao | Sim` para revelar a area de materiais apenas quando necessaria.
- Total efetivo de horas editavel pelo tecnico, mantendo o calculo automatico como valor inicial.
- Excecao `Cliente nao presente na obra`, permitindo finalizar sem assinatura e registando a ausencia no documento.
- Recuperacao local em IndexedDB por utilizador e folha, incluindo campos, linhas e assinatura.
- Autosave leve do JSON com estados `A guardar`, `Guardado` e `Sem ligacao`.
- Reservas de edicao por utilizador e aba, com heartbeat, expiracao e modo de consulta concorrente.
- Revisoes de documento, conflitos HTTP `409` e operacoes idempotentes para rascunho, envio e cancelamento.
- Fallback offline da PWA e pagina dedicada quando a navegacao nao esta disponivel.

### Alterado

- Lista de sistemas intervencionados corrigida para `SADI`, `VSS`, `SADCO`, `SADIR`, `SADEI`, `SCA`, `EAS`, `SADG`, `SCH` e `OTHER`.
- Escritas de Excel, JSON, assinaturas, HTML e observacoes passaram a ser atomicas.
- Atualizacoes no Microsoft Graph passaram a usar eTag e `If-Match` para evitar sobrescritas silenciosas.
- Cache da PWA limitado a recursos estaticos versionados; respostas `/api/` e dados das folhas deixaram de ser armazenados.
- Adicionada a configuracao `FS_EDIT_LEASE_SECONDS` para controlar a duracao das reservas.

### Corrigido

- Perda de alteracoes ao atualizar, recarregar, mudar de folha ou perder temporariamente a ligacao.
- Painel de materiais deixou de ocupar espaco quando nao existem materiais.
- Paineis de recuperacao e bloqueio marcados como ocultos deixaram de aparecer por sobreposicao de CSS.
- Acoes finais ficam desativadas enquanto a ligacao esta indisponivel e regressam depois da sincronizacao.

### Validacao

- Suite integral: `48 passed`.
- Validacao local em browser de autosave, refresh/restauro, duas abas, modo de consulta, falha de rede e retoma da sincronizacao.
- Sem erros de consola nem overflow horizontal no viewport de validacao.
- Os ficheiros Excel reais do utilizador nao foram alterados durante o QA.

### Nota de deploy

- A verificacao live, apenas de leitura, devolveu `404 itemNotFound` para o caminho Graph configurado `Aplicacao/Activas`.
- Confirmar `GRAPH_DRIVE_ID`, `GRAPH_ACTIVE_PATH` e permissoes do SharePoint antes de ativar o backend multiutilizador em producao.

## v1.0.4 - Local da loja e assinatura do cliente

Data: 2026-06-19  
Commit: `4f34bbd`

### Alterado

- A lista de folhas passou a mostrar o campo `Local / loja` logo a seguir ao cliente.
- A pesquisa de folhas passou tambem a incluir o valor de `Local / loja`.
- A assinatura do cliente no relatorio final deixou de mostrar o nome do cliente por baixo da caixa de assinatura.

### Validacao

- Testes focados de web/archive e schema executados com sucesso.

## v1.0.3 - Ajustes moveis e tecnico

Data: 2026-06-18  
Commit: `528d10b`

### Alterado

- Corrigido o nome do tecnico de `Jose Caldeira` para `José Califórnia`.
- Ajustado o friso superior em telemovel para ocupar menos espaco.
- O friso superior em telemovel passou a ficar fixo no topo, separado do scroll do formulario.
- Em telemovel, o titulo da folha fica compacto e com corte por reticencias quando necessario.
- Os botoes `Pre-visualizar`, `Exportar PDF`, `Recarregar` e o seletor de idioma passam a ficar numa linha horizontal com scroll.
- O texto completo da sessao fica escondido em telemovel para reduzir altura do cabecalho.

### Validacao

- Testes focados do schema executados com sucesso.

## v1.0.2 - Atualizacao do fluxo de folhas

Data: 2026-06-16  
Commit: `35eba24`

### Adicionado

- Suporte para ate 4 tecnicos no formulario.
- Campo de observacoes internas no fluxo de `Guardar e enviar`.
- As observacoes internas ficam guardadas junto da folha arquivada, mas nao aparecem no relatorio final.
- Instrucoes especificas para instalacao em iPhone via Safari quando aplicavel.

### Alterado

- Leitura do `LINK` passou a ser feita pelos cabecalhos, em vez de depender apenas da letra da coluna.
- A app passou a ignorar campos do `LINK` que nao fazem parte do formulario inicial.
- A leitura do `LINK` ficou tolerante a alteracoes na ordem das colunas.
- Campos duplicados como `Telefone` sao tratados pela ordem correta: primeiro telefone do cliente, segundo telefone da instalacao/contacto.
- Normalizacao da morada para evitar duplicacao de codigo postal/localidade.
- Divisao da morada para escrita no Excel: `Morada`, `Cod. Postal` e `CP`.
- Conversao defensiva de datas e horas antes de devolver dados em JSON.

### Corrigido

- Erro `Object of type time is not JSON serializable` quando o Excel devolvia horas reais.
- Duplicacao do codigo postal na morada.
- Escrita de checkboxes no `LINK`, incluindo limpeza correta quando um valor e desmarcado.
- Mapeamento dinamico para ficheiros `LINK` com colunas deslocadas ou removidas.

### Validacao

- Testes focados de Excel e schema executados com sucesso.

## v1.0.0-servidor - Versao para servidor com Microsoft Graph e PWA

Data: 2026-06-01  
Commit: `d1a5131`  
Tag: `v1.0.0-servidor`

### Adicionado

- Preparacao da aplicacao para servidor.
- Integracao com Microsoft Graph para leitura/escrita das folhas no SharePoint.
- Login Microsoft Entra ID / Office 365.
- Restricao de login a dominios autorizados da Sensorpoint.
- Configuracao PWA com manifest, service worker e icones.
- Ficheiros de exemplo para configuracao local e producao.
- Documentacao de apoio para Graph, Microsoft Login e implementacao em servidor.

### Alterado

- Autenticacao local antiga removida do fluxo principal.
- Configuracao de producao preparada para `service.sensorpoint.pt`.
- Cache Graph configuravel fora da pasta publica.
- App preparada para execucao com Docker/Plesk/Passenger/Gunicorn.

### Corrigido

- Ajustes no relatorio HTML para manter portugues correto e apresentacao consistente.
- Ajustes de compatibilidade para o fluxo de login Microsoft.

## v0.1.0 - Versao inicial limpa da app

Data: 2026-05-28  
Commit: `dfe2a6c`

### Adicionado

- Estrutura limpa do projeto.
- Aplicacao Flask para gerir folhas de servico.
- Leitura e escrita da sheet `LINK` dos ficheiros Excel.
- Listagem de folhas ativas.
- Edicao de dados de cliente, instalacao, servico, equipamentos, relatorio, materiais e tecnicos.
- Assinatura do cliente no formulario.
- Guardar rascunho.
- Guardar e enviar com arquivo da folha.
- Cancelamento de folha.
- Geracao de documento HTML do relatorio.
- Servicos separados para Excel, arquivo, dados do documento, assinaturas e ficheiros.
- Testes automatizados iniciais.
- Documentacao inicial do projeto e deploy.
- Dockerfile e docker-compose.

### Notas

- Esta versao serviu como base limpa antes das alteracoes de servidor, Microsoft Graph e PWA.

## Como manter este ficheiro

- Adicionar sempre a versao mais recente no topo.
- Incluir data, commit ou tag.
- Separar por `Adicionado`, `Alterado`, `Corrigido` e `Validacao` quando fizer sentido.
- Referir impactos de deploy quando a mudanca exigir configuracao no servidor, Microsoft Entra, Graph, Docker ou Plesk.
