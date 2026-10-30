# Cancelamento recuperável (M05)

O cancelamento preserva uma cópia local completa dos conteúdos geridos da folha
em `Excel/Canceladas`. No SharePoint, move o item identificado para
`<GRAPH_ARCHIVE_PATH>/Canceladas/cancel-<hash da operação>` no mesmo drive.
Não apaga recursivamente pastas. O identificador remoto e o destino ficam no
resultado da fila; o nome original e a prova ficam no contexto da operação.

## Comportamento e impacto

- Só rascunhos podem ser cancelados. Uma publicação ainda pendente ou falhada
  deve ser resolvida primeiro; o cancelamento não ultrapassa esse trabalho.
- Identidade e versão da pasta/ficheiro remoto têm de corresponder aos recibos
  locais. São comparados também os identificadores e versões dos ficheiros
  descendentes; falta de prova ou conflito impede a operação.
- Um ativo remoto ausente no momento da preparação não autoriza tocar num
  homónimo criado depois. Nesse caso, só é concluído o cancelamento local.
- A pasta remota é preservada inteira. A eTag da pasta não garante que os
  descendentes não mudaram: a comparação deteta alterações anteriores, mas um
  ficheiro acrescentado entre a comparação e o PATCH segue com a pasta e
  permanece recuperável no SharePoint.
- A operação deixa de retirar a origem local antes de confirmar o cancelamento.
  Falhas de preparação/publicação/commit mantêm a origem e podem ser retomadas
  com a mesma chave da operação, sem criar uma segunda cópia concluída.
- A fila nasce em `held` e só pode atuar depois do commit de edição confirmado.
  Leituras/preparação remotas que falhem antes do PATCH mantêm a repetição
  normal; conflitos conhecidos são terminais e permitem revisão/repetição
  manual. Respostas 409/412 ao PATCH são registadas como recusas definidas.
  A intenção só é persistida imediatamente antes desse PATCH. Uma resposta
  ambígua depois da intenção persistida exige reconciliação; não
  há repetição automática nem através do botão de repetição do administrador.
  Na recuperação, esse trabalho termina em `failed` para reconciliação e liberta
  a fila, mesmo quando os prazos de inatividade Graph e email são diferentes.
- Trabalhos antigos `remove_active` sem a nova prova são recusados e preservados
  para diagnóstico. A migração não pode inventar essa prova (ver M06).

Não muda o cancelamento de folhas originais, a autenticação nem os privilégios.
Os cancelamentos remotos passam a ocupar espaço em Arquivadas/Canceladas. Não há
limpeza automática desses conteúdos; é uma decisão operacional posterior.

## Fluxo e dependências

| Responsável | Garantia |
|---|---|
| `application.cancel_file` | Exclusão da escrita, validação de identidade/reserva, barreira de uploads, contexto durável e commit antes de retirar a origem |
| `FinalizationService` | Preparar cópia, publicar diretório completo com marcador de propriedade e preservar origem até ao commit |
| `EditingStateService.finalizations_to_retire` | Recuperar limpeza posterior ao commit, tanto de envio como de cancelamento |
| `graph_cancellation.prepare_cancellation` | Prova só de leitura de identidade, versões, descendentes e ausência |
| `GraphSyncQueue` | Commit guard, intenção/resultado persistidos, bloqueio de repetição e recuperação após reinício |
| `graph_cancellation.execute_cancellation` | Conferir prova e mover por ID com If-Match, sem DELETE e sem substituir homónimos |

O contrato de armazenamento e os bloqueios de processo/folha existentes continuam
a proteger as operações. Não importar a aplicação em ferramentas de diagnóstico:
a criação da aplicação configura serviços e não é uma ação apenas de leitura.

## Diagnóstico e recuperação

1. Confirmar o ID da folha e a chave de cancelamento no diagnóstico administrativo.
   Não criar outro cancelamento para tentar contornar uma operação pendente.
2. Em falha local antes do commit, repetir a mesma operação/reserva ainda válida.
   Conferir o contexto `canceled_path` e o marcador `.fs-finalization.json` se
   for necessária intervenção offline. Não substituir uma cópia de outra operação.
3. Se a origem ficou bloqueada pelo sistema operativo depois do commit, a próxima
   listagem/abertura recupera a limpeza. A fingerprint e o marcador impedem retirar
   uma origem alterada ou sem cópia pertencente à operação.
   Cancelamentos do formato antigo, sem fingerprint/marcador, não entram neste
   ciclo: a sua análise pertence à migração/recuperação offline, sem transações
   inúteis repetidas em cada listagem.
4. Em resultado remoto incerto, inspecionar o item pelo `source_id` e o destino
   determinístico registado, sem apagar ou reenviar nada. Se estiver no destino,
   o conteúdo remoto foi preservado; se ainda estiver em Activas ou noutro local,
   a decisão requer comparar versões/conteúdos. Um homónimo com outro ID nunca
   constitui prova da situação do original.
5. Preservar a fila/resultado para auditoria. Este ponto não acrescenta um botão
   que converta prova humana de reconciliação em autorização de repetição. A
   intervenção real cabe ao profissional e exige uma decisão explícita; não
   mudar manualmente `status` para `pending`.

## Testes e limites

Executar, na árvore da aplicação:

```text
python -B -m pytest tests/test_cancellation_recovery.py tests/test_graph_publication_recovery.py tests/test_graph_finalization_queue.py tests/test_editing_request_identity.py tests/test_operational_admin.py -q
```

`tests/conftest.py` prepara armazenamento fictício e bloqueia rede antes dos
imports. Os ensaios cobrem homónimo novo, eTag ausente/alterada, descendente
alterado/acrescentado, propriedade ausente, resposta perdida, reinício nos dois
checkpoints, commit falhado, publicação local interrompida, limpeza adiada,
repetição idempotente, barreira de uploads e recuperação com prazos diferentes
sem bloquear o trabalho seguinte. São testes de aplicação com transporte
simulado; não provam o comportamento de uma instalação ou credenciais reais.

Referências do contrato Graph: [movimento por ID e If-Match](https://learn.microsoft.com/en-us/graph/api/driveitem-move?view=graph-rest-1.0)
e [propriedades/eTag de driveItem](https://learn.microsoft.com/en-us/graph/api/resources/driveitem?view=graph-rest-1.0).
O desenho usa movimento para preservar conteúdos em corridas que uma eTag de
pasta não consegue excluir. Não houve pedidos operacionais durante a validação.
