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

Verificação de 23/10/2026: os três percursos reproduziam a captura prematura da
revisão. Os testes JavaScript cobrem sucesso, falha, conflito, revogação, espera
limitada, suspensão e libertação após falha do cancelamento. A bateria completa
passou: 240 testes. Não acrescenta configuração, permissões ou alterações de
formato aos dados. A validação Linux e a instalação são etapas distintas.
