# Notas para futuros trabalhos — deploy Docker e melhorias

Data das notas: 28/09/2026.

Fonte: `C:/Users/joaoc/Downloads/RELATORIO_DEPLOY_1ede4d8_2026-09-26 (1).md`, relatório do deployer sobre a revisão `1ede4d8`, de 26/09/2026.

Estas notas foram guardadas a pedido do utilizador para orientar trabalhos futuros. Descrevem o relatório e propostas de melhoria; não comprovam o estado atual do código ou do servidor e não autorizam deploys, alterações operacionais ou envios reais. Nesta tarefa apenas foi criado este documento. Antes de executar trabalho futuro, ler o `AGENTS.md`, verificar as alterações locais e confirmar o que já foi corrigido.

## 1. Conclusão a reter

O deployer colocou a versão em produção depois de adaptar a configuração ao Docker real. A documentação pressupunha Passenger e o Compose do repositório não reproduzia o servidor. As soluções aplicadas no servidor precisam de ser reconciliadas com o repositório para não desaparecerem num próximo deploy.

Segundo o relatório, três bloqueios foram resolvidos no servidor: arranque do Chromium para PDFs, execução da fila por um worker separado e ativação explícita do modo de produção/SADI. A regressão do manifest PWA ficou pendente. A validação funcional completa também estava pendente.

## 2. Melhorias por prioridade e critérios de validação

### P0 — Corrigir o manifest PWA

- Local referido: `src/web/application.py`, hook `redact_maintenance_json`. Confirmar a localização na versão atual; os números de linha do relatório podem ter mudado.
- Causa relatada: `application/manifest+json` satisfaz `response.is_json`, mas o ficheiro é servido em `direct_passthrough`. A chamada a `get_json(silent=True)` lança `RuntimeError`; `silent=True` não cobre este erro.
- Correção proposta pelo deployer: devolver a resposta sem a analisar quando `response.direct_passthrough` for verdadeiro, além das condições já existentes.
- Validar GET a `/manifest.webmanifest` sem sessão e com conta sintética sem acesso SADI: sem erro 500, conteúdo e tipo de resposta corretos.
- Confirmar que a ocultação dos dados SADI continua a funcionar nas respostas JSON normais e que outros downloads JSON não são afetados. Não alargar permissões para resolver o erro.

### P1 — Garantir que o Chromium funciona na imagem

- O navegador estar instalado não demonstra que consegue gerar PDFs.
- Segundo o diagnóstico do deployer, `appuser` tinha `HOME=/nonexistent`. Foi necessário um HOME com escrita e, naquele Docker, desativar o sandbox do Chromium.
- Solução aplicada: `HOME=/tmp` e `FS_PDF_BROWSER_NO_SANDBOX=true`. Cada uma, isoladamente, falhou.
- Melhoria proposta: fornecer uma pasta pessoal utilizável no Dockerfile e documentar explicitamente a configuração de sandbox para o ambiente de destino.
- Não tornar a desativação do sandbox uma regra geral sem avaliar o contexto. É uma redução de proteção; o relatório justifica-a pelo utilizador sem privilégios, isolamento do container e HTML local gerado pela aplicação. Essas condições devem ser confirmadas na implementação atual.
- Validar a geração de um PDF sintético dentro da imagem final, como `appuser`, com a configuração efetiva de execução. A execução isolada não deve carregar `.env`, volumes ou filas operacionais.

### P2 — Tornar o worker parte da configuração Docker

- `FS_GRAPH_QUEUE_IN_WEB=false` exige um consumidor da fila fora do processo web.
- O deployer acrescentou o serviço `worker`, usando a mesma imagem, código, volume de dados e configuração da app. Comando relatado: `python -m src.queue_worker --watch --interval 5`.
- App e worker partilham a base SQLite no mesmo host. Confirmar a compatibilidade e o tratamento de concorrência na versão atual.
- Atualizar o Compose e a documentação em conjunto, evitando uma configuração que deixe trabalhos em `pending` ou mantenha consumidores não planeados.
- Testar apenas com base/fila sintética isolada e transportes simulados. Nunca iniciar o worker contra a fila operacional para validar uma alteração.
- Para alterações de fila/envio, verificar destinatários finais Para/CC/BCC, anexos, bloqueio de transporte e repetição/duplicação com mocks.

### P3 — Introduzir um teste de fumo da imagem

- Construir a imagem candidata e executar `LocalPdfService.export_html_pdf` sobre HTML sintético dentro dela, como utilizador da aplicação.
- Confirmar que o PDF existe, não está vazio e é legível; registar o resultado e a versão da imagem testada.
- Integrar o teste no CI ou na preparação de cada release. Os testes fora do container não detetaram a falha de HOME/sandbox.
- Não arrancar a aplicação operacional, não montar dados reais e não utilizar credenciais de produção neste teste.

### P4 — Avisar sobre modo de execução em falta

- O relatório indica que a ausência de `FS_ENVIRONMENT` leva a `development`, desativa a SADI e remove a flag `Secure` do cookie.
- Acrescentar um aviso quando a variável estiver ausente e houver `FS_AUTH_PROVIDER=microsoft` ou `FS_STORAGE_BACKEND=graph`.
- Não mudar automaticamente para produção: esse modo também participa nos controlos de integrações reais. Manter os bloqueios e exigir configuração explícita.
- Validar o aviso com configurações sintéticas e confirmar o comportamento da SADI e dos cookies na versão atual.

### P5 — Alinhar documentação e release notes

- Separar claramente os procedimentos Docker e Passenger em `docs/IMPLEMENTACAO_SERVIDOR.md`, README e notas de versão aplicáveis.
- Documentar worker, HOME, sandbox, volumes, montagens de código/logs e identidade do projeto Compose.
- Listar em cada release todas as variáveis novas e alterações de valores por defeito, com efeitos e passos de atualização.
- Tratar `.env.production.example` como referência de variáveis, nunca como substituição automática da configuração existente.
- Opcional: acrescentar `HEALTHCHECK` HTTP sem efeitos de escrita. Distinguir saúde do processo web de funcionamento da fila e das integrações.

## 3. Particularidades do servidor a reconfirmar antes de um deploy

Estado descrito em 26/09/2026, não verificado nesta tarefa:

| Elemento | Informação do deployer | Consequência |
|---|---|---|
| Plataforma | hs4, Plesk com Docker | Não aplicar o procedimento Passenger por analogia. |
| Pasta / projeto Compose | `/var/www/vhosts/service.sensorpoint.pt/app` / `app` | Outra identidade de projeto pode selecionar/criar um volume vazio. |
| Volume | `app_app-data` em `/app/data` | Preservar dados, cache, estado de edição e fila. |
| Código | `./src` montado em `/app/src` | O código do host sobrepõe-se ao da imagem; trocar apenas a imagem pode não mudar o código executado. |
| Logs | Montagem do diretório privado de logs do site | Preservar a montagem compatível com `FS_LOG_DIR`. |
| Porta | `127.0.0.1:8000`, atrás do proxy Plesk | Preservar a exposição prevista. |
| Graph: ativas | `02-Aplicação/Activas` | O exemplo `Aplicação/...` não coincide com este destino. |
| Graph: arquivo | `02-Aplicação/Arquivadas` | Confirmar destinos sem sobrescrever a configuração operacional. |

O relatório recomenda gerir estes containers através do Compose. A interface Plesk pode indicar que o volume não está montado por não apresentar corretamente volumes com nome; verificar a montagem efetiva antes de concluir que os dados desapareceram.

Variáveis relatadas após o deploy: `FS_ENVIRONMENT=production`, `FS_MAINTENANCE_ENABLED=true`, `FS_GRAPH_QUEUE_IN_WEB=false`, `FS_PDF_BROWSER_NO_SANDBOX=true` e `HOME=/tmp`. São referência do servidor, não configuração para testes locais.

## 4. Processo para preparar um próximo deploy

Esta sequência é um lembrete para um deploy posteriormente autorizado; não deve ser executada apenas por constar destas notas.

1. Verificar `git status`, preservar trabalho preexistente e identificar a revisão candidata. Não incluir ficheiros ou alterações incidentais no pacote.
2. Comparar a configuração proposta com o servidor real, sem expor segredos. Confirmar imagem, código montado, projeto Compose, volume, fila e destinos Graph.
3. Construir a imagem com uma tag própria e executar o teste de PDF isolado antes de interromper produção.
4. Preparar o plano de recuperação. Num deploy autorizado, parar os consumidores necessários e criar backups consistentes da pasta/configuração e dos dados; verificar os backups antes da substituição.
5. Preservar as montagens específicas do servidor. Confirmar o conteúdo copiado com uma comparação; o relatório encontrou um alias `cp -i` que impedia substituições como esperado.
6. Aplicar apenas as variáveis necessárias. Mostrar diferenças com valores sensíveis ocultados; não imprimir o `.env` ou um diff bruto que exponha segredos.
7. Arrancar a imagem aprovada com a identidade e diretório Compose corretos, preservando o volume existente.
8. Verificar app e worker, logs sem dados sensíveis, configuração efetiva, saúde da fila e geração de PDF. Um container em estado `Up` não prova o fluxo funcional completo.
9. Registar o que foi efetivamente validado, o que ficou pendente e os recursos de recuperação preservados.

## 5. Validação funcional pendente no relatório

Reconfirmar quais destes pontos já foram concluídos desde 26/09/2026. Para desenvolvimento, usar dados fictícios, armazenamento isolado e serviços simulados:

- [ ] Fluxo de autenticação e permissões: checklist disponível aos perfis autorizados e indisponível aos restantes.
- [ ] Gravar e reabrir rascunho preserva respostas.
- [ ] Finalização bloqueada sem assinatura do técnico.
- [ ] Finalização coloca os documentos corretos no destino previsto, mantendo a separação dos PDFs da folha e das checklists.
- [ ] PDFs SADI persistidos no diretório esperado; o relatório refere `/app/data/sadi`, criado na primeira checklist.
- [ ] Fluxo comum de arquivo/finalização atómica continua funcional numa folha sem SADI.
- [ ] Envio e repetição validados com transporte simulado e inspeção do payload final.

O relatório propõe uma folha fictícia com email do cliente vazio. Isso, por si só, não garante isolamento: podem existir CC/BCC, notificações e escritas Graph. Aplicar os bloqueios do `AGENTS.md` e verificar todos os efeitos.

A referência do deployer a uma primeira folha real com email aceite pelo Graph não autoriza esse envio. Uma eventual validação real precisa de autorização específica e revisão de remetente, destinatários efetivos, assunto, corpo e anexos. Aceitação pelo Graph não comprova entrega.

## 6. Recuperação e limitações da evidência

O deployer reportou uma imagem anterior `folhas-servico:pre-1ede4d8`, backups em `/root/backup-folhas-2026-09-26/` e o `.env` original em `.env.bak-2026-09-26`. A existência, integridade e disponibilidade atual destes recursos não foram verificadas.

O backup da pasta descrito no relatório já continha o `.env` alterado; a recuperação da configuração anterior depende também do `.env.bak-2026-09-26`. Não assumir que repor apenas a pasta devolve toda a configuração original.

Como o código está montado a partir do host, uma recuperação deve considerar código, imagem, Compose e configuração em conjunto. Restaurar o volume pode perder trabalho posterior ao backup e exige avaliação e autorização próprias. Não executar automaticamente o comando destrutivo de rollback incluído no relatório.

O relatório comprova apenas o que o deployer declarou ter observado na data indicada: imagem construída, PDF sintético gerado, app/worker iniciados e fila sem trabalhos pendentes/em execução/falhados naquele momento. Não comprova entrega de emails, validação funcional completa nem o estado atual do servidor.

## 7. Estado destas notas

- Documento criado e revisto em 28/09/2026.
- Nenhuma melhoria acima foi implementada por esta tarefa.
- Nenhum serviço, worker, teste funcional, envio ou deploy foi executado.
- Existem alterações locais preexistentes; não presumir que correspondem à revisão implantada nem que podem ser sobrescritas.
