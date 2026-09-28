# Validação da interface — 28-09-2026

Âmbito: melhorias de cabeçalho móvel, pendências SADI, assinaturas invalidadas, confirmações e limite de materiais. Ensaios realizados sobre `aplicacao/`, no lançador isolado `tools/run_test_version.py`, em `http://127.0.0.1:5012`, com dados fictícios e transportes externos bloqueados pelo lançador. Não houve integração operacional, publicação ou envio de comunicações.

## Ambiente e comandos

- Windows, Chromium com user-agent `Chrome/153.0.0.0`, Playwright CLI 0.1.21, sessão independente `migration-ui`.
- `node --test --test-isolation=none App_Folhas_Checklists/aplicacao/tests/js/maintenance-model.test.cjs`: **9/9 testes passaram**. O modo sem isolamento por processo evita o `spawn EPERM` do runner no sandbox; estes testes são puros, sem acesso à rede.
- `node --check App_Folhas_Checklists/aplicacao/src/web/static/js/maintenance-editor.js`: passou.
- CLI iniciado com `npx --yes --package @playwright/cli playwright-cli -s=migration-ui open http://127.0.0.1:5012 --headed`. Após instalar no cache, os comandos usaram diretamente `node .../node_modules/@playwright/cli/playwright-cli.js`.
- Verificação browser por `run-code --filename=output/playwright/ui-navigation-qa.js`, `ui-layout-qa.js` e `ui-materials-qa.js`, a partir da pasta de trabalho. São guiões de ensaio e dependem da preparação fictícia indicada abaixo; não são testes independentes para CI.

## Resultados funcionais observados

Preparação: rascunho fictício `2026_9901_2026-09-28_TT`, Manutenção + SADI, dois locais, uma central convencional no Local 1 e uma fotografia sintética de um píxel, convertida pelo fluxo normal de upload.

| Percurso | Resultado |
|---|---|
| Tentar finalizar duas checklists incompletas | Resumo persistente de 50 pendências por local; primeiro campo pendente (data) recebeu foco. Após acrescentar a central, o resumo refletiu as novas pendências. |
| Ligação para pendência de outro local | Abriu Local 2 e focou a data. Texto da marca e ID da central do Local 1 permaneceram iguais. |
| Ligação para equipamento recolhido | Abriu o `<details>` da central e focou exatamente `conventional.0.model`. |
| Assinar com campos ainda por preencher | Técnico e cliente desenhados pela interação do rato; o backend demo guardou ambas as assinaturas e devolveu os respetivos tokens. Não foram inventados tokens na preparação. |
| Cancelar remoção de fotografia | Diálogo com nome acessível e foco inicial em «Voltar». Escape preservou fotografia, dados e ambos os tokens e devolveu o foco ao botão original. |
| Cancelar quantidade de 101 equipamentos | Confirmação acessível; Escape preservou integralmente os dados e assinaturas anteriores. |
| Alterar o modelo após assinar | Tokens e desenhos antigos removidos. Aviso identificou Local 1 e as assinaturas de técnico e cliente a recolher novamente; resumo continuou disponível. |
| Reabrir rascunho | Modelo alterado e fotografia mantidos pelo backend isolado. Nova tentativa de conclusão focou a data e exibiu mensagem curta: 61 pendências de checklist e 8 campos da FS, sem concatenar todas as perguntas no toast. |
| Limite de materiais | 12 materiais fictícios preenchidos pela UI; tentativa de acrescentar a 13.ª linha mostrou «Máximo de 12 materiais por folha.» e manteve as 12 linhas integralmente iguais. |

O guião de navegação concluiu **13 verificações explícitas** sem erro. A consola browser consultada após esse ensaio e a matriz móvel tinha **0 erros e 0 avisos**. O modelo recebeu testes adicionais de destinos das pendências, NC justificada, quantidade/cobertura/fotografias inválidas e preservação de equipamentos inativos.

## Matriz do cabeçalho

Testadas 12 combinações: larguras 320, 360 e 390 px, PT/EN e escala normal/200%. Em todas, os seis controlos do cabeçalho ficaram dentro dos seus limites, sem sobreposição e sem overflow horizontal da página. Foi injetado apenas no DOM de ensaio um cartão de sessão com nome fictício comprido e botão «Terminar sessão», porque o ambiente isolado não autentica um utilizador real.

| Largura | PT normal / 200%: altura do cabeçalho | EN normal / 200%: altura do cabeçalho | Largura do conteúdo |
|---|---|---|---|
| 320 px | 253 / 827 px | 253 / 854 px | 305 px |
| 360 px | 244 / 838 px | 244 / 838 px | 345 px |
| 390 px | 244 / 811 px | 244 / 811 px | 375 px |

A diferença de 15 px é a barra de deslocamento vertical do browser. A 200%, o cabeçalho cresce e as ações continuam acessíveis pelo scroll normal. A altura reduzida de 390 × 400 px também foi ensaiada: cabeçalho sem altura máxima, posição estática e overflow visível. A inspeção visual das capturas a 320 px confirmou o reflow das ações.

Capturas locais na pasta de trabalho `output/playwright/`: `ui-mobile-pt-320-1x.png`, `ui-mobile-pt-320-2x.png`, `ui-mobile-en-320-1x.png`, `ui-mobile-en-320-2x.png` e `ui-mobile-height-400.png`. Não foram incorporadas imagens grandes no pacote de migração.

## Limites da evidência

A escala de 200% foi exercitada com CSS `zoom: 2` para testar reflow; não foi validado o zoom nativo da interface do browser. O ensaio de altura reduzida não equivale a abrir um teclado virtual real. Faltam Android/iPhone físicos, Safari/iOS, toque/caneta, suspensão/rotação reais e leitor de ecrã. A matriz PT/EN refere-se às ações traduzidas da FS; o catálogo e a interface SADI continuam em português, conforme o âmbito existente. A persistência dos estados observada é da demo isolada e não comprova implantação operacional nem validade externa das assinaturas.
