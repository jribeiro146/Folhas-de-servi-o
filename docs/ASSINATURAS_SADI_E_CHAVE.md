# Continuidade da chave e assinaturas SADI (M22)

`FS_SECRET_KEY` é usada pela sessão Flask e pelo HMAC das assinaturas nos rascunhos SADI. Em produção deve ser uma chave estável, privada, com pelo menos 32 caracteres, fornecida pelo ambiente de execução. A chave não pertence ao Git, ao manifesto de entrega nem ao pacote de revisão. Uma atualização de código deve conservar a chave existente. Este documento não executa nem autoriza uma rotação operacional.

## Ao conservar ou mudar a chave

Com a mesma chave e conteúdo, as assinaturas anteriores continuam válidas, incluindo os rascunhos `sadi-2` anteriores à introdução dos avisos. Mudar a chave invalida os cookies de sessão existentes: os utilizadores têm de iniciar sessão novamente. Invalida também os HMAC dos rascunhos assinados com a chave anterior.

Ao abrir ou validar um rascunho, o servidor retira as assinaturas que não consegue verificar e conserva um marcador por local e papel: `signature_warnings.<papel> = verification_failed`. O aviso explica que a chave pode ter mudado ou o conteúdo ter sido alterado e pede uma nova assinatura. Um HMAC inválido não permite distinguir estas duas causas; não se afirma que houve uma rotação sem evidência operacional. Tokens malformados têm o mesmo tratamento.

O aviso regressa ao reabrir o rascunho e permanece depois de uma gravação. A leitura, por si só, não reescreve os dados persistidos. A finalização continua bloqueada enquanto faltar uma assinatura obrigatória válida. Uma página que já estava aberta recebe também o motivo ao tentar validar/finalizar os dados antigos. Depois de um novo login, reabrir o rascunho apresenta os avisos junto das assinaturas.

Recolher uma assinatura nova e guardá-la remove apenas o aviso desse papel. Não se assina automaticamente uma imagem antiga com a nova chave; uma captura pendente associada à assinatura rejeitada também é retirada. Capturas ainda não assinadas continuam a ser rascunhos, sem valor de assinatura válida. A dispensa explícita por cliente ausente mantém as regras existentes; a assinatura do técnico continua obrigatória.

Os marcadores só contêm códigos conhecidos e não incluem chaves, imagens ou tokens. São metadados de apresentação, excluídos do conteúdo HMAC. Não dispensam a validação criptográfica nem alteram a assinatura válida do outro papel.

## Documentos já finalizados e recuperação

Os PDFs já finalizados são ficheiros emitidos. A mudança de chave não os regenera nem altera os seus bytes. Depois do novo login, continuam sujeitos às permissões SADI existentes e são servidos com `Cache-Control: no-store`. Esta garantia não equivale a validar novamente o HMAC do rascunho com a chave nova.

Antes de uma rotação operacional planeada, inventariar os rascunhos pendentes e coordenar a recolha de novas assinaturas; conservar a chave de acordo com o procedimento privado de cópia e recuperação. Não a transcrever para tickets, logs ou documentação. Uma chave perdida não pode ser recuperada a partir dos HMAC. Restaurar apenas a chave anterior não recupera uma assinatura que já foi retirada numa gravação posterior; qualquer recuperação exige a cópia correspondente e o procedimento operacional autorizado. Não restaurar uma chave comprometida para evitar a recolha de assinaturas.

Não foi introduzida uma chave SADI separada nem aceitação de chaves antigas: isso exigiria política própria de rotação e recuperação. A configuração atual continua a ter uma única chave estável.

## Evidência e limites dos testes

`tests/test_sadi_signature_key.py` usa exclusivamente documentos, imagens, chaves e diretórios temporários fictícios. Cobre continuidade do formato histórico, mudança de chave, avisos persistentes, tokens malformados, nova recolha por papel, validação e bloqueio da finalização via HTTP, invalidação da sessão e acesso aos mesmos bytes PDF após novo login. Os PDFs deste teste são produzidos pelo renderer simulado da fixture; a comparação comprova conservação e controlo de acesso, não composição visual. As regressões SADI e de autenticação são executadas em conjunto.

`tests/js/maintenance-notice.test.cjs` cobre a apresentação dos avisos recebidos do servidor, remoção após nova assinatura, cliente ausente e rejeição de códigos desconhecidos. A matriz visual completa de SADI/PDF permanece no M18.
