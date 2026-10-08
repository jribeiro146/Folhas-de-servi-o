# Fronteiras de transporte — implementação por etapas

## Guarda direta e transporte de teste (08/10/2026)

`GraphMailService` e `TeamsNotificationService` aceitam `transport=` como argumento
Python opcional, com a interface de `urlopen`. É uma dependência de código para
testes; não é configurável por HTTP, ficheiro de fila ou variável de ambiente.
Os factories operacionais continuam a omiti-la.

Sem transporte injetado, cada tentativa de I/O exige `FS_ENVIRONMENT=production`
e a integração explicitamente ativada (`FS_MAIL_ENABLED` ou
`FS_TEAMS_NOTIFICATIONS_ENABLED`). A flag é a configuração carregada no processo;
alterações operacionais à configuração requerem reinício coordenado. Os testes
não ativam envios reais: usam simuladores, configuração fictícia e bloqueio de rede.

Na fronteira de mail, a recusa acontece antes de obter o token ou construir o
pedido autenticado, também com token em cache e em chamadas diretas. A recusa
devolve erro de configuração; não se disfarça como comunicação incerta. A mesma
regra protege a obtenção do token e o POST final. No Teams, a guarda antecede o
pedido do webhook. Autorização de uma instalação em modo production não é uma
autorização humana para executar um teste com destinatários reais.

O transporte injetado permite exercitar o pedido real produzido pelo serviço e
as respostas sintéticas, incluindo HTTP 503 e perda da resposta após aceitação,
sem substituir métodos de preparação nem usar rede. Preservam-se comprovativos,
classificação de resultado incerto de mail e proibição de repetir esse envio.

Impacto: chamadas diretas que antes podiam usar urllib fora de production/sem
integração ativa passam a ser recusadas. Os testes antigos passam o simulador
explicitamente; não desativam a nova guarda. O modo demo do pacote exterior e
os seus binários não foram transportados para esta base.

Primeira revisão Claude: a recusa de configuração Teams herdava repetição
automática ilimitada da fila. Reproduzido e corrigido: erros de configuração
Teams passam a `retryable=False`. O trabalho fica falhado e visível, sem I/O
externo nem alegação de resultado incerto. Depois de corrigida a configuração,
a repetição manual segue as regras operacionais existentes. Os testes cobrem
as duas filas, ausência de I/O e uma única tentativa após a recusa.

## Envelope efetivo e limite codificado (08/10/2026)

A mensagem JSON efetivamente transmitida é validada antes da obtenção de token
ou do POST, também com transporte injetado e em chamadas diretas à fronteira HTTP.
Para/CC/BCC exigem estruturas e endereços válidos; com caixa de teste configurada,
todos têm de coincidir com ela. Sem caixa de teste, mantêm-se destinatários externos
de produção. `send_prepared` continua a reconstruir CC apenas com o técnico e a
excluir BCC legado. A validação não escreve na fila nem modifica mensagens antigas.

O limite local é **4 000 000 bytes** para o JSON UTF-8 completo, incluindo corpo,
metadados e base64 dos anexos. A documentação Microsoft refere 4 MB para pedidos
de escrita; escolheu-se a interpretação decimal conservadora, em vez de aceitar
4 MiB (4 194 304 bytes). Um PDF permitido pelo limite bruto pode ser recusado após
codificação. A mensagem indica que deve reduzir o conteúdo; não há upload de
anexos grandes automático. Estar dentro do limite não garante aceitação/entrega
ou ultrapassa políticas da caixa Microsoft. Fontes oficiais:
[limites do pedido Graph](https://learn.microsoft.com/en-us/graph/use-the-api) e
[sendMail](https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0).

Testes sintéticos verificam o limite exato e mais um byte, corpo UTF-8, base64,
Para/CC/BCC, payload inválido, token em cache e variantes do caminho sendMail,
sempre sem I/O externo. Recusas são falhas conhecidas, sem repetição automática;
os recibos e a reconciliação de envios incertos conservam a política existente.

## Próximas etapas, ainda não implementadas

Antecipar a validação no fluxo Graph antes da remoção do ativo: neste ponto,
`archive_and_remove` ainda remove antes de chamar `send_prepared`; não se dá a
etapa de fluxo como concluída. Só se validou a fronteira do mail. Acrescentar
assinatura institucional opcional privada; recuperar notificações Teams de forma
durável e distinguir o seu resultado incerto; sanitizar diagnóstico. Esta alteração
não aprova essas funcionalidades, a instalação ou o candidato completo.
