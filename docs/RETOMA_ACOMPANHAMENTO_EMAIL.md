# Retomar o acompanhamento de e-mail (M10)

Depois de o servidor responder ao POST de conclusão com email_status=pending e
graph_job_id, o navegador guarda a referência antes de qualquer espera adicional.
Ao recarregar, voltar à aplicação ou tornar a aba visível, volta a consultar
o mesmo trabalho. A retoma faz apenas GET: nunca repete o POST de envio, não
reconstrói o documento e não pede repetição administrativa.

O monitor resiliente M03 em mail-job-monitor.js mantém o seu comportamento.
mail-followup.js compõe esse monitor, acrescentando persistência limitada,
validação da sessão, prazos e apresentação. Na conclusão inicial, o formulário
continua a esperar pela aceitação e a navegar com a mensagem já existente.
Numa consulta recuperada, a aceitação é mostrada no painel da página, sem navegar
automaticamente nem descartar outra folha que esteja em edição.

## Referência e prazo

A chave sessionStorage sensorpoint-mail-followup-v1 contém apenas um scope
opaco e até dez referências: id, createdAt, expiresAt e retryAt. Não contém
documentos, nomes de ficheiros, destinatários, assinaturas, PDFs, tokens de
acesso ou resultados privados. Campos desconhecidos são removidos na leitura.
O conteúdo é limitado a16KiB e o identificador aos caracteres usados pela app.

Cada referência dura24horas desde o primeiro registo; voltar a guardar o mesmo
id não prolonga esse prazo. Isto remove só acompanhamento local, nunca trabalhos,
recibos ou provas da fila. No limite de dez, as referências existentes são
preservadas e a nova consulta continua sem promessa de recuperação; o painel
mostra o limite. «Remover acompanhamento» elimina somente a referência desta
aba, sem cancelar o envio ou apagar dados do servidor.

sessionStorage pertence à aba e à origem, podendo sobreviver a reload e retorno.
O browser pode copiá-lo ao duplicar uma aba; essa cópia continua limitada à mesma
sessão/utilizador e só produz consultas GET. Não se usa localStorage para partilhar
trabalhos entre abas. Se o navegador bloquear ou esgotar o armazenamento, a consulta
atual continua e aparece um aviso de que a retoma não ficou guardada.

## Sessão, conta e respostas tardias

O servidor produz scope a partir de um nonce aleatório da sessão assinada e do
principal efetivo (tenant/oid Microsoft ou identidade local). Não expõe a identidade
nesse campo. Logout e login Microsoft limpam a sessão; trocar o principal também
altera o scope. O scope não substitui autenticação nem autoriza ler um trabalho.

GET /api/graph/monitor-context confirma a sessão antes de ler/mostrar referências
recuperadas. As consultas levam X-Mail-Monitor-Scope; o servidor compara-o antes
de aceder à fila e mantém a verificação independente de proprietário/administrador.
Uma aba antiga também é recusada se a nova conta for administradora.
O consumidor original sem esse cabeçalho conserva as permissões M03.
As duas rotas continuam sujeitas à admissão e a respostas no-store.

Quando a página fica oculta ou sai, o painel é limpo e a geração das consultas
é invalidada. Uma resposta tardia não pode confirmar um envio ou voltar a mostrar
uma referência da geração anterior. Ao regressar de BFCache, a sessão é conferida
de novo. Uma página antiga não apaga uma referência já guardada por uma sessão
mais nova. Pedidos GET já em voo podem terminar; não originam novas consultas
nem efeitos de envio depois da invalidação. O timeout do monitor continua limitado.

401/403 ou mudança de scope retiram o acompanhamento da sessão antiga e pedem
atualização da página. Uma falha temporária na confirmação da sessão oculta as
referências e preserva-as para nova consulta. O botão «Voltar a consultar» também
faz só GET. Autenticar de novo pode invalidar a retoma local mesmo para a mesma
pessoa; o trabalho no servidor permanece com o seu proprietário e o administrador
continua a poder consultar pela referência.

## Esperas e resultados

Retry-After de429/5xx e next_attempt_at de trabalhos agendados são guardados
assim que a resposta é observada, inclusive quando o monitor original ainda está
à espera. Reload/retorno respeita esse instante, em vez de começar logo outra
consulta ao trabalho. O painel mostra o prazo; há nova consulta quando vencer,
desde que a aba esteja ativa e a referência não tenha expirado.

A aceitação confirmada remove a referência persistente e mostra confirmação
apenas na página atual. Não confirma entrega ao destinatário. Falhas, estado
desconhecido, falta de commit,404 e dados inválidos mantêm mensagens distintas;
não são convertidos em autorização para reenviar. Consultas simultâneas da
conclusão inicial e da retoma partilham um único monitor por trabalho/geração.

## Limites e evidência

Não há recuperação garantida se o POST tiver sido aceite pelo servidor mas a
resposta com o id nunca chegar ao browser. Não se infere sucesso/falha nesse caso
nem se repete o POST automaticamente. Esta alteração também não é uma pesquisa
de todo o histórico de trabalhos, um mecanismo de envio offline ou um serviço
de notificações fora da aplicação. O scope não é uma defesa contra código
malicioso já executado na mesma origem ou alguém com acesso à sessão autenticada.

Os testes JavaScript exercitam o monitor real com GET simulado, reload/retorno,
duas referências, conta/sessão, respostas tardias, prazos, TTL, quota/armazenamento,
remoção e eventos DOM de pagehide/pageshow. Testes Flask verificam scope opaco
e estável, renovação, recusas antes de tocar na fila, proprietário, admin e cache.
As regressões do monitor M03 permanecem aplicáveis e são repetidas.
O ensaio DOM simulado não substitui a matriz de browser real prevista no M16.
Não houve envio real, deploy ou alteração de uma fila operacional.
