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
- **Histórico:** últimas 100 intervenções administrativas, autor, data e motivo.

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

## Dependências e limites da alteração

A alteração limita-se ao template, CSS e JavaScript do painel, testes e este guia.
Usa o logótipo já incluído; não adiciona bibliotecas, chamadas externas, permissões
Microsoft, identidade administrativa, configuração obrigatória ou migrações.
As rotas, os contratos da API, CSRF, verificações de revisão, estados da fila e
decisões permitidas são os existentes. A autorização administrativa segue
[a configuração existente](IDENTIDADE_ADMINISTRATIVA.md).

A instalação de uma futura versão continua a usar o procedimento habitual de
atualização e admissão do pacote; esta alteração visual não justifica apagar dados
nem repetir a reposição inicial. Este guia não autoriza instalação em produção.

## Validação local

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
