# Entrega sobre a produção SADI

Revisão base: **1ede4d8a690593a5f2b1d2908f3670fc1d038d7d**.
Branch: `codex/producao-melhorias-20260928`.
O commit da entrega e a execução CI ficam identificados no PR e nos artefactos CI.
Obter a lista exata de ficheiros face à produção com:

```sh
git diff --name-status 1ede4d8a690593a5f2b1d2908f3670fc1d038d7d HEAD
```

Esta entrega substitui, para instalação em produção, a proposta do PR #2.
Aplicar a raiz desta branch; não instalar a cópia sob `migracao/`.

## Alterações

- Inventário SharePoint com índice local, atualização concorrente coordenada e
  diagnóstico sem nomes de clientes, com lista de ativas renovada sem recarregar a página.
- Doze materiais nos relatórios, paginação de textos extensos e identificação nas páginas.
- Melhorias de utilização móvel e navegação para campos pendentes SADI, mantendo
  a dispensa de assinatura do cliente por ausência e a obrigatoriedade do técnico.
- PWA com atualização de cache e indisponibilidade explícita quando não há rede.
- Aviso de `FS_ENVIRONMENT` ausente sem ativação implícita da produção.
- Imagem com HOME gravável, UID/GID configuráveis, worker separado e smoke PDF real.
- Correção de `redact_maintenance_json` para ignorar `direct_passthrough`;
  manifest público testado com SADI ativa em produção, com e sem login.
- Correção da fila já presente em `350dde2`: tarefas de finalização aguardam o
  commit e uploads pendentes impedem a finalização prematura. A base `1ede4d8`
  já chamava os métodos correspondentes, mas não os incluía na fila.
- Correções da revisão de `73bb3a2`: duas gravações antes do worker reutilizam
  metadados de publicação mais recentes; uploads antigos ultrapassados por uma
  publicação posterior confirmada deixam de bloquear a finalização e de poder
  republicar conteúdo antigo. O resultado guarda `superseded_by`.
- Criação de pasta recuperável após perda da resposta POST/PATCH, através de um
  nome temporário aleatório persistido e confirmação do ID antes da mudança de nome.
  Pastas homónimas sem prova de propriedade continuam protegidas contra adoção.
- O inventário ignora nomes inválidos com aviso e não descarrega metadados remotos.
  Limpa marcadores `.fs-local-dirty` antigos com DELETE condicionado pelo eTag;
  recupera cópias importadas sem apagar marcadores de edições locais.
- Trabalhos sem confirmação de commit expiram para revisão após uma hora
  (configurável), sem executar efeitos externos. Repetir volta a validar o commit.
  Estado malformado de um trabalho não interrompe a verificação dos restantes.
- Contenção do mutex recebe HTTP 503 com `Retry-After: 2`, em vez de erro 500.
  Conflitos reais 409/412, também no arquivo, continuam a exigir reconciliação.

O rodapé `FS <número>` e o nome `__folha_final` dos anexos mantêm-se.

## Preservação da produção

Mantêm-se o schema SADI, `maintenance_access.py`, `maintenance_private_bundle.html`
e o ramo de ativação operacional em `create_app()`. O serviço privado mantém o
formato e leitura anteriores; a primeira finalização cria explicitamente a pasta
SADI com 700 mesmo quando nunca houve gravação de rascunho.
Os ficheiros em `/app/data/sadi` continuam privados, com diretórios 700 e ficheiros
600, sem migração manual, alteração do formato ou alteração de proprietário.
O teste de compatibilidade usa dados fictícios gravados pelo escritor e schema
exatos de `1ede4d8`, com hashes e proveniência guardados junto da fixture.

No hs4, construir com **APP_UID=100 / APP_GID=101**; não aplicar chown ao volume.
Preservam-se os nomes `folhas-servico` e `folhas-servico-worker`, o volume
`app_app-data`, o bind de código e o bind de logs. O mapeamento de logs e a mudança
de HOME estão explicitados no [guia Docker](10_ALOJAMENTO_DOCKER.md).

## Comentário para o implementador

> A entrega foi reconciliada sobre 1ede4d8, mantendo a SADI operacional e o
> armazenamento privado existente. Usar a raiz da branch de produção indicada
> neste documento. No hs4 construir com APP_UID=100 e APP_GID=101, mantendo
> FS_PDF_BROWSER_NO_SANDBOX=true, o volume app_app-data, os binds e a configuração
> operacional. Não fazer chown nem migrar os JSON SADI. Antes de instalar,
> conferir o SHA da entrega, o diff face à base e o resultado do smoke PDF da
> imagem no PR. Fazer o primeiro refresh autenticado e confirmar o inventário
> antes de disponibilizar a edição; verificar avisos de nomes inválidos e limpeza
> adiada de marcadores. Seguir 10_ALOJAMENTO_DOCKER.md para backup, validação,
> conflitos históricos que requerem intervenção e rollback.
