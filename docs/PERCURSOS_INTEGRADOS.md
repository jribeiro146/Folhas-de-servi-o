# Percursos integrados de navegador e worker (M16)

## Executar e localizar a evidência

`python -B tools/check_integrated_journeys.py` executa uma matriz finita em Chromium, com a página, scripts, coordenador de edição, rotas HTTP, armazenamento SQLite e ciclo `src.queue_worker.main` reais. O processo web e o worker têm PIDs distintos e partilham apenas o armazenamento fictício desta execução. O worker interno da web fica desativado.

Usar um ambiente Python de QA com `requirements-server.txt` e `requirements-browser-tests.txt`; instalar o Chromium correspondente com `python -m playwright install chromium`. Estas dependências são opcionais de teste e não entram nas instalações pip do Docker de produção. Foram escolhidos Playwright 1.61.0 e pypdf 6.20.0. Instalar dependências numa venv/pasta de QA separada; não alterar o Python de produção para executar esta ferramenta. Os downloads de instalação são anteriores ao ensaio.

O lançador cria uma pasta `fst-*` nova no diretório temporário do sistema, limpa as variáveis operacionais e prepara um contrato de armazenamento sintético antes de importar a aplicação. Os filhos validam o marcador, os flags e as raízes herdadas. `FS_MAIL_ENABLED`, `FS_TEAMS_NOTIFICATIONS_ENABLED` e `FS_GRAPH_QUEUE_IN_WEB` permanecem `false`; o backend permanece `local`. O transporte injetado reconhece apenas os pedidos fictícios previstos, sem fallback de rede. A rede Python fica bloqueada antes dos imports da aplicação; o navegador só pode contactar a porta loopback deste ensaio. O Chromium do PDF admite apenas recursos locais/data.

Cada execução conserva `output/playwright/m16-fst-*/`: logs separados, capturas de conflito/recuperação e `result.json` apenas se todos os critérios passarem. O relatório inclui identidade do código, versões, PIDs, resultados e efeitos simulados. O armazenamento fictício é preservado para diagnóstico. Não copiar cookies ou a chave temporária para relatórios. Os processos arrancados pelo lançador são encerrados no fim, incluindo falhas de arranque; a interrupção abrupta selecionada do worker é intencional.

## Matriz e critérios

| Percurso | Prova exigida |
|---|---|
| Lista, abertura e autosave | Página real e coordenador ativo; gravação confirmada por HTTP |
| HTTP 503 no autosave | Texto e IndexedDB preservados, commit desativado, recuperação após remover a falha |
| Offline/online | Último formulário preservado e posteriormente confirmado |
| HTTP 401 real | Cookie fictício retirado; edição suspensa; novo login sintético e reload recuperam o texto |
| Conflito 409 real | Dois contextos de navegador independentes; cópia local preservada até escolha explícita |
| Falha de atualização da lista | Formulário aberto não substituído |
| Disco cheio no JSON do rascunho | HTTP 500, Excel legível e sidecar anterior intacto; autosave sobrevive ao reload; repetição reconcilia a gravação |
| Finalização, PDF, email e Teams | PDF real legível e hash igual ao anexo da mensagem; efeitos apenas no worker |
| Crash após recibo de email | Worker morre com código 73 após checkpoint; novo worker conclui a fila com um email e um Teams no total |
| Cancelamento | Rascunho sai das ativas, existe em Canceladas, nenhum efeito de comunicação adicional |
| Administração | Libertação/revogação e reposição pela UI, autosave preservado, conta normal recusada |
| Novo rascunho | UI cria exatamente uma cópia, conserva o Excel original e não envia mensagens |

O relatório agrega finalização e crash num único resultado, portanto exige onze resultados `PASS`, sem erros JavaScript ou pedidos externos. O journal de efeitos usa append e fsync: é conservado mesmo quando o worker morre. Só após confirmar a morte do processo é antecipado `updated_at` dos trabalhos em execução para representar o tempo de recuperação decorrido; a classificação e retoma continuam a cargo da fila real. Não se modifica o resultado do trabalho nem se repete um POST de envio no navegador.

## Defeito encontrado e correção

O ensaio demonstrou uma perda de autosave após falha parcial: guardar o Excel podia ter sucesso e escrever o JSON falhar. Ao reabrir, a comparação de fingerprint classificava essa escrita própria como alteração externa e apagava o autosave. O texto desaparecia do formulário, apesar de ter sido confirmado anteriormente no servidor. A reprodução está no teste `tests/test_draft_partial_failure.py` e no percurso real com ENOSPC/reload.

Ao registar a falha de uma operação de rascunho existente, a aplicação reconhece agora o fingerprint da sua escrita parcial enquanto ainda detém os bloqueios da operação e do bundle. O serviço exige uma operação `draft` pendente e o mesmo documento. Conserva o autosave e a revisão; o estado continua `failed`, sem fingir uma gravação concluída. Uma alteração externa posterior continua a mudar a revisão e a invalidar o autosave, com regressão própria. A criação de uma cópia nova não altera o fingerprint da área privada original. Finalização e cancelamento mantêm o tratamento existente.

Isto não transforma o bundle num sistema transacional de múltiplos ficheiros. A escrita Excel/JSON continua sequencial; após uma falha deve repetir-se a gravação antes de considerar o rascunho consolidado. A injeção selecionada testa ENOSPC no sidecar com SQLite disponível, não um disco integralmente indisponível nem todas as fronteiras de crash. Sem prova, não atribuir as queixas históricas a este defeito.

## Limites

O login Microsoft e a aceitação de email/Teams são simulados. Usam-se sessões assinadas e o parser/política reais, com GUIDs e contas fictícias; não se testa o fornecedor ou entrega ao destinatário. A preparação/validação de mensagens é real. O PDF usa `LocalPdfService` para publicação atómica e Chromium Playwright para renderizar o HTML real; o lançador Chromium padrão do Docker tem a prova separada de M15.

Este percurso usa backend local e não substitui os testes de concorrência/eTag, pastas ou cancelamento remoto Graph. O service worker fica bloqueado nesta matriz; cache e atualização têm o ensaio M19. SADI e privacidade por local pertencem à matriz M18. O P3 conhecido de M22 (bootstrap offline, três abas e retry oculto) permanece aberto e não é aprovado pelo M16. Não há deploy, migração operacional, email real ou ação no Teams.
