# Contrato de execução e troca de versão

## Estado desta alteração

Esta etapa introduz e ensaia a API `RuntimeContract`; **ainda não está ligada à web, à fila ou à atualização Graph**. A integração é a alteração seguinte, antes de mudar funcionalidades ou formatos. Não executar contra dados operacionais para testar. Não foi feita qualquer ativação no servidor.

O contrato fica em `runtime-contract.sqlite3` num disco local persistente, partilhado pelos processos do mesmo host. Regista SHA-256 do código ativo, época incremental e versões dos contratos de armazenamento (`editing`, `queue`, `bundle`, `sadi`, `inventory`). Estes números identificam conjuntos de formatos suportados, não substituem as versões de documentos nem provam que cada registo está válido. A inspeção dos esquemas reais e os marcadores por base fazem parte da integração/preparação seguintes.

## API e condições

- `initialize_empty()` é explícito e só aceita uma pasta nova/vazia; uma repetição aceita apenas um contrato válido da mesma versão. Dados ou diretórios existentes sem contrato são recusados, nunca interpretados como instalação vazia. Uma base inválida não é substituída. Um temporário abandonado exige inspeção.
- `operation()` verifica contrato e SHA, mantendo uma transação de leitura durante **toda a operação**, incluindo efeitos externos, finalização e gravações posteriores. Várias operações da mesma versão podem coexistir. A integração terá de abranger pedidos, inicialização de serviços, worker e threads de atualização; uma verificação isolada antes de uma escrita não é suficiente.
- `activate(next_sha, next_formats=...)` só aceita o SHA anterior esperado pelo objeto e formatos de destino compatíveis, exige acesso exclusivo, aumenta a época e altera apenas o código ativo. Se houver operações em curso, recusa rapidamente. Depois da troca, objetos/processos antigos já não entram em novas operações. A chamada é uma API interna de preparação, não um endpoint administrativo nem uma migração automática. O futuro CLI deve exigir artefacto e preparação compatíveis antes de a invocar.
- `inspect()` mostra os identificadores mesmo quando os formatos persistidos são diferentes, desde que a estrutura do contrato seja legível. `matches_release` e `formats_compatible` distinguem as duas incompatibilidades. O diagnóstico não autoriza operações; estrutura inválida ou versão desconhecida da própria base de controlo continuam a ser recusadas. Não lê dados de clientes.
- Base ausente, corrompida, contrato futuro, formatos inválidos ou modo WAL nesta base de controlo recusam entrada e ativação. O processo que morre liberta os bloqueios do SO; uma exceção normal também fecha a transação. Não apagar/substituir manualmente esta base nem os ficheiros de bloqueio enquanto existirem processos.

Só a base de controlo usa `journal_mode=DELETE`: a transação de leitura permite leitores concorrentes e impede a transação exclusiva de ativação. As bases operacionais podem continuar em WAL. Fundamento: [bloqueios SQLite](https://www.sqlite.org/lockingv3.html) e [transações SQLite](https://www.sqlite.org/lang_transaction.html). O timeout é limitado; não se força a expulsão de uma operação em curso. Uma falha na operação propagada pelo chamador não é reclassificada como erro do contrato.

## Limites e transição

Código antigo que não implementa este contrato não participa nos bloqueios. A primeira passagem de uma versão legada exige parar todos os escritores e preparar os dados com backup consistente; a API `initialize_empty()` não adota esse estado. Restauro/rollback não desfaz email/Teams/Graph já efetuados. O comando e ensaio de migração permanecem pendentes.

Os testes usam duas ligações e também processos reais separados, com dados fictícios e rede bloqueada. Confirmam concorrência compatível, recusa de ativação com operação em curso, libertação após saída/crash, bloqueio de processos antigos, preservação de dados não marcados e recusa de formatos futuros/corrompidos. A validação atual é Windows/Python 3.13; Linux continua por executar. Esta fundação, por si só, não permite declarar o plano nem a aplicação prontos para produção.

## Resposta à primeira revisão Claude e integração prevista

Os três achados foram reproduzidos e corrigidos: `inspect` separa estrutura de compatibilidade; há verificação de diretório antigo antes de criar o lock e nova verificação com exclusão; o subprocesso usa cwd explícito, ensaia pytest noutro diretório e mostra stderr se falhar. O lock persistente nunca é apagado para contornar contenção. Num arranque concorrente, a existência do lock permite procurar exclusão antes da segunda verificação; não se interpreta um temporário de outro inicializador como dados antigos definitivos.

Cada operação tem a sua ligação privada, sem acesso concorrente pelo chamador. `check_same_thread=False` permite terminar uma transferência **sequencial** de ownership entre threads sem deixar o bloqueio retido; o teste correspondente reproduziu a falha com a guarda original de Python e passou após a correção. A ligação é sempre fechada, mesmo se o rollback falhar. Não partilhar um context manager em execução simultânea nem herdar uma ligação através de fork. A integração normal abrirá e fechará na mesma thread:

- Web: envolver a inicialização dos serviços; em cada pedido entrar antes de hooks que recuperam finalizações ou escrevem estado e sair no teardown, incluindo erros. Nunca transportar a ligação de um pedido para outra thread.
- Worker: entrada própria na thread que faz claim e execução, conservada até ao checkpoint/estado final. O heartbeat terá entrada própria para cobrir uma escrita que ainda esteja a terminar quando a thread principal sinaliza paragem. Enqueue e retry também terão proteção quando invocados fora da web.
- Atualização Graph: entrada própria na thread `_run`, até à publicação do estado de conclusão. Se a versão mudar entre o agendamento e o arranque da thread, esta liberta o mutex e recusa iniciar trabalho, sem escrever com código antigo; a nova versão recupera o marcador interrompido. Os métodos síncronos que alteram o estado também ficam abrangidos.

Os consumidores atuais são fornecidos como contexto para essa alteração seguinte. Nenhum destes pontos é apresentado como já integrado. Configuração com dados dentro de `src` terá de ser recusada antes do cálculo da identidade; uma cópia/assinatura do código não pode percorrer dados de execução.

### POSIX, backup e journal

Nos processos web/worker, aceder a `runtime-contract.sqlite3` apenas pela mesma biblioteca `sqlite3`: não usar open/read/close, hashes ou `copytree` sobre a base durante operações. Em POSIX, fechar outro descritor direto pode cancelar bloqueios do mesmo processo; ligações feitas pela mesma biblioteca SQLite são coordenadas pelo seu VFS. Referência: [SQLite, problemas de bloqueios e corrupção](https://www.sqlite.org/howtocorrupt.html#_posix_advisory_locks_canceled_by_a_separate_thread_doing_close_).

Pesquisa do código atual: a identidade lê apenas `src`, entrada WSGI e requisitos; as cópias existentes são de bundles de documentos, não de todo `APP_DATA_DIR`. Não existe ainda rotina de backup de todo o estado nos processos web/worker. A futura ferramenta de backup será um processo separado, com escritores parados/coordenados e uso da API SQLite; não acrescentar um endpoint que copie ou faça hash desta base dentro dos processos de aplicação.

`runtime-contract.sqlite3-journal` pode ser necessário para recuperar uma atualização interrompida. Não o apagar, renomear, trocar com outro backup ou copiar apenas a base após um crash. Num backup offline por ficheiros, preservar conjuntamente base e journal correspondente; a recuperação normal pertence ao SQLite. O teste de crash força páginas para disco, confirma um journal com cabeçalho válido, mata apenas o subprocesso sintético e verifica que a API recupera SHA/época anteriores. Há ainda testes de recusa enquanto a transação exclusiva está aberta, inicialização simultânea e repetição sem mudar a época.

Linux/Docker continuam indisponíveis neste computador. Os testes entre processos foram executados em Windows; o ensaio Linux de bloqueios, SIGKILL e symlinks permanece obrigatório antes da declaração de prontidão Linux, não é substituído por esta revisão estática nem pelos testes Windows.
