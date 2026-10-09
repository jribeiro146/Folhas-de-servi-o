# Preservação do estado de edição inválido — M04

O estado JSON de cada documento é validado antes de normalizar, ler para recuperação
ou confirmar uma transação de edição. A versão atual do agregado é 2; a versão da
estrutura SQLite permanece 1. São contratos distintos. Não há migração implícita.

JSON truncado, objetos com chaves duplicadas, versões desconhecidas, identificador
divergente, campos obrigatórios ausentes e estruturas/tipos inválidos passam a
quarentena lógica. A linha original e a sua data não são sobrescritas. Não se cria
uma cópia vazia nem se apagam autosaves, reservas, operações ou revogações para
tentar recuperar. Campos adicionais desconhecidos são conservados.

A validação comum cobre identidade, revisão, sessões, proprietário, autosave,
operações e bloqueios administrativos. Os payloads dos formulários não são
revalidados como se fossem novos pedidos: a validação de conteúdo dos documentos
continua no respetivo domínio. A classe de persistência também é usada por testes
genéricos; as transações da EditingStateService fornecem sempre o validador de
agregado à persistência, na entrada e antes do commit.

## Comportamento observado

- A API de edição devolve HTTP 423 com `editing_state_quarantined` e uma mensagem
  segura. Não devolve o estado bruto.
- Uma mutação inválida criada depois de ler estado válido faz rollback e recebe
  `editing_state_write_invalid` (409), sem alegar corrupção do estado anterior.
  Identificadores de documento, sessão e idempotência são texto opaco: são usados
  exatamente como recebidos, sem remover espaços. Valores de tipo errado, vazios
  ou com espaços nas extremidades são recusados antes do bloqueio/transação.
  Onde a chave de idempotência é opcional, ausência, `null` e `""`
  mantêm a compatibilidade anterior: o servidor gera a chave quando a operação a
  usa. Uma chave composta apenas por espaços é recusada. Nos envios, gravações de
  rascunho e cancelamentos a chave continua obrigatória.
- A recuperação de finalizações e o logout isolam o documento afetado, deixando
  os restantes disponíveis. Os registos de diagnóstico usam uma referência hash,
  nunca o JSON ou nomes de clientes. A deteção é registada em cada passagem pelo
  leitor; folhas pendentes podem gerar avisos repetidos até serem recuperadas.
- O painel do administrador apresenta a referência e a necessidade de recuperação,
  sem oferecer expulsão ou reposição de acesso nessa linha. Um pedido direto também
  é recusado e não cria uma intervenção administrativa bem-sucedida.
- A fila usa a mesma validação ao verificar a confirmação local de uma finalização.
  Uma operação `complete` dentro de um estado inválido não liberta trabalho externo.
  Mantém-se a espera/recusa controlada da guarda existente.

A quarentena é deduzida do conteúdo preservado em cada leitura, não altera o esquema
nem acrescenta uma flag que permita ignorar a validação. Se houver um restauro
explícito e verificado de evidência válida, a mesma validação decide a admissibilidade.
Este bloco não fornece um botão de reparação ou de reenvio forçado: o procedimento
de recuperação integra M06/M07/M08. Não editar JSON nem apagar bases para contornar
a quarentena.

## Verificação

Testes com dados sintéticos cobrem preservação byte a byte do JSON e da data,
logout real HTTP, continuidade da listagem, abertura bloqueada, autosave e revogação
das outras folhas, diagnóstico administrativo, versões/tipos/chaves duplicadas e
trabalhos retidos com uma confirmação inválida. O transporte Graph é simulado e a
rede está bloqueada antes da importação da aplicação. Os cenários existentes de
fila passaram a gerar comprovativos completos pela API real de edição, conservando
as verificações de ausência de duplicação e preservação de recibos.

Esta proteção não demonstra que ocorreu corrupção na instalação atual, não valida
todos os conteúdos do Excel e não substitui backup/restauro do conjunto de dados.

## Diagnóstico prévio das formas históricas

O commit `f409af0b0ea77b11374954325e02779529aaa1e8` criava agregados de versão 1,
com `lease` e sem `sessions`, `owner`, `workspace_kind` ou `source_changed_at`.
O commit `a1cac3c0d3da8c7c712fce816cbc2b746e01ba21` já criava a forma completa de
versão 2. A normalização anterior também aceitava formas incompletas: não sabemos
quantas existem na instalação, nem se todas são legítimas. As fixtures em
`tests/legacy_editing_shapes.py` foram reconstruídas destas funções, com valores
fictícios; nenhum estado real foi copiado para testes.

No gravador JSON de `f409af0`, o nome do snapshot é o hash do identificador.
Extrato de `src/services/editing_state_service.py`, conferido nesse commit:

```python
def _state_path(self, document_id: str) -> Path:
    safe = hashlib.sha256(document_id.encode("utf-8")).hexdigest()
    return self.root / f"{safe}.json"
```

`_state_transaction` obtinha esse caminho, lia o estado e passava o mesmo caminho
e objeto a `_atomic_write_json`, que serializava o objeto em UTF-8 e publicava-o
com `os.replace`. O inspetor lê `document_id` do conteúdo, confirma a correspondência
exata com `sha256(document_id).json` e só depois classifica a versão/estrutura.
Nome divergente, identidade ausente ou JSON inválido ficam `invalid_state`.
Não aceita uma renomeação para `document_id.json` como se fosse um snapshot válido.
No modo SQLite, compara o identificador do conteúdo com a chave da linha.

Antes de instalar o candidato, o profissional dispõe deste diagnóstico sobre uma
**cópia consistente**, não sobre dados em utilização:

```text
python -B tools/inspect_editing_states.py --database-copy CAMINHO_ABSOLUTO_DA_COPIA_SQLITE
python -B tools/inspect_editing_states.py --json-directory-copy CAMINHO_ABSOLUTO_DOS_SNAPSHOTS_COPIADOS
```

Copiar só o ficheiro SQLite enquanto há escritores não produz necessariamente uma
cópia válida: incluir o WAL aplicável ou usar uma cópia consistente de SQLite com
os escritores parados. O inspetor usa uma transação de leitura com `mode=ro` e
`query_only`; pode haver gestão interna de ficheiros auxiliares SQLite na cópia,
sem atualizações dos dados da aplicação. Não inicializa bases nem importa a
configuração ou transportes da aplicação. Uma origem ausente nunca é criada.

O resultado contém contagens e referências hash, sem JSON, nomes ou caminhos:
`compatible`, `legacy_version`, `incomplete_current`, `unsupported_version` ou
`invalid_state`. As classes antigas/incompletas são candidatas a preparação, não
prova de conteúdo válido. Código de saída 0: todos os agregados lidos compatíveis;
1: há estados a preparar/rever; 2: inspeção incompleta ou impossível. Falta de
leitura nunca é aprovação. O âmbito é só o agregado de edição: não certifica o
esquema SQLite, filas, snapshots associados ou a instalação completa.

M04 não deve ser instalado isoladamente onde este diagnóstico identifique estados
pendentes: a migração explícita M06 e a recuperação M07 têm de lhes dar tratamento.
Isto faz parte das ferramentas/instruções entregues ao profissional, não reabre
uma tarefa nossa de inventariar ou operar o Plesk. Nunca trocar `version` ou
preencher campos manualmente para fazer uma linha passar no validador.

## Manutenção e continuidade do desenvolvimento

O M04 protege dados de uma aplicação interna. A prioridade é resolver falhas
concretas dos técnicos sem acrescentar complexidade desnecessária. Antes de alterar
o formato ou os metadados de um pedido, ler os respetivos produtores e consumidores:

| Responsabilidade | Código e contrato a manter |
|---|---|
| Pedidos do navegador | `editing-coordinator.js` gera as chaves do autosave/fecho; `document-editor.js` e `maintenance-editor.js` usam o coordenador nas operações e SADI. |
| Entrada HTTP | `application.py`: `extract_edit_metadata` recusa identidades ambíguas; `exclusive_operation` valida uma cópia do JSON/multipart antes de bloquear; a vista valida pela mesma regra. |
| Exclusão e administrador | `EditingStateService.operation_guard` e `admin_change_access` usam o mutex do mesmo `document_id`. A preparação da gravação já tem de estar protegida antes de criar a operação pendente. |
| Persistência | `editing_state_format.py` valida o agregado; `editing_state_repository.py` preserva a linha por rollback. Validação de estado existente e de uma nova escrita têm erros diferentes. |
| Leitores e efeitos externos | Serviço de edição, painel admin e guarda da fila usam a validação comum. Um leitor novo não deve transformar erro de descodificação em `{}` nem autorizar publicação com prova inválida. |
| Diagnóstico histórico | `tools/inspect_editing_states.py` lê cópias sem importar app/configuração; `tests/legacy_editing_shapes.py` conserva a proveniência das fixtures. |

O HTTP423 `editing_state_quarantined` pede recuperação do estado preservado.
O HTTP409 `editing_session_required` pede metadados válidos; não indica corrupção.
O HTTP409 `editing_state_write_invalid` recusa a nova escrita e mantém o estado
anterior. `operation_in_progress` (409) indica exclusão ativa. Manter estas
distinções nas interfaces e logs para facilitar o apoio interno.

Para reproduzir o âmbito do M04 na raiz da aplicação, com o isolamento de
`tests/conftest.py` e dados fictícios:

```text
python -B -m pytest -q tests/test_editing_request_identity.py tests/test_editing_quarantine.py tests/test_inspect_editing_states.py tests/test_operational_admin.py tests/test_graph_finalization_queue.py tests/test_graph_publication_recovery.py tests/test_maintenance_access.py
node --test tests/js/*.test.cjs
```

Os testes de identidade cobrem JSON/multipart, recusa antes de criar linhas,
bloqueio HTTP real e tentativa do administrador dentro da preparação da gravação.
Os testes de snapshots cobrem nomes históricos, versões, identidades divergentes,
JSON danificado e preservação de bytes. Uma mudança no formato deve atualizar os
testes de leitura/escrita e o procedimento de migração explícita em conjunto.
Ver também [formatos SQLite](FORMATOS_SQLITE.md), [contrato de execução](CONTRATO_DE_EXECUCAO.md)
e [administração/sincronização](SINCRONIZACAO_V2_ADMIN.md).

O fecho técnico do M04 não fecha M05 (cancelamento), M06/M07 (migração/restauro)
ou M13/M14 (entrega). As aprovações, SHA e evidência de cada revisão ficam no
registo de colaboração e na nota de passagem da consolidação; esses estados devem
ser atualizados na entrega, sem confundir documentação histórica com instalação.
