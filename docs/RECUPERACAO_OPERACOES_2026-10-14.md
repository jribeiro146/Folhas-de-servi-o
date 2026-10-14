# Recuperação de operações após perda da resposta

## F01 — finalização concluída sem resposta no navegador

A finalização guarda uma referência opaca antes do POST. Se a resposta se perder, a página consulta o recibo persistido; uma recarga faz essa consulta antes de abrir novamente a folha. Uma conclusão reconhecida encaminha para a lista e para o acompanhamento normal do email, sem repetir o POST. O texto «aceite pela Microsoft» continua a depender da consulta da fila e não confirma entrega na caixa.

O novo GET `/api/editing/operations/<document_id>/<idempotency_key>` não depende da presença do ficheiro nas ativas. Lê o estado validado sem o criar ou atualizar, exige o âmbito da sessão e compara a identidade persistida da operação, incluindo fornecedor/tenant. Devolve apenas metadados de conclusão; não devolve destinatários, corpo, ficheiros, assinaturas, reservas ou observações internas. Operações antigas sem prova de identidade não são expostas por este percurso.

`commit-recovery.js` guarda por folha, em sessionStorage, apenas âmbito, tipo, documento e chave idempotente. Não guarda o formulário ou o token de edição. Uma tentativa confirmada como falhada perde a referência no navegador: a próxima ação explícita usa uma chave nova e o conteúdo atual. Uma operação ainda não encontrada mantém a referência. Durante o envio, novos autosaves e heartbeats ficam suspensos. A recuperação automática só consulta; não envia. O módulo integra os recursos versionados e a lista pública do service worker.

Se a consulta estiver indisponível ou a operação continuar pendente, a folha fica protegida até Voltar a tentar. Se a sessão mudar, a referência anterior não é consultada. Se o navegador não conseguir guardar a referência, ou o módulo não carregar, a finalização é recusada antes do POST. A recuperação não substitui a reconciliação administrativa de operações incertas nem migra referências entre logins.

A revisão independente 1/3 pediu duas correções. Primeiro, o servidor calcula um digest do pedido antes de o preparar, incluindo notas privadas, assinaturas e identidades das fotografias/removidas. Uma repetição parcial com conteúdo diferente recebe 409; nunca substitui silenciosamente o formulário pelo `prepared_document` anterior. O navegador consulta a falha e permite nova finalização explícita. Um arquivo de uma tentativa falhada é preservado como evidência, sem entrega pela fila cuja operação não foi consolidada. Segundo, referências locais malformadas ou demasiado grandes não são interpretadas como operações válidas: ficam intactas e a abertura normal da folha aplica as verificações de reserva e encerramento. Não bloqueiam guardar/cancelar uma folha ativa e não geram reenvio automático; não permitem recuperar uma identidade de operação que já esteja ilegível.

## Validação do candidato F01, primeira revisão

- Regressão HTTP antes da implementação: 4 falhas esperadas por ausência do endpoint; 2 proteções de base passaram. `f01-python-red.txt`.
- 46 testes Python de recuperação, âmbito de sessão e identidade HTTP passaram. `f01-python-dev2.txt`.
- 207 regressões de estado de edição, fila, finalização, cancelamento, publicação e quarentena passaram. `f01-regression-r1.txt`.
- 185 testes JavaScript passaram. `f01-js-r1.txt`.
- 14 grupos Chromium passaram em `output/playwright/m16-fst-1edc4d9f/result.json`: os 12 percursos anteriores e duas variantes de perda de resposta após finalização. Uma recupera na mesma página; a outra perde também a consulta, recarrega e recupera por Voltar a tentar. Ambas verificam um POST e um email simulado por operação. Captura `f01-reload.png` inspecionada.

Os logs referidos ficam em `qa-output/consolidacao-20261007` da pasta de trabalho. Testes com dados novos, autenticação/transportes fictícios e rede externa bloqueada; nenhum envio real. A primeira assertiva de recarga reutilizava a identidade de uma folha sintética já encerrada; o ensaio foi corrigido para usar duas identidades distintas. Outra execução encontrou WinError 5 em `os.rename` do arquivo temporário; o diagnóstico foi preservado e a causa não foi atribuída sem prova. A execução final usou temporários do sistema fora do OneDrive e passou. Os testes Python também usam caminhos temporários curtos, após dois casos excederem o limite Windows em `qt`.

F02 (rascunho criado sem resposta) e F03 (resposta HTML a uma operação) continuam separados e pendentes nesta revisão. Esta mudança não atualiza instâncias em execução, não faz deploy e não substitui a validação final no servidor de destino.

## Validação do candidato F01, segunda revisão

303 testes Python passaram em `f01-python-r2-final.txt`; 186 JavaScript passaram em `f01-js-r2.txt`. Os 16 grupos Chromium passaram em `output/playwright/m16-fst-bd9840b0/result.json`, log `f01-browser-r2-pass.txt`. Os dois novos grupos comprovam (a) falha após publicação parcial, recarga, edição, finalização com texto atual e um email simulado; (b) referência local corrompida, recarga, gravação e cancelamento normais. Os testes HTTP provocam falhas antes/depois da publicação, recusam alteração com a mesma chave e confirmam texto/notas/destinatário atuais com uma chave nova e recibo autenticado depois de retirar a origem.

Os ensaios intermédios ficam preservados: o novo teste HTTP usava inicialmente o nome errado do campo de notas privadas; o teste de navegador procurava o painel de acompanhamento depois de uma conclusão normal (que já o fecha) e depois um seletor de toast incorreto; outro caso reutilizava as iniciais de uma folha sintética já encerrada. Corrigidos os testes, incluindo espera pela reserva de edição pronta antes de preencher. Não se contam essas execuções como aprovação. A recusa HTTP passou a incluir explicitamente o código `operation_payload_changed` no corpo JSON, para além do estatuto 409.
