# F03 — resposta inválida durante a gravação

## Problema e correção

Uma resposta HTML500 era interpretada como JSON, apresentando o erro técnico
`Unexpected token '<'`. A gravação podia ter falhado antes de começar ou ter
concluído no servidor: trocar apenas a mensagem não distinguia esses casos.

A aplicação passa a manter o contrato JSON e a referência de diagnóstico nas
exceções500 das rotas API, incluindo erros dos guards que ficam fora do tratamento
interno da vista. Mantém o estatuto500, sem devolver a exceção privada ao técnico.
As páginas não API conservam o tratamento Flask existente;404/405 e as recusas
específicas continuam com os seus estatutos.

Nas ações Guardar rascunho e Finalizar, `readCommitResult` verifica se recebeu uma
resposta válida, incluindo os campos necessários ao encaminhamento em caso de
sucesso. HTML, corpo vazio ou JSON sem contrato entram no percurso F01/F02 de
consulta do recibo, sem assumir que a escrita falhou e sem repetir automaticamente
o POST. A referência do cabeçalho é apresentada apenas se tiver formato válido.
Uma resposta inválida na própria consulta também conserva a referência e pede
nova consulta; não deixa escapar o erro do analisador JSON.

## Percurso de recuperação

- Recibo completo: segue a mesma navegação da conclusão normal para a cópia ou
  para o acompanhamento da finalização. Fotografias confirmadas são reconhecidas.
- Recibo pendente ou consulta indisponível/inválida: conserva a referência e o
  formulário, bloqueia nova escrita e disponibiliza Voltar a tentar.
- Sem operação no servidor: conserva o formulário e a mesma chave. Só um novo
  clique explícito pode gravar. Falha/interrupção confirmada mantém as políticas
  específicas de F01/F02, incluindo proteção das cópias adotadas noutra aba.

Não são alterados destinatários, corpo de email, fila, autenticação ou regras de
publicação. Os ensaios usam dados e sessões fictícios e transportes simulados.

## Validação e limites

205 testes JavaScript PASS em `qa-output/consolidacao-20261007/f03-js-r1.txt`:
HTML, null e sucesso incompleto em draft/send; consulta antes de qualquer decisão;
nenhum segundo POST automático; referência segura e ausência do erro técnico;
consulta com HTML/null mantém a referência. Regressões F01/F02 incluídas.

165 testes Python PASS (`f03-python-r1.txt`). Os novos testes
injetam OSError no guard real de draft/send:JSON500, mesma referência do
cabeçalho, conteúdo privado excluído da resposta e do log formatado, ficheiro e
estado intactos, recibo404 e nova gravação explícita possível. Inclui diagnóstico,
privacidade, arquivo/web, identidade de edição e regressões de recibos F01/F02.
Os29gruposChromium passaram em `output/playwright/m16-fst-1259639b/result.json`,
log `f03-browser-r1.txt`. Os quatro novos grupos provocam HTML antes/depois de
guardar, HTML na consulta e HTML após finalizar. Confirmam o texto e uma cópia;
um segundo POST só ocorre no caso sem gravação e após novo clique. Finalização
confirmada gera um único email fictício. Sem erros JavaScript ou rede externa.

Âmbito: gravação/finalização e consulta do respetivo recibo. Não se afirma revisão
global de todas as leituras JSON do frontend. A perda de ligação continua distinta
de uma confirmação de falha. Esta validação não atualiza as instâncias5012/5013,
não constitui ensaio no servidor de destino e não autoriza produção.
