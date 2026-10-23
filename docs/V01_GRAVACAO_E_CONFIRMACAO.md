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
contrato. O servidor continua a verificar revisão, sessão e idempotência; não
se contornam conflitos reais nem operações de resultado incerto.

Depois de uma recusa explícita por conflito, a escolha «Usar servidor» permite
guardar/finalizar/cancelar sem obrigar a editar outro campo. «Usar este dispositivo»
grava com a revisão atual e uma chave nova. A escolha explícita num conflito
detetado ao retomar uma página também substitui a referência do autosave anterior,
depois de consultar a revisão do servidor. Sem essa resolução explícita, falhas
desconhecidas conservam a referência anterior.

Enquanto o painel de conflito aguarda essa escolha, regressar à rede, editar
campos ou suspender/sair da página não grava sobre a versão do servidor.
As alterações ficam na recuperação local; o fecho apenas liberta a sessão.

Verificação de 23/10/2026: os três percursos reproduziam a captura prematura da
revisão. Os testes JavaScript cobrem sucesso, falha, conflito, revogação, espera
limitada, suspensão e libertação após falha do cancelamento. A bateria completa
inicial passou: 240 testes; a revisão acrescentou oito casos de resolução de
conflito, seguidos de quatro casos de retoma após falha de rede. Não acrescenta
configuração, permissões ou alterações de
formato aos dados. A validação Linux e a instalação são etapas distintas.
