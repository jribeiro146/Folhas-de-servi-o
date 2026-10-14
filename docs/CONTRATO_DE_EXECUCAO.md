# Contrato de execução e troca de versão

## Estado desta alteração

A API `RuntimeContract` foi aprovada pelo Claude no commit `0c4c763`. A alteração atual liga a admissão à web, ao worker independente, à fila e à atualização Graph; aguarda a sua própria revisão. Não executar contra dados operacionais para testar. Não foi feita qualquer ativação no servidor.

O contrato fica em `runtime-contract.sqlite3` num disco local persistente, partilhado pelos processos do mesmo host. Regista SHA-256 do código ativo, época incremental e versões dos contratos de armazenamento (`editing`, `queue`, `bundle`, `sadi`, `inventory`). Estes números identificam conjuntos de formatos suportados, não substituem as versões de documentos nem provam que cada registo está válido. A inspeção dos esquemas reais e os marcadores por base fazem parte da integração/preparação seguintes.

## API e condições

- `initialize_empty()` é explícito e só aceita uma pasta nova/vazia; uma repetição aceita apenas um contrato válido da mesma versão. Dados ou diretórios existentes sem contrato são recusados, nunca interpretados como instalação vazia. Uma base inválida não é substituída. Um temporário abandonado exige inspeção.
- `operation()` verifica contrato e SHA, mantendo uma transação de leitura durante **toda a operação**, incluindo efeitos externos, finalização e gravações posteriores. Várias operações da mesma versão podem coexistir. Abrange pedidos, inicialização de serviços, worker e threads de atualização; uma verificação isolada antes de uma escrita não é suficiente.
- `activate(next_sha, next_formats=...)` só aceita o SHA anterior esperado pelo objeto e formatos de destino compatíveis, exige acesso exclusivo, aumenta a época e altera apenas o código ativo. Se houver operações em curso, recusa rapidamente. Depois da troca, objetos/processos antigos já não entram em novas operações. A chamada é uma API interna de preparação, não um endpoint administrativo nem uma migração automática. O futuro CLI deve exigir artefacto e preparação compatíveis antes de a invocar.
- `inspect()` mostra os identificadores mesmo quando os formatos persistidos são diferentes, desde que a estrutura do contrato seja legível. `matches_release` e `formats_compatible` distinguem as duas incompatibilidades. O diagnóstico não autoriza operações; estrutura inválida ou versão desconhecida da própria base de controlo continuam a ser recusadas. Não lê dados de clientes.
- Base ausente, corrompida, contrato futuro, formatos inválidos ou modo WAL nesta base de controlo recusam entrada e ativação. O processo que morre liberta os bloqueios do SO; uma exceção normal também fecha a transação. Não apagar/substituir manualmente esta base nem os ficheiros de bloqueio enquanto existirem processos.

Só a base de controlo usa `journal_mode=DELETE`: a transação de leitura permite leitores concorrentes e impede a transação exclusiva de ativação. As bases operacionais podem continuar em WAL. Fundamento: [bloqueios SQLite](https://www.sqlite.org/lockingv3.html) e [transações SQLite](https://www.sqlite.org/lang_transaction.html). O timeout é limitado; não se força a expulsão de uma operação em curso. Uma falha na operação propagada pelo chamador não é reclassificada como erro do contrato.

## Limites e transição

Código antigo que não implementa este contrato não participa nos bloqueios. A primeira passagem de uma versão legada exige parar todos os escritores e preparar os dados com backup consistente; a API `initialize_empty()` não adota esse estado. Restauro/rollback não desfaz email/Teams/Graph já efetuados. Consultar [migração M06](MIGRACAO_OFFLINE.md) e [backup/restauro M07](BACKUP_RESTAURO.md) para os comandos offline e respetivos limites.

Os testes usam duas ligações e também processos reais separados, com dados fictícios e rede bloqueada. Confirmam concorrência compatível, recusa de ativação com operação em curso, libertação após saída/crash, bloqueio de processos antigos, preservação de dados não marcados e recusa de formatos futuros/corrompidos. O checkpoint original foi validado em Windows/Python 3.13; os ensaios Linux posteriores, incluindo contrato e recusa de versões, estão em [VALIDACAO_CI.md](VALIDACAO_CI.md). Esta fundação, por si só, não permite declarar o plano nem a aplicação prontos para produção.

## Histórico: resposta à primeira revisão da API e desenho da integração

Os três achados foram reproduzidos e corrigidos: `inspect` separa estrutura de compatibilidade; há verificação de diretório antigo antes de criar o lock e nova verificação com exclusão; o subprocesso usa cwd explícito, ensaia pytest noutro diretório e mostra stderr se falhar. O lock persistente nunca é apagado para contornar contenção. Num arranque concorrente, a existência do lock permite procurar exclusão antes da segunda verificação; não se interpreta um temporário de outro inicializador como dados antigos definitivos.

Cada operação tem a sua ligação privada, sem acesso concorrente pelo chamador. `check_same_thread=False` permite terminar uma transferência **sequencial** de ownership entre threads sem deixar o bloqueio retido; o teste correspondente reproduziu a falha com a guarda original de Python e passou após a correção. A ligação é sempre fechada, mesmo se o rollback falhar. Não partilhar um context manager em execução simultânea nem herdar uma ligação através de fork. A integração normal abrirá e fechará na mesma thread:

- Web: envolver a inicialização dos serviços; em cada pedido entrar antes de hooks que recuperam finalizações ou escrevem estado e sair no teardown, incluindo erros. Nunca transportar a ligação de um pedido para outra thread.
- Worker: entrada própria na thread que faz claim e execução, conservada até ao checkpoint/estado final. O heartbeat terá entrada própria para cobrir uma escrita que ainda esteja a terminar quando a thread principal sinaliza paragem. Enqueue e retry também terão proteção quando invocados fora da web.
- Atualização Graph: entrada própria na thread `_run`, até à publicação do estado de conclusão. Se a versão mudar entre o agendamento e o arranque da thread, esta liberta o mutex e recusa iniciar trabalho, sem escrever com código antigo; a nova versão recupera o marcador interrompido. Os métodos síncronos que alteram o estado também ficam abrangidos.

Na revisão da API, os consumidores foram fornecidos apenas como contexto. A implementação descrita na secção seguinte concretiza estes pontos e recusa dados dentro de `src` antes do cálculo da identidade; uma cópia/assinatura do código não pode percorrer dados de execução.

### POSIX, backup e journal

Nos processos web/worker, aceder a `runtime-contract.sqlite3` apenas pela mesma biblioteca `sqlite3`: não usar open/read/close, hashes ou `copytree` sobre a base durante operações. Em POSIX, fechar outro descritor direto pode cancelar bloqueios do mesmo processo; ligações feitas pela mesma biblioteca SQLite são coordenadas pelo seu VFS. Referência: [SQLite, problemas de bloqueios e corrupção](https://www.sqlite.org/howtocorrupt.html#_posix_advisory_locks_canceled_by_a_separate_thread_doing_close_).

A ferramenta `tools/backup_runtime.py` corre num processo separado, com escritores parados/coordenados e uso da API SQLite; não existe endpoint de backup na web/worker. O contrato é serializado pela ligação que detém a exclusão. Não acrescentar um endpoint que copie ou faça hash desta base dentro dos processos de aplicação. Após restauro, o formato adicional `restore_hold` bloqueia admissão/ativação até à libertação offline auditada; isto também impede operações novas relativas a efeitos posteriores ausentes da cópia.

`runtime-contract.sqlite3-journal` pode ser necessário para recuperar uma atualização interrompida. Não o apagar, renomear, trocar com outro backup ou copiar apenas a base após um crash. Num backup offline por ficheiros, preservar conjuntamente base e journal correspondente; a recuperação normal pertence ao SQLite. O teste de crash força páginas para disco, confirma um journal com cabeçalho válido, mata apenas o subprocesso sintético e verifica que a API recupera SHA/época anteriores. Há ainda testes de recusa enquanto a transação exclusiva está aberta, inicialização simultânea e repetição sem mudar a época.

Linux/Docker continuam indisponíveis neste computador. Os testes entre processos foram executados em Windows; o ensaio Linux de bloqueios, SIGKILL e symlinks permanece obrigatório antes da declaração de prontidão Linux, não é substituído por esta revisão estática nem pelos testes Windows.

## Resposta à segunda revisão

Os achados anteriores ficaram resolvidos, sem contexto em falta. Os dois P3 adicionais foram tratados: falhas SQLite da atualização/commit de `activate` são convertidas em `ContractError` (ou `ContractBusy`), com mensagem que manda consultar o contrato antes de repetir; um resultado de commit não confirmado não é apresentado como sucesso nem como prova universal de rollback. Uma falha sintética antes do commit, com SQLITE_FULL, confirma que SHA/época anteriores se mantêm. O teste do journal agora compara também os bytes da base antes da transação e depois de matar o escritor, antes de qualquer ligação de recuperação: os bytes diferem, o journal existe e a API recupera o estado anterior. A comparação direta acontece apenas sem ligações SQLite abertas nesse processo e depois da morte do filho; não foi usada a sugestão de abrir/fechar o ficheiro diretamente no processo escritor, porque isso quebraria bloqueios POSIX.

Também se confirma que ativar o mesmo SHA não aumenta a época. Na revisão da API, a biblioteca estava isolada dos consumidores; a integração repete a bateria completa.

## Integração nos consumidores — 08/10/2026

`resolve_runtime` calcula uma única identidade no arranque, depois de recusar pastas de dados/cache/logs/Excel dentro de `src`. Web e worker verificam o contrato antes de construir serviços persistentes. Não há inicialização automática de dados existentes nem bypass por variável de ambiente. Contrato ausente, futuro ou de outra versão recusa o arranque. A preparação/migração explícita e os marcadores de cada armazenamento são a alteração seguinte; este checkout intermédio ainda não é uma entrega instalável.

A web entra no contrato antes dos hooks de autenticação/recuperação e conserva a entrada até ao teardown do pedido, incluindo erros. Uma recusa devolve HTTP 503, `Retry-After: 2`, `Cache-Control: no-store` e um código estável. Streaming de respostas pode continuar depois do teardown, mas não executa gravações nem serviços externos nesse estágio. Atualizar a versão exige parar novas entradas e esperar pelas operações em curso, em vez de forçar a troca.

Fila: a inicialização, enqueue, retry, worker e heartbeat têm admissão. O worker conserva-a durante claim, operações externas, checkpoints e finalização. Cada heartbeat abre a própria entrada, cobrindo também uma thread que ainda acabe depois do join com timeout. Um worker assíncrono recusado limpa o indicador local e não reagenda; a execução síncrona propaga o erro e o comando termina com código 1. O resumo da fila continua disponível como leitura. Não se alterou a política dos comprovativos de mail nem dos resultados incertos.

Atualização Graph: request/status têm admissão pois também recuperam estado interrompido. A thread abre a sua própria entrada; se começar depois da troca, liberta o mutex e não sincroniza nem escreve estado antigo. O processo da nova versão deteta e marca a interrupção. O bloqueio normal dura até à publicação da conclusão e libertação do mutex.

Injeção de serviços e de contratos nos factories é uma interface Python de teste, não recebida de HTTP/configuração do utilizador. Os factories operacionais fornecem obrigatoriamente o contrato aos serviços que constroem. Serviços de baixo nível continuam a aceitar ausência de contrato para testes isolados; não são pontos de entrada operacionais. `tools/test_runtime.configure()` prepara apenas a sua pasta nova e fictícia antes de importar a aplicação; fixtures com pastas próprias fazem o mesmo explicitamente. Nunca adotar dados existentes para fazer passar um teste.

Os testes de integração exercitam HTTP, erro HTTP, arranque sem contrato, worker antigo, ausência de claim/envio após troca, efeito externo em curso, heartbeat atrasado, atualização em curso e thread agendada antes da troca. Os testes anteriores da API cobrem processos/crash/journal. A aprovação desta integração não cobre Linux nem substitui preparação/migração e ensaios finais do plano.

### Correções da primeira revisão da integração

Reproduzidos os dois P2 em cinco cenários. `admit_contract` reutiliza a entrada exterior na mesma thread, PID, caminho resolvido e SHA. Uma entrada interior não abre nova transação durante PENDING nem liberta a entrada exterior. Outras threads abrem a sua própria ligação; outro SHA não reutiliza a entrada. O contexto é removido mesmo se a operação falhar. A API primitiva continua sem reentrância; todos os consumidores operacionais usam o novo wrapper.

`ContractBusy` é transitório: heartbeat continua no ciclo seguinte; worker assíncrono agenda nova tentativa com um segundo de espera; `--watch` adia o ciclo (incluindo construção da fila). Uma execução única continua a devolver código 1 se não conseguiu processar. Refresh tenta a admissão durante até três segundos; se a ocupação persistir, liberta o mutex e a observação seguinte assinala interrupção, permitindo nova atualização explícita. Incompatibilidade definitiva mantém recusa sem reagendar.

Os testes distinguem ambos os erros e verificam recuperação. O ensaio PENDING usa exclusão SQLite real com timeout de um segundo para obter uma janela determinística; não altera a base. A ativação normal continua com timeout de 0,25 s. Não se afirma que uma única tentativa de ativação produza sempre a corrida; entradas repetidas/temporização podem torná-la visível.

Contexto solicitado: `register_admin` executa retry/refresh de forma síncrona no pedido, passando pelo hook da aplicação. As threads são as protegidas da fila/coordenador. `passenger_wsgi.py`, `src/main.py` e worker configuram logging antes do factory; por isso o próprio validador de logging recusa diretórios dentro de src antes de mkdir/open (regressão sintética). No lançador local, a chamada adicional a `ensure_directories` também tem admissão. Não foram executados os pontos de entrada com configuração operacional.

### Correção da segunda revisão da integração

A segunda revisão confirmou os P2 e encontrou um P3: logging predefinido criava `APP_DATA_DIR/logs` antes da recusa de uma instalação não preparada. A ordem descrita no parágrafo anterior foi alterada: os três pontos de entrada passam primeiro pelo factory e só configuram logging de ficheiro depois, dentro da admissão. Erros anteriores à preparação saem por stderr/log do supervisor; não criam dados locais. Não se relaxou `initialize_empty()` nem se apagam diretórios para contornar a proteção. Três subprocessos sintéticos reproduziram a criação indevida, depois confirmaram recusa sem criar a pasta, preparação explícita e HTTP 200 após preparar.

Contexto JS incluído: autosave conserva o payload/idempotência e a recuperação local, desativa os botões de commit e agenda nova tentativa quando recebe erro; um 503 não é classificado como revogação. Heartbeat não apaga a sessão nem o conteúdo por estes códigos. Rascunho/envio apresentam a mensagem recebida sem `markCommitted`, confirmação de fotos ou navegação; a nova regressão de rascunho verifica os dois códigos 503. A melhoria geral de feedback do plano mantém-se posterior. Não há alegação de ensaio visual nesta etapa.

Há testes adicionais de 503/no-store/Retry-After em HTML e recurso estático, sem executar hooks posteriores, e de refresh ocupado para lá de três segundos seguido de recuperação explícita. Os testes prévios de exclusão do worker cobrem timestamp aparentemente expirado sem segundo claim enquanto o mutex está ocupado; o diagnóstico detalhado de heartbeat/readiness continua na etapa prevista do plano.


## Entrega identificada — M13

O contrato passa a estar ligado também às ferramentas operacionais e aos ficheiros
de entrega cobertos pela identidade. resolve_runtime recusa armazenamento dentro
de src ou tools antes do fingerprint e exige manifesto nas factories operacionais.
A exceção é o perfil sintético completo do lançador, nunca produção. A leitura
inspect é diagnóstica e não prepara dados. Esta ampliação muda source_sha256:
atualizar exige ativação coordenada, sem recriar o contrato sobre dados existentes.
Consultar [Identidade e pacote](IDENTIDADE_DA_VERSAO.md) para construir do SHA limpo,
verificar o ZIP e conhecer os limites da prova de integridade.
