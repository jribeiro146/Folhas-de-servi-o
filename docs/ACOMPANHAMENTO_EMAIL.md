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

Este bloco cobre a consulta durante a sessão aberta. Persistir referências após
recarregar/navegar, consultar vários trabalhos e os ensaios completos de interface
continuam pendentes. Os testes Node usam respostas sintéticas; não validam serviços
reais ou comportamento visual num navegador. Não foram enviados emails ou Teams.
