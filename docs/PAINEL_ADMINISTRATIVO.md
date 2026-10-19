# Painel administrativo — organização de 19/10/2026

Nas páginas normais, o acesso «Administração operacional» ocupa uma linha própria
na barra lateral, acima da pesquisa e do botão Atualizar. Continua a aparecer
apenas para a identidade administrativa já autorizada. O acesso não ocupa nem
sobrepõe a linha da pesquisa, incluindo quando a barra lateral é estreita.

O painel `/admin` organiza as ferramentas operacionais em três separadores:

- **Folhas e acessos:** reservas, proprietário, sessões, última atividade e gravação
  automática. A libertação e a reposição continuam a pedir um motivo. A libertação
  suspende a reentrada dos técnicos afetados até repor o acesso; não transfere
  alterações que só existam no dispositivo. Esta consequência aparece na confirmação.
- **Publicações e envios:** últimas 100 operações por concluir, estado e ações
  permitidas. No fim da lista, a pesquisa por identificador permite consultar
  reconciliação, incluindo operações já concluídas. Um erro de consulta aparece
  também junto ao campo. Ver [procedimento de reconciliação](RECONCILIACAO_TRABALHOS.md).
- **Histórico:** atividade recente das fichas e últimas 100 intervenções
  administrativas. A atividade permite ver quem iniciou uma finalização/envio,
  gravação de rascunho ou cancelamento, em que ficha, quando e com que resultado.

O resumo superior mostra reservas apresentadas, pendentes mais operações em
processamento, falhas e estado do processo independente. Os números da fila vêm
do resumo completo do servidor; não são calculados a partir das 100 operações
visíveis. `—` significa fila inativa, não zero. Retenções locais não entram no
contador identificado como «Pendentes e em processamento».

**Diagnóstico técnico e SharePoint**, abaixo dos separadores, reúne o estado da
cache, pedido de atualização, atividade do worker, pendências Teams e versão.
O estado resumido do worker não confirma envio nem entrega de mensagens. A área
técnica conserva os pormenores necessários para distinguir indisponibilidade,
inatividade e versões diferentes.

## Navegação e atualização

Os separadores funcionam com rato, toque e teclado: setas esquerda/direita,
Home e End. Atualizar o diagnóstico mantém o separador selecionado. Recarregar a
página abre «Folhas e acessos». Não existe atualização automática nem persistência
do separador no browser. Quando uma atualização falha, os dados anteriores ficam
visíveis com indicação de que podem estar desatualizados.

Os cartões e diálogos adaptam-se a ecrãs pequenos. Os estados têm texto além de
cor, e as ações mantêm os pedidos de confirmação e os motivos obrigatórios.

## Atividade recente das fichas

Ao abrir «Histórico», a aplicação consulta os últimos 100 pedidos, ordenados pela
hora em que foram iniciados. Pode escolher «Finalizações e envios», «Rascunhos» ou
«Cancelamentos»; o filtro é aplicado antes do limite de 100. «Atualizar histórico»
repete apenas a leitura. Atualizar o diagnóstico nesse separador atualiza também
a atividade. Uma falha da consulta deixa uma mensagem e permite voltar a tentar.

O nome é guardado a partir da identidade autenticada no primeiro pedido durável,
antes da conclusão. Não vem dos nomes de técnicos preenchidos na ficha nem do
utilizador que a tem reservada hoje. Repetir a mesma operação não cria outra
entrada nem altera o autor inicial. Recibos antigos sem esse nome mostram
«Autor não registado». Não é feita consulta ao diretório Microsoft para os completar.

«Concluída no servidor» não comprova publicação ou envio. Quando existe trabalho
na fila, o cartão mostra o estado desse processamento e, nas finalizações, o
resultado do email: por enviar, recusado, por confirmar ou aceite pelo serviço.
Um recibo de aceitação não confirma entrega ao destinatário. A hora «Estado
atualizado» é a última alteração registada, não a hora de entrega do email.
Horas apresentadas no fuso horário do dispositivo do administrador.

É uma consulta recente dos recibos conservados, não uma auditoria integral:
mantém o limite existente de 64 operações por agregado de edição e as regras de
retenção. Não inclui aberturas da página, gravações automáticas, nem trabalhos
antigos da fila que não tenham operação de edição correspondente. As intervenções
administrativas continuam numa lista própria abaixo. Estados corrompidos são
preservados e assinalados como indisponíveis, sem tentar repará-los na consulta.

O endpoint `GET /api/admin/activity?kind=all|send|draft|cancel` usa a mesma
autorização do painel e respostas sem cache. Exporta apenas a projeção necessária;
não inclui destinatários, corpos de mensagens, assinaturas, caminhos privados,
tokens, contexto das operações ou diagnósticos brutos. A leitura percorre os
agregados locais conservando apenas as 101 projeções mais recentes e consulta,
numa chamada à fila, os resultados dos até 100 pedidos apresentados.

## Dependências e limites da alteração

A apresentação usa o logótipo já incluído; não adiciona bibliotecas, chamadas externas, permissões
Microsoft, identidade administrativa, configuração obrigatória ou migrações.
O histórico acrescenta uma rota de leitura e metadados opcionais aos novos recibos
de edição. Não altera o esquema SQLite, os envios, CSRF, verificações de revisão,
estados da fila ou decisões permitidas. A autorização administrativa segue
[a configuração existente](IDENTIDADE_ADMINISTRATIVA.md).

A instalação de uma futura versão continua a usar o procedimento habitual de
atualização e admissão do pacote; esta alteração visual não justifica apagar dados
nem repetir a reposição inicial. Este guia não autoriza instalação em produção.

## Validação local do layout anterior

- 67 testes Python: administração operacional e identidade.
- 211 testes JavaScript: bateria completa, incluindo navegação, manutenção do
  separador, contadores completos, falha de atualização, feedback de consulta,
  estados traduzidos e regressões de reconciliação/revogação.
- Verificação manual no navegador com armazenamento novo, identidades e dados
  fictícios, rede externa bloqueada, email/Teams desativados e sem worker:
  separadores, teclado, motivo obrigatório, libertar/repor, histórico, repetição
  conhecida, prova ausente recusada e reconciliação indeterminada sem desbloqueio.
- Apresentação conferida em computador, tablet e telemóvel; erro de pesquisa
  visível junto ao formulário. A revisão independente e aceitação visual da
  proposta são registadas fora deste guia, sobre o commit exato.
- A sobreposição do acesso administrativo nas páginas normais foi reproduzida
  antes da correção. Depois, verificaram-se a separação de 12 px entre linhas,
  pesquisa funcional, abertura do painel, apresentação móvel e ausência do acesso
  na sessão de técnico. Esta correção adicional altera apenas a classe do link
  e o seu CSS; não acrescenta nem altera JavaScript.

Não houve alteração no servidor nem teste de comunicações reais neste trabalho.

## Validação da atividade recente (19/10)

- 140 testes Python: histórico, autorização, identidade, edição, pedidos HTTP e
  finalização em fila. Incluem autor forjado no formulário, repetição idempotente,
  legado sem autor, aceitação/recusa/incerteza, fila ausente, corrupção preservada,
  ordenação e filtro antes do limite, e ausência de dados privados na resposta.
- 214 testes JavaScript: bateria completa, incluindo filtro, respostas fora de
  ordem, falha/repetição da consulta, estado vazio, limites e renderização textual.
- A verificação manual e o parecer independente ficam registados na nota de
  passagem e no registo de revisão sobre o commit exato.
