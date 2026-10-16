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

## Terceira revisão: preservar edições independentes da cópia

Aceite o P2 da segunda ronda. A área original e a cópia são documentos distintos:
o bloqueio da primeira não protegia a segunda. `draft_copy_guard` passa a deter o
mutex de edição da própria cópia e o bloqueio do seu bundle, desde a verificação
até ao fim da escrita e do checkpoint, na mesma ordem das restantes escritas.
Não usa operation_guard para examinar a cópia, pois isso poderia normalizar uma
operação pendente de outro editor. A leitura do estado é feita sem o modificar.

Só reutiliza uma cópia sem dono, sessões, autosave, operações ou revisão posterior
à inicial, e com fingerprint igual ao registado pela tentativa anterior. Uma
aba que a tenha aberto, um autosave, uma gravação ou uma alteração externa fazem
a original receber `draft_copy_unavailable`; nenhum byte, revisão ou autosave da
cópia é substituído. O formulário da original pode ser guardado numa nova cópia
apenas através de nova ação explícita. Uma escrita concorrente mantém o mutex
ocupado e é recusada sem desbloquear o outro editor.

O fingerprint é registado antes da escrita e no finally ainda sob os bloqueios.
Exceções controladas, incluindo SystemExit no teste, conservam prova da escrita
parcial para a retoma. Uma morte abrupta sem finally, com bytes já alterados,
não fornece essa prova: a cópia incerta fica preservada e não é sobrescrita. O
utilizador pode abrir essa cópia pela lista ou criar outra explicitamente. Igual
prudência aplica-se a cópias antigas sem checkpoint e a falhas posteriores à
atribuição de proprietário. Não se promete reutilização automática nesses casos.

96 regressões Python PASS (`f02-python-r3.txt`), incluindo quatro cenários de
adoção/alteração independente e uma tentativa de bootstrap da cópia durante a
escrita, recusada com409. Estado e ficheiros da cópia são comparados antes/depois
da recusa. Frontend inalterado desde os200JS aprovados na ronda2. Mais92 testes
Python PASS de arquivo/web, publicação Graph, fila de finalização e fotografias
(`f02-python-r3-integrations.txt`): total188Python distintos no candidato.
Os25gruposChromium passaram em `output/playwright/m16-fst-1412bedb/result.json`,
log `f02-browser-r3.txt`, mantendo a fotografia e todas as regressões F01/F02.
