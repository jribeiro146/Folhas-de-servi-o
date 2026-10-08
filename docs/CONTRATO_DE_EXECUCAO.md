# Contrato de execução e troca de versão

## Estado desta alteração

Esta etapa introduz e ensaia a API `RuntimeContract`; **ainda não está ligada à web, à fila ou à atualização Graph**. A integração é a alteração seguinte, antes de mudar funcionalidades ou formatos. Não executar contra dados operacionais para testar. Não foi feita qualquer ativação no servidor.

O contrato fica em `runtime-contract.sqlite3` num disco local persistente, partilhado pelos processos do mesmo host. Regista SHA-256 do código ativo, época incremental e versões dos contratos de armazenamento (`editing`, `queue`, `bundle`, `sadi`, `inventory`). Estes números identificam conjuntos de formatos suportados, não substituem as versões de documentos nem provam que cada registo está válido. A inspeção dos esquemas reais e os marcadores por base fazem parte da integração/preparação seguintes.

## API e condições

- `initialize_empty()` é explícito e só aceita uma pasta nova/vazia; uma repetição aceita apenas um contrato válido da mesma versão. Dados ou diretórios existentes sem contrato são recusados, nunca interpretados como instalação vazia. Uma base inválida não é substituída. Um temporário abandonado exige inspeção.
- `operation()` verifica contrato e SHA, mantendo uma transação de leitura durante **toda a operação**, incluindo efeitos externos, finalização e gravações posteriores. Várias operações da mesma versão podem coexistir. A integração terá de abranger pedidos, inicialização de serviços, worker e threads de atualização; uma verificação isolada antes de uma escrita não é suficiente.
- `activate(next_sha)` só aceita o SHA anterior esperado pelo objeto, exige acesso exclusivo, aumenta a época e altera apenas o código ativo. Se houver operações em curso, recusa rapidamente. Depois da troca, objetos/processos antigos já não entram em novas operações. A chamada é uma API interna de preparação, não um endpoint administrativo nem uma migração automática. O futuro CLI deve exigir artefacto e preparação compatíveis antes de a invocar.
- `inspect()` mostra os identificadores e se o processo corresponde à versão ativa. Não lê dados de clientes.
- Base ausente, corrompida, contrato futuro, formatos inválidos ou modo WAL nesta base de controlo recusam entrada e ativação. O processo que morre liberta os bloqueios do SO; uma exceção normal também fecha a transação. Não apagar/substituir manualmente esta base nem os ficheiros de bloqueio enquanto existirem processos.

Só a base de controlo usa `journal_mode=DELETE`: a transação de leitura permite leitores concorrentes e impede a transação exclusiva de ativação. As bases operacionais podem continuar em WAL. Fundamento: [bloqueios SQLite](https://www.sqlite.org/lockingv3.html) e [transações SQLite](https://www.sqlite.org/lang_transaction.html). O timeout é limitado; não se força a expulsão de uma operação em curso. Uma falha na operação propagada pelo chamador não é reclassificada como erro do contrato.

## Limites e transição

Código antigo que não implementa este contrato não participa nos bloqueios. A primeira passagem de uma versão legada exige parar todos os escritores e preparar os dados com backup consistente; a API `initialize_empty()` não adota esse estado. Restauro/rollback não desfaz email/Teams/Graph já efetuados. O comando e ensaio de migração permanecem pendentes.

Os testes usam duas ligações e também processos reais separados, com dados fictícios e rede bloqueada. Confirmam concorrência compatível, recusa de ativação com operação em curso, libertação após saída/crash, bloqueio de processos antigos, preservação de dados não marcados e recusa de formatos futuros/corrompidos. A validação atual é Windows/Python 3.13; Linux continua por executar. Esta fundação, por si só, não permite declarar o plano nem a aplicação prontos para produção.
