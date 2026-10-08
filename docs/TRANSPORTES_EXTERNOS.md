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
**GraphMailService**. Web usa prepare/test_connection; a fila usa agora a API de
mensagem fixa descrita abaixo (antes usava send_prepared).
Os serviços Graph de armazenamento/pastas têm os seus próprios métodos homónimos,
não afetados. Os únicos acessos diretos do mail são testes de fronteira/envelope
e um teste de falha de token, ajustado para o endpoint sintético do tenant da
configuração (continua transporte simulado, sem rede). Outros testes substituem
`_graph_bytes` por um simulador para observar o payload.

O Claude recomendou, para o passo de preflight, bytes imutáveis e um checkpoint
durável com hash/tamanho antes de remover o ativo; revalidar contra a configuração
no envio e reter se faltar/divergir, sem reconstruir mail aceite/incerto. Essa
refatoração foi implementada na etapa de mensagem durável descrita abaixo.

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

Esta API, isoladamente, não persiste mensagens. A integração durável da fila e a
assinatura institucional opcional estão descritas abaixo, em etapas próprias.

### Resposta à primeira revisão da mensagem fixa

Quatro falhas reproduziram os dois achados: saída do modo de teste, entrada no
modo de teste com o mesmo destinatário e assunto legado só com espaços nos dois
modos. A identidade inclui agora a caixa de teste normalizada e o assunto vazio
após strip usa o texto por omissão. Mantém-se corpo HTML vazio por compatibilidade,
agora explicitado acima. Troca de caixa A por B continua recusada; diferenças
apenas de maiúsculas/espaços na configuração e rotação do secret são permitidas.

Na revisão da API, o único consumidor operacional de GraphMailService.send_prepared
era GraphSyncQueue._send_mail_once. Na integração durável, a fila passou a
prepare_delivery/validate_delivery/send_delivery. Web prepara os dados com
prepare_service_email e testa ligação; não faz envio direto. O método homónimo
do Teams é independente.

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
retenção/restauro. Esse parecer aprovou apenas o desenho; a implementação durável
abaixo exige testes e revisão próprios.

### Resposta à segunda revisão da mensagem fixa

A revalidação exige agora prefixo `[TESTE]` quando a identidade guardada identifica
modo de teste. Não acrescenta o prefixo nem modifica os bytes ao enviar. Em modo
normal, um assunto com ou sem esse marcador continua permitido e não muda o modo.
O caso de teste com marcador removido falhou antes da correção; dois controlos
positivos confirmam os bytes inalterados no modo normal.

Acrescentado também o cenário Graph com PDF ilegível: mail_rejected conhecido,
sem reconciliação nem transporte mail. Na revisão da API isolada, o ativo ainda
era removido antes dessa recusa. O teste foi posteriormente alterado e falhou
para reproduzir essa ordem; a integração durável abaixo corrige-a.

O pedido de executar `tools/validate_migration.py` refere uma ferramenta do pacote
demo exterior, `App_Folhas_Checklists/aplicacao/tools/validate_migration.py`.
Esse ficheiro não existe no checkout de produção desta revisão. Executar o do demo
não validaria este SHA; não foi copiado nem apresentado como teste da produção.
A bateria pytest completa da árvore canónica é a verificação aplicável, complementada
pelos ensaios de migração explícita ainda previstos no plano.

## Mensagem durável antes da remoção (08/10/2026)

Nos trabalhos com email, a fila prepara a mensagem após obter o PDF e antes de
remover o ativo. Guarda os bytes finais em `graph_mail_messages` na própria base
da fila, juntamente com remetente, tenant, caixa de teste, tentativa criadora,
tamanho e SHA-256. O digest vincula estes campos, ID do trabalho e bytes; não é
uma assinatura criptográfica contra alguém com acesso de escrita à base.
A inserção e o checkpoint `message_ready` confirmam na mesma transação IMMEDIATE,
condicionada ao `attempt_token`. Cada ligação usa synchronous=FULL. Nenhum token
de acesso ou secret é guardado no snapshot.

Uma retoma usa essa mensagem sem novo upload, exportação ou leitura do PDF. Verifica
tipo/tamanho antes de carregar o BLOB, metadados/digest e identidade/política atual
antes de qualquer remoção ou envio. Estado em falta, divergente ou já retirado sem
recibo fica `invalid_state`, sem reconstrução ou retry manual genérico. Mudanças
de configuração incompatíveis recusam o envio e preservam os bytes para diagnóstico.

`removing_active` é confirmado antes de pedir a remoção. Se a tentativa for
interrompida antes da confirmação, só pode repetir essa verificação com o eTag
guardado; sem eTag fica `removal_unknown`/`invalid_state` para reconciliação.
`active_removed=true` confirma o passo mesmo quando o serviço devolve ausência
do ativo. As repetições seguintes não voltam a apagar por nome, protegendo uma
folha homónima criada depois. Não há promessa de transação distribuída com Graph:
sem resposta suficiente, a operação continua retida.

Antes de POST, `sending_mail` mantém a regra de resultado incerto. Recusas conhecidas
podem repetir segundo a política existente, com os mesmos bytes e correlação.
Após aceitação, recibo e `payload=NULL, retired_at=...` confirmam na mesma transação.
Retêm-se digest, tamanho e identidade para auditoria; o BLOB de mensagem aceite
deixa de ocupar a tabela viva. Mensagens pendentes, recusadas ou incertas conservam
os bytes. Não se promete apagamento seguro de WAL, páginas livres ou backups.
Uma falha na transação do recibo mantém `sending_mail` e os bytes: reconciliação,
nunca repetição automática. Um recibo aceite tem precedência sobre PDF ausente,
snapshot retirado e alteração de configuração; não reenvia.

Impacto de instalação: a fila passa a **formato 2**, também no contrato global;
editing permanece 1. O formato 1 é recusado explicitamente e o validador anterior
recusa o novo. A migração offline e o restauro consistente continuam por implementar;
este checkpoint não se instala sobre uma fila antiga. Backup tem de usar o SQLite
de forma consistente, incluindo a tabela de mensagens e recibos, dentro do futuro
procedimento que coordena todos os stores e ficheiros privados. Copiar só o ficheiro
principal com WAL ativo não constitui um backup aprovado. Os blobs acrescentam até
4 000 000 bytes por mensagem não aceite, mais overhead e histórico SQLite.

Ensaios sintéticos: interrupção real de subprocesso após remoção simulada, recuperação
sem PDF, ausência de eTag, corrupção/oversize/metadados, rollback de snapshot e de
recibo, perda da resposta, repetição de recusa conhecida e tentativa antiga impedida
de guardar/enviar. Os testes de orquestração usam preparação/validação reais e
substituem apenas o envio. A bateria completa e a revisão independente ficam
registadas no índice da colaboração com os respetivos commits.

## Assinatura institucional opcional privada (08/10/2026)

Por omissão, não há imagem de assinatura. Para a ativar, o profissional configura
`FS_MAIL_SIGNATURE_FILE=instituicao.png` e guarda essa imagem em
`FS_APP_DATA_DIR/mail-assets/instituicao.png`. Esta variável aceita **só um nome**,
com letras ASCII, números, ponto, hífen ou underscore; não aceita caminhos, URLs,
subpastas ou streams alternativos. A pasta e o ficheiro não podem resolver para
outro destino por ligação; recursos em `src`, incluindo `web/static`, são recusados.
O diretório de dados deve permanecer fora da raiz pública do servidor, protegido
por permissões locais. A aplicação não instala a imagem, não cria uma rota de
download e não copia a imagem pessoal da demo. Nenhum binário real foi incluído.

Aceita PNG ou JPEG com extensão correspondente, até **512 KiB e 2048 × 1024 píxeis**.
A leitura é limitada; Pillow verifica a estrutura e descodifica os píxeis dentro
destes limites, incluindo a recusa de JPEG truncado. MPO é recusado explicitamente;
não é tratado como JPEG simples. Se outro código ativar globalmente
`ImageFile.LOAD_TRUNCATED_IMAGES`, a assinatura é recusada com erro de configuração,
sem alterar essa flag global nem aceitar uma descodificação permissiva.
O nome transmitido é genérico,
`assinatura-institucional.png` ou `.jpg`; não revela o caminho privado. A imagem
segue inline por CID e um bloco HTML com texto alternativo “Sensorpoint”, largura
máxima de apresentação 792 píxeis e proporção mantida. Não altera o PDF, arquivo
da folha ou checklist privada SADI.

A preparação web chama `validate_signature_config()` através de
`prepare_service_email`, antes do teste de credenciais, claim da operação,
arquivo e enqueue. Imagem configurada ausente/inválida devolve 503, preservando o
rascunho e sem criar uma operação de envio. Esta leitura não fixa o snapshot nem
reserva o ficheiro; o worker repete a validação porque a imagem pode mudar entretanto.
Não está em `validate_config`, que também é usado para revalidar snapshots.

A preparação final acrescenta a imagem uma vez à mensagem fixa e sujeita o **pedido
completo** ao limite de 4 000 000 bytes. Se a imagem configurada faltar, for inválida
ou exceder os limites, o trabalho recebe recusa conhecida antes de remover o ativo.
Não há omissão silenciosa de uma imagem configurada. Para desativar em novas
mensagens, deixar a variável vazia e reiniciar de forma coordenada na instalação.

Snapshots já preparados conservam os bytes originais, mesmo se a configuração
for desativada ou o ficheiro mudar/desaparecer. A revalidação verifica a imagem
guardada, CID, tipo, nome, propriedades inline e presença no corpo sem reler o disco.
Aceita apenas um PDF e, opcionalmente, esta imagem; não permite um segundo PDF
ou anexos adicionais arbitrários. Não há nova versão SQLite nesta etapa: os bytes
ficam no BLOB do formato 2 e a identidade da release continua a impedir workers
de código diferente. Um worker anterior recusaria o envelope com imagem antes de
remover/enviar, em vez de reconstruir a mensagem.

Ensaios usam imagens PNG/JPEG geradas de raiz: desativação/ficheiros removidos após
preparação, bytes iguais, limite codificado incluindo a imagem, caminho inválido,
destino resolvido diferente, recurso público, leitura limitada, imagem falsa,
truncada, demasiado grande ou ilegível, e recusa no fluxo Graph sem remoção do ativo.

### Resposta à primeira revisão da assinatura

O P2 foi reproduzido: imagem ausente/inválida devolvia HTTP200/pending e arquivava
antes da recusa do worker. Os dois ensaios web agora confirmam HTTP503, rascunho
preservado, nenhuma operação persistida/arquivo/trabalho/token; o controlo com
imagem válida mantém a finalização. Outro ensaio remove a imagem depois do
preflight e confirma a recusa no worker antes da remoção remota. Snapshots antigos
sem imagem continuam a enviar os bytes originais se a assinatura for ativada depois,
mesmo sem o ficheiro atual. O modo global permissivo também foi reproduzido e é
recusado explicitamente; a pesquisa em src não encontrou nenhum código que o ative.

Pillow já era dependência da aplicação (Excel, schema SADI, assinaturas). Ficou
fixado em **12.1.1**, a versão efetivamente instalada e testada, em requirements.txt
e requirements-server.txt. Dockerfile instala requirements-server.txt; foi incluído
como contexto na revisão. Isto não equivale a build/ensaio Linux: falta runtime
Docker/Podman/WSL neste computador e o pacote final continua por validar.

## Notificações Teams: implementação por etapas

### Intenção Teams durável — primeira parte da etapa Teams

O filho `teams:{parent_job_id}` é criado na mesma transação IMMEDIATE que guarda
o recibo aceite de email e retira o BLOB da mensagem. Não se chama `enqueue` dentro
da transação nem se abre outra ligação para escrever. O recibo, a intenção e a
retirada confirmam juntos; se falhar a transação após um POST aceite, mantém-se
`sending_mail` para reconciliação, sem repetir o email. Uma interrupção depois do
commit conserva o filho, mesmo antes de o pai ser marcado como concluído.

A conclusão normal, a retoma com recibo aceite e a reconciliação de trabalhos
inativos usam a mesma inserção idempotente. Comparam tipo e payload em JSON
canónico se o ID já existir. Um conflito preserva os dados/comprovativos existentes,
guarda `teams_child_conflict` no pai sem apagar `mail_accepted`, e retém um filho
divergente ainda não concluído como inválido para não o executar. O diagnóstico
original é guardado uma única vez, sem aninhamento a cada tentativa.

A persistência da intenção não depende da disponibilidade do serviço Teams.
Se o filho for executado sem serviço configurado, fica falhado sem retry automático;
o pai continua concluído e o email aceite não é repetido. Resolver a configuração
e repetir esse filho é uma operação administrativa separada, sujeita às regras
de reconciliação da etapa seguinte.

`status` já alterava trabalhos inativos; agora tem admissão de release e transação
explícita. O mutex do worker continua a impedir reconciliar um processo vivo e é
tentado sem bloquear, incluindo quando a consulta ocorre no próprio worker.
`_claim_next` reutiliza a sua transação existente. `summary` permanece só de leitura
e acrescenta contagens `teams_missing` e `teams_conflicts`, disponíveis no diagnóstico
da API/admin e no resumo do worker. Pais históricos já concluídos sem filho são
assinalados, sem criar ou enviar automaticamente notificações antigas.

Na alteração da intenção persistente, o esquema manteve-se queue=2. A proteção
abaixo eleva-o a queue=3 para impedir a execução por workers anteriores.

Testes sintéticos usam processos que terminam antes/depois do commit do recibo,
comprovativo externo simulado persistido, um único filho, zero reenvios de email,
serviço ausente, reconciliação concorrente, admissão de versão e colisão divergente.
O ensaio de falha posterior ao aceite foi movido do antigo helper de enqueue para
a fronteira de conclusão, preservando a verificação do recibo durante retry/guard.
Os testes de busy do heartbeat/worker dirigem a falha à thread respetiva, porque
as consultas de status também têm agora admissão e podiam consumir a falha simulada.

### Resultado do POST Teams e repetição limitada

A fila valida configuração/guardas/payload antes de gravar sending_teams. Esta
fase é confirmada em SQLite, com synchronous=FULL, antes de entrar no transporte.
Uma aceitação 2xx produz teams_accepted e accepted=true, gravados na mesma
transação que a conclusão. Se essa transação falhar, permanece sending_teams:
não há prova durável de aceitação nem permissão para repetir.

Timeout, falha de ligação, resposta inválida, 3xx, 408, 425, 5xx ou outra exceção
depois de começar o pedido deixam teams_unknown. Os redirecionamentos automáticos
do transporte urllib estão desativados. Stale sending_teams também é retido
como desconhecido, nunca reclamado para envio. Antes do POST, uma interrupção
conhecida fica parada para revisão manual. A fila e as duas rotas de retry,
incluindo o painel administrativo, recusam repetir resultados incertos.

Rejeições HTTP 4xx, exceto 408/425, são conhecidas e terminais. Só HTTP429 com
Retry-After explícito e válido permite retry automático: segundos ou data HTTP
com fuso, até 24h. Um prazo superior exige revisão manual; nunca é encurtado.
O código HTTP e retry_not_before absoluto são persistidos em teams_rejected,
mesmo acima de24h ou no esgotamento de tentativas. O painel mostra o prazo e
retira a ação de repetição enquanto não terminar; a fila volta a validar o prazo
sob transação em cada retry. Metadados de prazo inválidos ficam em quarentena.
O prazo indicado não é limitado pelos anteriores cinco minutos da fila.
FS_TEAMS_JOB_MAX_ATTEMPTS tem valor inicial 3, mínimo 1; o limite é persistido
em cada filho, incluindo os criados atomicamente com o email. Após esgotá-lo,
uma rejeição conhecida pode ser repetida por ação administrativa auditada.
Essa ação também respeita retry_not_before; não constitui exceção ao throttling.

As mensagens de erro não incluem o URL assinado, corpo ou texto da exceção
externa. O cabeçalho de correlação ajuda o diagnóstico, mas não assegura
idempotência do Workflow. Uma resposta 202 comprova aceitação para processamento,
não publicação no canal: [semântica assíncrona Microsoft](https://learn.microsoft.com/en-us/power-automate/guidance/coding-guidelines/asychronous-flow-pattern).
Não se consultou nem alterou qualquer Workflow real.

Os testes usam transportes sintéticos, morte abrupta de subprocesso após o POST
simulado, falha da transação do recibo/conclusão, reabertura sem serviço,
rejeições HTTP e deadlines/limites, quarentena e bloqueio nas duas rotas admin.
Não há migração implícita de queue2; os validadores antigos/novos recusam os
formatos incompatíveis. Migração e restauro deverão reter tentativas antigas
sem resultado comprovado, pois a base restaurada não desfaz um efeito remoto.

Resposta à primeira revisão Teams: reproduzidos quatro casos do P3 de prazo
manual antecipado (700s/48h e duas rotas), corrigidos conforme acima. Acrescentados
ensaios de falha de checkpoint após HTTP400/503, prazo corrompido/overflow e CLI
activate a recusar tanto contrato queue2 como base queue2, sem mudar época/SHA.
O CLI é fornecido como contexto; não foi alterado nem migrada qualquer base real.
O contexto frontend desta revisão serve apenas a pergunta de desenho da etapa5;
mail-job-monitor e document-editor continuam sem alterações desta funcionalidade.

### Etapas seguintes

Sanitizar o diagnóstico restante; fluxos completos, identidade/admin, migração,
backup/restauro e pacote final. Esta alteração não aprova essas funcionalidades,
a instalação ou o candidato completo.
