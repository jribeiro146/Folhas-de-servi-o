# F02 — retomar o rascunho após perda de resposta

## Problema e resultado esperado

A criação podia terminar no servidor e perder a resposta HTTP. O navegador ficava
no original, cuja reserva privada já tinha sido encerrada, e a recuperação local
voltava a receber conflito. Agora a gravação de rascunho guarda uma referência
opaca antes do POST e consulta o recibo autenticado existente quando a resposta
se perde, na mesma página ou depois de recarregar. Uma conclusão confirmada abre
a folha indicada pelo servidor pelo mesmo percurso de uma gravação normal.
Não cria outra cópia nem volta a publicar a operação.

## Dependências e invariantes

- `commit-recovery.js` admite `draft` e `send`, mantendo âmbito de sessão,
  validação e conservação das referências desconhecidas/inutilizáveis.
- `save_draft` grava a identidade do recibo na transação inicial do claim.
  A consulta valida o proprietário completo e o âmbito da sessão; continua sem
  alterar o estado. Só acrescenta `file` e `created_copy` para rascunhos, sem
  divulgar caminhos locais, texto, fotografias, assinaturas ou reserva.
- Enquanto a consulta não está resolvida, mantém o formulário protegido e oferece
  a recuperação existente. Um recibo pendente não autoriza repetir o POST.
- Uma falha/interrupção de rascunho conserva a chave. Depois do bootstrap e da
  escolha de conflito, quando necessária, a próxima gravação explícita escreve
  o formulário atual na cópia já registada no contexto da operação. A política
  de finalização F01 continua a exigir uma chave nova após falha confirmada.
- Tanto a resposta normal como o recibo usam `resumeDraftCommit`, com a indicação
  de publicação SharePoint pendente quando aplicável. A recuperação remove a
  cópia IndexedDB da área original apenas após confirmar sucesso.
- O mutex, as reservas de edição, revisões, verificação SADI e proteção de outras
  abas continuam a governar a reabertura e escrita. Não há repetição automática
  de uma gravação, email ou publicação Graph.

## Utilização e limites

Com a resposta perdida, aguardar a consulta. Se a rede impedir essa consulta,
usar **Voltar a tentar** ou recarregar. Após falha confirmada, conservar o texto,
resolver uma eventual escolha entre versões e carregar em **Guardar rascunho**.
Uma operação antiga sem identidade de recuperação não ganha acesso retroativo.
A retoma de escrita parcial aqui validada começa depois de o caminho da cópia
ficar registado; não redefine a atomicidade interna da criação de pastas.
Fotografias ainda só presentes no seletor de ficheiros não sobrevivem a uma
recarga anterior à sua gravação; esta alteração não cria armazenamento local de
anexos. Uma gravação já concluída preserva os anexos no servidor.

## Evidência

89 testes Python passaram: recibos de rascunho existente e cópia nova, leitura
sem alteração do estado, exclusão de dados privados e proprietário alheio,
falha e morte simulada após escrita parcial, retoma com texto atualizado e uma
só cópia. Inclui regressões F01, reservas e quarentena.
197 testes JavaScript passaram, incluindo retoma interrompida de draft perante
bootstrap ocupado, offline e verificação pendente, preservando a chave original.
Logs: `qa-output/consolidacao-20261007/f02-python-r1.txt` e `f02-js-r1.txt`
na raiz do projeto.

Os 25 grupos Chromium passaram em
`output/playwright/m16-fst-4a16521f/result.json`, log `f02-browser-r1-final.txt`.
Incluem os 21 grupos F01 existentes e quatro novos: resposta perdida na criação
com retoma imediata; criação com consulta indisponível e recarga; rascunho existente
com recarga; escrita parcial, recarga, escolha explícita de conflito HTTP200,
edição e repetição explícita com uma única cópia. Originais intactos, texto atual,
nenhum envio nos quatro casos F02, sem erros JavaScript ou rede externa.
Diagnósticos de desenvolvimento preservados:
o primeiro ensaio usou uma espera Python que não processava eventos do navegador;
o segundo confirmou os três casos de resposta perdida mas precisava de escolher
a versão local no conflito legítimo após falha parcial. O teste foi ajustado para
exigir que essa escolha obtém HTTP200 e deixa gravar sem ciclo de conflitos.
Dados e transportes fictícios, rede externa bloqueada, sem atualização das
instâncias locais do utilizador nem publicação em produção.

## Segunda revisão: fronteiras adicionais

Aceites os dois P2 da primeira revisão. Antes de reutilizar uma cópia parcial,
o servidor confirma o caminho ativo e contido, a identidade original da cópia e
que não foi encerrada. Caso contrário, regista `draft_copy_unavailable` no recibo
failed e devolve409. Mesmo se essa resposta se perder, a consulta permite limpar
a referência e a chave; só o próximo clique explícito cria outra cópia, mantendo
o texto. Uma cópia encerrada ainda por retirar também é recusada.

O percurso de recibo completo, tanto draft como send, limpa os anexos pendentes
antes de navegar. Assim, fotografias já guardadas não provocam beforeunload nem
permitem cancelar a navegação e iniciar inadvertidamente outra cópia.

P3: o erro de sessionStorage indica agora que o rascunho não foi guardado. A recusa
antes do POST é deliberada: permitir uma gravação sem referência persistível
reintroduziria o F02 após recarga. Não existe fallback que afirme uma gravação ou
repita uma operação incerta. O formulário/autosave continua disponível; este
percurso exige armazenamento de sessão funcional. É um limite explícito.

200 testes JS PASS (`f02-js-r2.txt`). 91 testes Python distintos PASS:89 na suite
direcionada (`f02-python-r2.txt`) e os dois novos casos, com repetição dos quatro
casos do módulo (`f02-python-r2-boundaries.txt`,6PASS). A primeira fixture destes
dois testes foi corretamente recusada pelo validador por construir um recibo
incompleto; corrigida para usar acquire/claim/commit de cancelamento reais.
Não foi necessário alterar código da aplicação por esse diagnóstico.

Os 25 grupos Chromium passaram novamente em
`output/playwright/m16-fst-50e9aa6c/result.json`, log `f02-browser-r2.txt`.
O caso de criação com resposta perdida inclui agora uma fotografia fictícia,
recusa qualquer diálogo beforeunload e confirma uma fotografia numa única cópia.
Todas as regressões F01 e as proteções de rede/efeitos continuaram aprovadas.
