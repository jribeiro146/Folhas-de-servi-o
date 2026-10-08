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

### Resposta à primeira revisão do envelope

Os caminhos alternativos de escrita e o JSON ambíguo estavam desprotegidos nas
chamadas internas diretas. Treze cenários reproduziram os dois achados. O serviço
agora aceita apenas POST ao endpoint de token do tenant configurado (sem bearer)
e POST a `/v1.0/users/<remetente configurado>/sendMail` no host Graph oficial (com
bearer). Não aceita `$batch`, forward/reply/envio de drafts, outro remetente, host,
método ou query. Esta restrição aplica-se com transporte injetado também.

Um `object_pairs_hook` recusa chaves JSON repetidas, inclusive diferenças só de
capitalização. No topo e em `message`, só aceita as propriedades utilizadas pelo
serviço, com a grafia exata. `from`, `sender`, `replyTo` ou propriedades desconhecidas
são recusadas. Não se depende da interpretação de JSON ambíguo pelo Graph.

Pesquisa dos consumidores em `src`, `tools` e `tests`: fora do próprio serviço,
nenhum consumidor operacional chama `_graph_bytes` ou `_request_bytes` de
**GraphMailService**. Web usa prepare/test_connection e a fila usa send_prepared.
Os serviços Graph de armazenamento/pastas têm os seus próprios métodos homónimos,
não afetados. Os únicos acessos diretos do mail são testes de fronteira/envelope
e um teste de falha de token, ajustado para o endpoint sintético do tenant da
configuração (continua transporte simulado, sem rede). Outros testes substituem
`_graph_bytes` por um simulador para observar o payload.

O Claude recomendou, para o passo de preflight, bytes imutáveis e um checkpoint
durável com hash/tamanho antes de remover o ativo; revalidar contra a configuração
no envio e reter se faltar/divergir, sem reconstruir mail aceite/incerto. Essa
refatoração, formatos persistentes e respetivos testes ainda não foram implementados.

## Mensagem fixa em memória (08/10/2026)

`prepare_delivery` produz `GraphMailDelivery`, um objeto imutável com os bytes
JSON finais e a identidade de envio (remetente, tenant e caixa de teste normalizada),
sem credenciais. Caixa de teste vazia identifica modo normal; preenchida identifica
modo de teste, sem guardar dois campos redundantes que possam divergir.
Não obtém tokens nem usa rede. O PDF é lido uma vez, com limite de 3 MiB + 1 byte,
e a mensagem codificada continua limitada a 4 000 000 bytes. Erros de leitura
são recusas conhecidas anteriores ao envio.

`validate_delivery` verifica a configuração, a política de transporte, a identidade
e a mensagem final sem reescrever conteúdo. Exige um único PDF válido, assunto não
vazio, corpo HTML em string (eventualmente vazia por compatibilidade com trabalhos
legados), correlação e gravação nos itens enviados. Assunto legado só com espaços
usa o assunto por omissão. Devolve metadados; isso não é uma
aceitação do Graph. A validação antecipada de política só dispensa a guarda de
ambiente quando há um transporte Python de teste explicitamente injetado.

`send_delivery` repete a validação e transmite os mesmos bytes, sem ler ficheiros
ou adaptar destinatários. Se mudar o remetente, tenant ou caixa de teste de forma
incompatível, recusa, incluindo a saída do modo de teste e a sua ativação mesmo
quando o destinatário original coincide com a caixa de teste. As credenciais podem
ser renovadas sem alterar a mensagem.
O comprovativo é preparado antes do POST e só recebe `accepted=true` depois do
retorno do transporte; continua a não comprovar entrega. Falhas ambíguas mantêm
a classificação anterior. `send_prepared` preserva a interface antiga através
de prepare_delivery + send_delivery.

Testes exercitam a API pública com transporte simulado: alteração do dicionário
de origem e remoção do PDF após preparação, bytes idênticos enviados, ausência
de I/O na preparação, mudança de configuração/política, leitura limitada e
mensagens incompletas/corrompidas. Os testes antigos que observam o payload por
`_graph_bytes` passam agora uma dependência de transporte explícita para não
contornarem implicitamente a validação antecipada de política.

Esta API ainda não persiste mensagens nem muda a ordem da fila. O próximo passo
tem de guardar e verificar o snapshot antes da remoção remota, contemplando
interrupções, formatos persistentes e restauro. A assinatura institucional opcional
também ainda não foi implementada.

### Resposta à primeira revisão da mensagem fixa

Quatro falhas reproduziram os dois achados: saída do modo de teste, entrada no
modo de teste com o mesmo destinatário e assunto legado só com espaços nos dois
modos. A identidade inclui agora a caixa de teste normalizada e o assunto vazio
após strip usa o texto por omissão. Mantém-se corpo HTML vazio por compatibilidade,
agora explicitado acima. Troca de caixa A por B continua recusada; diferenças
apenas de maiúsculas/espaços na configuração e rotação do secret são permitidas.

Pesquisa de consumidores em todos os ficheiros Python de src e tools: a única
chamada operacional a GraphMailService.send_prepared é em
GraphSyncQueue._send_mail_once. Web prepara os dados com prepare_service_email
e testa ligação; não faz envio direto. O método homónimo do Teams é independente.
prepare_delivery/send_delivery são por enquanto usados pelo wrapper e pelos testes.

Não existe tests/test_graph_sync_queue*.py nesta árvore. Os testes de fila estão
em test_graph_mail_service.py, test_graph_finalization_queue.py,
test_audit_mail_recovery.py, test_audit_worker_ownership.py,
test_graph_publication_recovery.py e test_audit_queue_corruption.py, entre outros
testes de web/finalização. Nenhum depende de classificar OSError de leitura do PDF
como mail_unknown. Foram acrescentados dois casos públicos de fila: PDF ausente
ou PermissionError na leitura após o exportador devolver o caminho. Confirmam
mail_rejected, sem reconciliação, sem retry automático e zero I/O de transporte.
Preservam-se testes de resultado ambíguo depois do POST e de falha anterior no token.

O Claude considerou adequado o desenho seguinte com BLOB/checkpoint na mesma
SQLite, sob condição de identidade/hash/tamanho, transação por tentativa, FULL
em cada conexão, formato queue=2, checkpoint de remoção, migração explícita e
retenção/restauro. Esse parecer é sobre o desenho; persistência e mudança da ordem
ainda não foram implementadas nesta alteração.

### Resposta à segunda revisão da mensagem fixa

A revalidação exige agora prefixo `[TESTE]` quando a identidade guardada identifica
modo de teste. Não acrescenta o prefixo nem modifica os bytes ao enviar. Em modo
normal, um assunto com ou sem esse marcador continua permitido e não muda o modo.
O caso de teste com marcador removido falhou antes da correção; dois controlos
positivos confirmam os bytes inalterados no modo normal.

Acrescentado também o cenário Graph com PDF ilegível: mail_rejected conhecido,
sem reconciliação nem transporte mail. O teste expõe a limitação ainda existente:
o ativo foi removido antes dessa recusa. Será alterado quando a preparação durável
for integrada no fluxo, na próxima mudança, sem alegar resolução antecipada.

O pedido de executar `tools/validate_migration.py` refere uma ferramenta do pacote
demo exterior, `App_Folhas_Checklists/aplicacao/tools/validate_migration.py`.
Esse ficheiro não existe no checkout de produção desta revisão. Executar o do demo
não validaria este SHA; não foi copiado nem apresentado como teste da produção.
A bateria pytest completa da árvore canónica é a verificação aplicável, complementada
pelos ensaios de migração explícita ainda previstos no plano.

## Próximas etapas, ainda não implementadas

Antecipar a validação no fluxo Graph antes da remoção do ativo: neste ponto,
`archive_and_remove` ainda remove antes de chamar `send_prepared`; não se dá a
etapa de fluxo como concluída. Só se validou a fronteira do mail. Acrescentar
assinatura institucional opcional privada; recuperar notificações Teams de forma
durável e distinguir o seu resultado incerto; sanitizar diagnóstico. Esta alteração
não aprova essas funcionalidades, a instalação ou o candidato completo.
