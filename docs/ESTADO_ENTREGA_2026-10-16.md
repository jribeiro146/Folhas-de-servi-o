# Validação final local — 16/10/2026

**A bateria local final passou. A instalação em produção ainda não está confirmada.**
Faltam o ensaio Linux/Docker da versão atual e as verificações da configuração e
integrações no destino. João pediu validar antes de publicar, reservando a
publicação para o último passo. Não houve push, deploy ou envio operacional nesta
validação. Não é necessária uma pré-produção completa para testar a aplicação;
é necessário um ambiente Linux/Docker isolado para confirmar essa parte da entrega.

## Versão e alterações incluídas

Base canónica: `qa-output/production-delivery-20260928`. O pacote/demo é outra
árvore e não deve ser copiado sobre esta. O commit que executou a bateria final é
`5eee0607c1709e049cd322b8775fb0142106b9ba`, fonte
`173dc0a9152267bc458d171237069adff97f169e6b9b01b4f5955bbe9ae0d8cf`, 123 ficheiros.
O commit posterior de consolidação altera apenas documentação: o relatório que
acompanha o ZIP identifica o HEAD final, checksum, manifesto e parecer do Claude.
O manifesto é verificado pelo procedimento de [identidade da versão](IDENTIDADE_DA_VERSAO.md).

M01–M22 conservam os checkpoints técnicos documentados. As correções posteriores são:

- [F01](RECUPERACAO_OPERACOES_2026-10-14.md): consultar o recibo depois de uma resposta
  perdida/interrupção, preservar o formulário e impedir repetição automática da finalização.
- [F02](RECUPERACAO_RASCUNHOS_F02_2026-10-16.md): recuperar a cópia privada já criada e
  retomar uma escrita parcial comprovada, protegendo cópias adotadas noutra sessão.
- [F03](RESPOSTAS_GRAVACAO_F03_2026-10-16.md): respostas HTML, vazias ou incompletas
  durante a gravação entram no percurso de consulta do recibo; não são apenas um aviso novo.

Desde F03 (`ebd65e40`), o código em `src/`, Docker e requisitos permanece igual.
Nesta validação só foram corrigidos um teste de concorrência e uma indicação
histórica desatualizada no relatório SADI. O teste passa a fazer o pedido concorrente
num cliente e thread independentes, como acontece com dois pedidos reais. Conserva
a exigência de HTTP409 enquanto a cópia está a ser escrita. A matriz SADI passa a
remeter a verificação M22 para o ensaio integrado, em vez de o indicar como aberto.

## Evidência final

[Registo verificável](EVIDENCIA_LOCAL_2026-10-16.json). Os logs integrais estão em
`qa-output/consolidacao-20261007/final-readiness-20261016`, fora do pacote operacional.

| Verificação | Resultado |
|---|---|
| Python completo, candidato 5eee0607 | **1570 aprovados, 4 skips, zero falhas**, 441,85 s |
| JavaScript | **205 aprovados**, zero falhas |
| Percursos Chromium | **29 grupos PASS**: rede/401, três abas, conflito, disco cheio, processo interrompido, administrador e recibos F01–F03 |
| SADI e PDFs | **5 grupos PASS**, três perfis, assinaturas, cliente ausente, separação por local e anexo de email sem SADI privado |
| Atualização da aplicação | **6 grupos PASS**, versão à espera, formulário preservado, cache e modo offline |
| Inspeção visual | **8 páginas verificadas**, folha, anexo e dois PDFs SADI; novas renderizações idênticas às páginas inspecionadas |
| Manual no navegador | Guardar uma cópia, recarregar, preservar acentos/texto e finalizar com **0 minutos** e **materiais Sim/linha vazia**: PASS |

Não somar execuções repetidas como testes distintos. Três skips exigem permissões
POSIX; o quarto exige criação de symlink não disponível neste Windows. O teste de
recusa de junções Windows passou fora da sandbox. O resultado Linux anterior não
substitui estes quatro casos no pacote atual.

O ensaio manual usou a instância fictícia 5015, entretanto terminada, com rede,
email, Teams e worker desativados. O arquivo conserva o texto, 0 minutos e uma
linha de materiais em branco, conforme as regras confirmadas por João. Os ensaios
integrados usam transportes simulados e verificam ausência de duplicação por
operação. Não demonstram entrega real de correio ou acesso Microsoft/Graph real.
As instâncias locais 5012/5013/5014 não foram reiniciadas nesta fase. O portal de
treino 5014 e os seus atalhos de identidade fictícia não fazem parte do pacote.

## Falhas preservadas e limites

A primeira bateria teve 1562 aprovados/4 skips/8 falhas. Seis erros resultaram de
caminhos temporários longos; um ensaio no mesmo contexto demonstrou que o mesmo
nome, com diretório existente, falha a 263 caracteres e funciona a 154. A raiz de
pytest foi encurtada, sem mudar políticas globais do Windows. Uma junção foi
recusada pela sandbox. A oitava falha vinha de um pedido aninhado no teste F02,
que reutilizava `g` do Flask e contaminava o contexto de logging do teste seguinte.
A sequência mínima falhou antes e passou depois da correção do ensaio.
Os módulos afetados passaram 119 testes/1 skip com a raiz curta e a permissão necessária.

A segunda bateria teve 1569 aprovados/4 skips/**uma falha**: ao finalizar uma SADI
com cliente ausente, recebeu HTTP400 e a mensagem genérica de ficheiro em uso.
O ponto exato da recusa não foi capturado nessa execução; não se demonstrou se
ocorreu na abertura, gravação ou substituição do Excel, nem qual processo a causou.
O Excel e o sidecar com dois locais ficaram preservados no rascunho.

Dez processos isolados repetindo esse caso passaram. A terceira bateria completa,
com observador passivo que só regista a exceção original sem mudar a decisão da
aplicação, passou 1570 testes; não capturou novas recusas SADI. **Isto não demonstra
a causa nem a resolução do bloqueio intermitente Windows.** Manter a ocorrência
aberta no diagnóstico e verificar o caso no destino Linux. Os WinError5/HTTP400
históricos também não foram retroativamente explicados por estes ensaios.

A pré-visualização no navegador integrado tinha uma observação anterior; o percurso
Chrome foi verificado na atualização local. Esta validação não certifica todos os
navegadores/dispositivos. Retenção 90/180/365 dias continua apenas simulada, sem
prazo de eliminação escolhido e sem apagar recibos ou operações incertas.

## Condições antes de instalar

1. Executar [Linux/Docker](VALIDACAO_DOCKER_INSTALADOR.md) no pacote final, com dados
   fictícios e transportes simulados: imagem, PDF Chromium, web e worker separados,
   dois perfis de permissões e aceitação de migração/backup/restauro. O CI de 14/10
   testa outro commit e outro ZIP. Se for usado GitHub Actions, a publicação da
   branch de testes deve respeitar a decisão de João sobre o último passo.
2. Conferir o destino AlmaLinux, montagens e dono dos volumes, HTTPS e callback
   Microsoft. Manter a mesma configuração e versão em web/worker, a chave
   `FS_SECRET_KEY` estável e `FS_GRAPH_QUEUE_IN_WEB=false`. A abertura do site não
   confirma o worker: consultar [diagnóstico do worker](DIAGNOSTICO_WORKER.md).
3. Configurar a [identidade administrativa](IDENTIDADE_ADMINISTRATIVA.md):
   `jribeiro@sensorpoint.pt` só é administrador quando o tenant e o object ID do
   utilizador coincidem com `FS_ADMIN_TENANT_ID` e `FS_ADMIN_OBJECT_ID`. Não usar os
   GUIDs fictícios do treino nem confundir object ID do utilizador com client ID da aplicação.
4. Fazer [backup e verificação](BACKUP_RESTAURO.md) antes da intervenção; escolher
   init, migração legada ou activate conforme o armazenamento existente. Verificar
   reversão e preservação das operações incertas; não libertar envios sem reconciliação.
5. Após autorização operacional específica, validar Microsoft/Graph e comunicações
   com dados e destinos de teste definidos. Para qualquer email de teste autorizado
   por João nesta sequência, o único destinatário permitido é `jribeiro@sensorpoint.pt`,
   incluindo Para/CC/BCC. Não reenviar uma operação de resultado incerto.

Esta página é o estado da validação local e das condições de entrega. A revisão
independente do Claude é estática; execução dos testes e inspeção manual são do
Codex. O parecer válido, o âmbito revisto e o resultado de `check` acompanham a
entrega; não constituem autorização de instalação nem eliminam as condições acima.
