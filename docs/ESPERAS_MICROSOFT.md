# Esperas indicadas pela Microsoft

A aplicação conserva os prazos de resposta 429/503 do armazenamento numa base privada `provider-waits.sqlite3`, dentro do diretório de dados existente. Web e processador da fila consultam a mesma base antes de obter um token e imediatamente antes de cada pedido. Pedidos já iniciados podem terminar. Não há uma espera longa dentro de um pedido HTTP ou do bloqueio de uma ficha.

Um prazo válido em segundos ou data HTTP não é encurtado. Sem prazo válido, aplica-se espera exponencial. O maior prazo observado prevalece entre sessões e reinícios. Uma espera superior a 24 horas fica suspensa para análise, conservando o prazo original. Valores impossíveis de representar ficam suspensos sem prazo calculável. Uma decisão administrativa nunca permite antecipar o prazo; valores sem prazo calculável exigem diagnóstico pelo suporte e não têm botão de desbloqueio.

A espera exponencial acompanha as recusas consecutivas. Depois de cinco minutos sem nova recusa para além do prazo anterior, uma nova ocorrência começa no atraso inicial. O simples fim do prazo não reinicia a contagem, para não reduzir todas as tentativas consecutivas a um segundo.

O âmbito de armazenamento identifica a aplicação Microsoft, tenant e biblioteca por hash; o email tem âmbito separado. Também existe capacidade de suspensão da aplicação inteira, sem inferir esse âmbito a partir de qualquer resposta 429. Não se guardam cabeçalhos crus ou credenciais nesta base.

O botão Atualizar conserva a última lista confirmada e mostra a espera. Publicações pendentes mantêm-se na fila; uma espera partilhada não consome tentativas dos trabalhos que ainda não arrancaram. As confirmações exclusivamente locais de publicações anteriores podem concluir durante a espera. Uma operação remota que já arrancou mantém a política existente de limite de tentativas. Nenhuma suspensão ou revisão torna repetível um envio de resultado incerto.

Terminado o prazo, a lista deixa de indicar espera e permite nova atualização, ainda que o intervalo normal de atualização não tenha terminado. A lista só volta a ser confirmada depois de uma leitura completa. Enquanto existirem trabalhos à espera de confirmação local, a fila verifica essa confirmação de cinco em cinco segundos, mesmo que outra publicação esteja sujeita a uma espera longa.

O administrador consulta as esperas em Publicações e envios. Nas suspensões anómalas cujo prazo já terminou pode concluir a revisão, indicando motivo. A decisão verifica versão e referência da resposta e grava auditoria e alteração na mesma transação. Falha de auditoria ou diagnóstico desatualizado mantém a suspensão. O histórico de intervenções combina as decisões humanas com as intervenções existentes, sem duplicar a escrita entre bases. As esperas automáticas têm uma lista separada, para não ocultarem decisões humanas.

## Atualização e recuperação

O contrato de armazenamento acrescenta `provider_waits: 1`; não há nova variável de ambiente nem nova permissão Microsoft. Parar web e processador, obter backup consistente e executar o prepare/apply da migração M06 para o pacote exato. A migração aceita o contrato anterior sem esta base e cria-a vazia; uma base já existente é validada e copiada com os prazos e decisões. Não executar a reposição única de 19 de outubro.

O backup inclui a base e os dados já confirmados no WAL. A restauração preserva os prazos do snapshot e mantém a retenção global existente. Um backup antigo não contém respostas Microsoft recebidas posteriormente: antes de libertar essa retenção, o implementador deve conservar/conferir também as esperas mais recentes na cópia do armazenamento retirado e resolver as diferenças, além dos envios posteriores ao backup. A confirmação global de reconciliação não deve ser dada com este ponto por verificar. Não apagar nem substituir manualmente a base para contornar uma espera.

Testes usam transportes e relógios sintéticos, incluindo 120 segundos, data HTTP, dez instâncias, recusa a meio da listagem/download, repetição manual, migração e backup. Não constituem medição de latência da Microsoft em produção.

## Email

O transporte de email usa a mesma base e o mesmo parser de prazos. Uma recusa 429 de envio cria uma espera por tenant/aplicação/remetente; uma espera de email não suspende só por si a leitura do SharePoint ou a edição local. Uma recusa temporária na obtenção do token usa o âmbito da aplicação Microsoft, partilhado com o armazenamento que utiliza a mesma credencial. A guarda é consultada antes do token e imediatamente antes do POST.

A verificação de credenciais antes de finalizar é independente da espera do remetente: a folha pode ser arquivada localmente e o email fica pendente na fila. Essa verificação reutiliza um token válido já disponível ou consulta apenas a guarda da aplicação antes de pedir outro. Se não existir token válido e a obtenção de credenciais estiver em espera, a finalização com email permanece por confirmar: devolve a espera, preserva o rascunho/autosave e não cria um envio futuro sem nova confirmação do técnico.

Um prazo de 900 segundos mantém os 900 segundos, incluindo quando vem numa data HTTP. Reiniciar, abrir novas sessões, criar outro trabalho ou repetir manualmente não permite antecipá-lo. A mensagem já preparada conserva os mesmos bytes. O acompanhamento do envio mostra a espera também nos trabalhos ainda não iniciados e identifica as suspensões que exigem análise; não repete o POST.

Uma resposta 503 depois de enviar continua a significar resultado incerto: o serviço pode ter aceite a mensagem. Regista-se a espera para outros trabalhos quando possível, mas o envio incerto continua retido para reconciliação mesmo depois desse prazo. Falhar o registo da espera também não torna esse envio repetível. O mesmo cuidado mantém-se para falhas de comunicação e restantes respostas ambíguas. Não foram alterados destinatários, limites de tentativas, permissões nem configuração obrigatória.

Uma falha local de preparação antes do POST não constitui envio incerto. A fila conserva a mensagem e aplica a política limitada de recuperação. Se o fornecedor já recusou e falhar a gravação da espera partilhada, conserva-se o prazo representável no trabalho e exige-se revisão do armazenamento, sem retoma automática. Um prazo impossível de representar que também não pôde ser registado fica bloqueado para diagnóstico, preservando a mensagem; não se substitui por um prazo mais curto.

Durante essa falha, uma guarda de emergência em memória impede novos pedidos no mesmo processo, incluindo outras instâncias do serviço e outros trabalhos. Assim que a escrita recuperar, a guarda e a auditoria são gravadas com o prazo absoluto original, conservando suspensões; a recuperação é tentada no máximo uma vez a cada dez segundos. Uma suspensão ainda não gravada não pode ser levantada no painel. Esta contenção em memória não é prova durável nem comunicação entre processos: se a escrita falhar, o operador deve resolver o armazenamento antes de reiniciar ou retomar os serviços. Um processo terminado antes de qualquer gravação perde essa memória; o prazo já guardado na fila continua a proteger o trabalho respetivo.
