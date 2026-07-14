# Patch Notes

Registo das principais alteracoes por versao da aplicacao Folhas de Servico.

As versoes sem tag formal usam o commit Git como referencia. A versao marcada para servidor continua a ser `v1.0.0-servidor`, salvo indicacao posterior.

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
