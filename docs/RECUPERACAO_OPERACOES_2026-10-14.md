# Recuperação de operações após perda da resposta

## F01 — finalização concluída sem resposta no navegador

A finalização guarda uma referência opaca antes do POST. Se a resposta se perder, a página consulta o recibo persistido; uma recarga faz essa consulta antes de abrir novamente a folha. Uma conclusão reconhecida encaminha para a lista e para o acompanhamento normal do email, sem repetir o POST. O texto «aceite pela Microsoft» continua a depender da consulta da fila e não confirma entrega na caixa.

O novo GET `/api/editing/operations/<document_id>/<idempotency_key>` não depende da presença do ficheiro nas ativas. Lê o estado validado sem o criar ou atualizar, exige o âmbito da sessão e compara a identidade persistida da operação, incluindo fornecedor/tenant. Devolve apenas metadados de conclusão; não devolve destinatários, corpo, ficheiros, assinaturas, reservas ou observações internas. Operações antigas sem prova de identidade não são expostas por este percurso.

`commit-recovery.js` guarda por folha, em sessionStorage, apenas âmbito, tipo, documento e chave idempotente. Não guarda o formulário ou o token de edição. A chave é reutilizada se uma tentativa explícita ainda for necessária. Durante o envio, novos autosaves e heartbeats ficam suspensos. A recuperação automática só consulta; não envia. O módulo integra os recursos versionados e a lista pública do service worker.

Se a consulta estiver indisponível ou a operação continuar pendente, a folha fica protegida até Voltar a tentar. Se a sessão mudar, a referência anterior não é consultada. Se o navegador não conseguir guardar a referência, ou o módulo não carregar, a finalização é recusada antes do POST. A recuperação não substitui a reconciliação administrativa de operações incertas nem migra referências entre logins.

## Validação do candidato F01, primeira revisão

- Regressão HTTP antes da implementação: 4 falhas esperadas por ausência do endpoint; 2 proteções de base passaram. `f01-python-red.txt`.
- 46 testes Python de recuperação, âmbito de sessão e identidade HTTP passaram. `f01-python-dev2.txt`.
- 207 regressões de estado de edição, fila, finalização, cancelamento, publicação e quarentena passaram. `f01-regression-r1.txt`.
- 185 testes JavaScript passaram. `f01-js-r1.txt`.
- 14 grupos Chromium passaram em `output/playwright/m16-fst-1edc4d9f/result.json`: os 12 percursos anteriores e duas variantes de perda de resposta após finalização. Uma recupera na mesma página; a outra perde também a consulta, recarrega e recupera por Voltar a tentar. Ambas verificam um POST e um email simulado por operação. Captura `f01-reload.png` inspecionada.

Os logs referidos ficam em `qa-output/consolidacao-20261007` da pasta de trabalho. Testes com dados novos, autenticação/transportes fictícios e rede externa bloqueada; nenhum envio real. A primeira assertiva de recarga reutilizava a identidade de uma folha sintética já encerrada; o ensaio foi corrigido para usar duas identidades distintas. Outra execução encontrou WinError 5 em `os.rename` do arquivo temporário; o diagnóstico foi preservado e a causa não foi atribuída sem prova. A execução final usou temporários do sistema fora do OneDrive e passou. Os testes Python também usam caminhos temporários curtos, após dois casos excederem o limite Windows em `qt`.

F02 (rascunho criado sem resposta) e F03 (resposta HTML a uma operação) continuam separados e pendentes nesta revisão. Esta mudança não atualiza instâncias em execução, não faz deploy e não substitui a validação final no servidor de destino.
