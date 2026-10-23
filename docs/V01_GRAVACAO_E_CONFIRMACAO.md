# Gravação automática e confirmação da folha

Ao guardar um rascunho, finalizar ou cancelar, a interface aguarda a resposta
de uma gravação automática já em curso antes de escolher a revisão da operação.
Durante essa preparação não inicia outra gravação automática. Isto evita um
conflito provocado pela própria gravação da mesma página.

Uma resposta não confirmada, conflito, retirada de acesso ou suspensão da página
impede essa confirmação. Os dados locais continuam disponíveis pelo mecanismo
existente de recuperação. A espera termina ao fim de 30 segundos; não cancela nem
repete o pedido que ainda pode estar a ser processado. Depois da confirmação do
servidor pode tentar-se novamente. Ao sair durante a preparação, a libertação da
sessão não tenta escrever o documento com uma revisão antiga.

O coordenador conserva a preparação até o consumidor chamar `setCommitPending`
ou libertá-la no seu `finally`. Guardar, finalizar e cancelar cumprem esse
contrato. A ação fica reservada desde o primeiro clique, antes de aguardar uma
assinatura ou os metadados. Cliques seguintes em guardar, finalizar ou cancelar
não iniciam outra ação. Uma tentativa que não conseguiu preparar a operação
não pode libertar a preparação de outra. O servidor continua a verificar revisão, sessão e idempotência; não
se contornam conflitos reais nem operações de resultado incerto.
As assinaturas SADI usam os metadados da sessão ativa sem reservar uma gravação:
o respetivo pedido só devolve a prova digital do conteúdo submetido, sem gravar
o documento ou avançar a revisão. A prova é conservada pela gravação normal da
folha. Uma assinatura não pode deixar essa gravação suspensa nem libertar a
preparação de outra operação.

Depois de uma recusa explícita por conflito, a escolha «Usar servidor» permite
guardar/finalizar/cancelar sem obrigar a editar outro campo. «Usar este dispositivo»
grava com a revisão atual e uma chave nova. A escolha explícita num conflito
detetado ao retomar uma página também substitui a referência do autosave anterior,
depois de consultar a revisão do servidor. Sem essa resolução explícita, falhas
desconhecidas conservam a referência anterior.

Enquanto o painel de conflito aguarda essa escolha, regressar à rede, editar
campos ou suspender/sair da página não grava sobre a versão do servidor.
As alterações ficam na recuperação local; o fecho apenas liberta a sessão.
A recuperação local também conserva a indicação de conflito. Ao recarregar ou
reabrir, uma cópia diferente volta a pedir a escolha, mesmo quando tem o número
da revisão atual. A escolha explícita retira essa indicação; recuperações sem
conflito mantêm o comportamento habitual. O campo é opcional no IndexedDB e não
muda bases/configuração do servidor. Cópias antigas sem esse campo não permitem
identificar retroativamente um conflito que uma versão antiga não registou.
Na abertura com conflito, o formulário mostra a versão do servidor apenas para
consulta. Os botões de escolha ficam fora do formulário e conservam a cópia
original do dispositivo até à decisão. Eventos atrasados não podem substituir
essa cópia. Uma aba que esteja a observar outra aba ativa também não pode decidir
ou apagar a recuperação partilhada.
Quando a aba editora fecha, a observadora volta a consultar o servidor e a
recuperação partilhada antes de permitir uma escolha. Uma cópia descartada deixa
de ser oferecida; um conflito ainda existente mantém a versão local mais recente.
Se a consulta falhar, continua em modo de consulta com a opção normal de voltar
a tentar. O fecho durante uma verificação obriga a consultar novamente os dados.
Na comparação, uma lista de materiais vazia equivale à linha de preenchimento
com quantidade, referência e descrição vazias; uma checklist ausente equivale
à lista vazia, depois da verificação da recuperação no servidor. Isto evita
pedir uma escolha apenas porque o servidor normalizou campos vazios ao fechar.
Não altera a cópia original. Quantidades (incluindo zero), textos, campos
inesperados e locais de manutenção com conteúdo continuam a distinguir versões.

Verificação de 23/10/2026: os três percursos reproduziam a captura prematura da
revisão. Os testes JavaScript cobrem sucesso, falha, conflito, revogação, espera
limitada, suspensão e libertação após falha do cancelamento. A bateria completa
atual passou: 304 testes JavaScript, incluindo resolução explícita após falha de
rede, regresso à rede/edição/fecho antes da escolha, marcador de conflito após
reabrir, proteção da cópia original na pré-visualização, escolhas nas duas abas,
falha de consulta e fecho durante verificação. A função real de assinatura foi
testada em sucesso, recusa, falha de rede, concorrência entre assinaturas e com
autosave/preparação de rascunho. Oito regressões adicionais executam os consumidores
reais de guardar/finalizar/cancelar com o coordenador real: cliques sobrepostos
durante uma assinatura, rascunho durante outra operação e preparação recusada
que pertence a outra ação. Os oito casos falharam antes desta correção.
Treze casos adicionais cobrem as representações vazias na reabertura/retoma e
a proteção das diferenças reais; seis reproduziram o conflito desnecessário
antes da correção. O caso foi observado no ensaio integrado depois de uma
sessão expirar e voltar a entrar, com o mesmo texto preservado nas duas versões.
Estes ensaios usam dados fictícios e transportes em memória; não provam
envios reais ou a execução Linux. Não acrescenta configuração, permissões ou
alterações de formato aos dados. A validação Linux e a instalação são etapas
distintas.
