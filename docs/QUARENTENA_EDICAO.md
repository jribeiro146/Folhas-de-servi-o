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
- A recuperação de finalizações e o logout isolam o documento afetado, deixando
  os restantes disponíveis. Os registos de diagnóstico usam uma referência hash,
  nunca o JSON ou nomes de clientes.
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
