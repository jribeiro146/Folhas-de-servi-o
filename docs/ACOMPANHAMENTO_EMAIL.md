# Acompanhamento do email

O monitor consulta apenas o identificador devolvido pela finalização, através de
GET sem cache. Só confirma aceitação quando `result.mail.accepted` é literalmente
`true` e a resposta identifica esse trabalho. Um problema posterior no Teams não
retira essa aceitação. Aceitação pela Microsoft não comprova entrega ao destinatário.

Falhas de rede, HTTP429/5xx e JSON temporariamente inválido permitem novas consultas
do mesmo trabalho. Não provocam novo envio. Existem limites de consultas, duração
total e duração de cada pedido, incluindo a leitura do corpo. Os pedidos parados
são abortados. Retry-After aceita segundos ou data HTTP. Um prazo além da sessão
termina o acompanhamento com indicação de pausa, sem antecipar outra consulta.
Tentativas agendadas na fila recebem tratamento equivalente.

HTTP401 pede nova sessão;403 indica falta de autorização;404 conserva o resultado
como desconhecido. Estados provisórios de confirmação local e reconciliação não
sugerem repetição. Mensagens do fornecedor não são mostradas pelo monitor. A janela
de estado apresenta a referência e, quando existe, o prazo. O antigo botão de retry
sem CSRF/motivo foi retirado do editor; a repetição de falhas conhecidas continua a
depender do painel de administração e das verificações do servidor.

Prazos passados e 5xx sem Retry-After terminam por tempo esgotado, sem inventar um
agendamento. `held` persistente tem esse mesmo resultado neutro: a finalização HTTP
já confirmou o arquivo local, mas o worker pode não ter libertado o trabalho.
`mail_commit_unconfirmed` é reservado ao sinal explícito `commit_guard_required`.
Um prazo futuro dentro da sessão é respeitado antes da consulta seguinte.

Este bloco cobre a consulta durante a sessão aberta. Persistir referências após
recarregar/navegar e os ensaios integrais de finalização continuam pendentes.
`tools/check_mail_monitor_ui.py` executa quatro cenários no Chromium, com o HTML,
CSS, editor e monitor reais. Configura armazenamento temporário antes de importar
a aplicação; bloqueia sockets Python e interceta todos os pedidos do navegador.
O coordenador de edição e as respostas HTTP são simulados: o ensaio verifica a
janela de estado, referência/prazo, ausência de detalhes privados e que Fechar não
faz retry. Não é um ensaio integral de worker, autenticação ou entrega.

O worker em `src/queue_worker.py` processa uma vez por invocação; com `--watch`,
repete por predefinição a cada5s (mínimo1s). A cadência efetiva no Plesk não foi
observada e fica para o inventário de instalação. O monitor não pressupõe essa
cadência nem a presença do worker: expirar não declara sucesso ou falha de envio.
Não foram enviados emails ou Teams.
