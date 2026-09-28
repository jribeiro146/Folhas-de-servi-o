# Guia funcional para a app a preservar

Este guia descreve o percurso de utilização observado e os comportamentos que o desenvolvimento deve conservar. A pasta `aplicacao/` inclui a interface e o backend real das folhas de serviço e checklists, com os serviços de ficheiros/Excel, assinaturas, PDF, Graph, autenticação e filas. Apenas a administração foi excluída. A base serve para continuar o desenvolvimento local; não está configurada nem validada em produção por esta entrega. O laboratório incluído serve apenas para experimentar validações; não executa o percurso completo abaixo.

Antes de usar a cópia em desenvolvimento, seguir as instruções de arranque isolado do pacote: dados fictícios, diretórios exclusivos, configuração operacional excluída e transportes externos bloqueados/simulados. Os mocks são ferramentas de validação; a aplicação entregue não se limita ao laboratório nem a um backend simulado.

## Encontrar e iniciar a folha

1. Procurar na lista por identificação da folha e confirmar cliente/local. Distinguir folha pronta, rascunho em execução, folha em uso e ficheiro inválido.
2. Atualizar a lista se necessário. A indicação de atualização deve representar a conclusão efetiva do inventário, sem apagar o formulário aberto.
3. Abrir a folha. Se estiver em consulta por outro utilizador a editar, não escrever por cima. A interface deve explicar quem/porquê quando autorizado e permitir tentar novamente.
4. Rever identificação, cliente, instalação, trabalhos pedidos, tipos de serviço e sistemas. Número de obra inválido não deve abrir uma pasta presumida.

## Preencher e guardar

- Escrever o relatório da intervenção de forma clara.
- Indicar se houve materiais. Quando Sim, preencher referência, descrição e quantidade. Antes de remover uma linha ou mudar para Não, verificar a consequência e confirmar quando houver conteúdo.
- Registar os técnicos que trabalharam, data e horas. O total é calculado; quando necessário pode ser ajustado. O ajuste mantém-se até escolher «Usar cálculo».
- Guardar rascunho mesmo incompleto. Observar a diferença entre alterações no dispositivo, pedido a guardar, confirmação de gravação e sincronização pendente.
- Ao retomar, rever a recuperação proposta. Não substituir outra revisão automaticamente. Se houver conflito, preservar as duas versões até resolver.

## Assinatura da folha

Quando o cliente está presente, recolher a assinatura e preencher primeiro e último nome e data válida. Se estiver ausente, usar a opção própria e confirmar a remoção de uma assinatura já capturada quando aplicável. A exceção fica indicada na FS.

Esta dispensa **não se aplica automaticamente à checklist SADI**. A regra atual da checklist exige as duas assinaturas por local; a interface deve explicar a diferença.

## Manutenção SADI

No código incluído, esta funcionalidade aparece em demonstração e num rascunho com Manutenção e SADI selecionados. A restrição de ambiente continua ativa e não deve ser removida apenas para conseguir executar um teste. A interface deve apresentar claramente quando é aplicável; a migração não comprova utilização operacional das checklists.

1. Definir quantos locais existem; identificar cada um com local, data, técnico e periodicidade. «Outra» precisa de descrição.
2. Responder às verificações gerais. Em cada tipo de equipamento, escolher explicitamente Sim/Não; quando Sim, definir quantidade e identificar cada unidade.
3. Preencher as verificações das centrais e repetidores. Escolher OK, NC ou NA; não deixar respostas assumidas automaticamente.
4. Justificar **cada** NC. Uma NC justificada continua a ser não conformidade técnica, mas não é um erro de preenchimento por si só.
5. Preencher periféricos e ensaios uma única vez por local, mesmo se não houver centrais/repetidores. Nas periodicidades mensal/trimestral/semestral, indicar percentagem ou áreas ensaiadas.
6. Se necessário, acrescentar fotografias do local e legendas; aguardar a preparação. Fotografias SADI entram no relatório do local. Fotografias internas da FS seguem outro fluxo e não devem ir para o relatório ao cliente.
7. Consultar as pendências e recolher assinaturas do técnico e cliente por local. Pode haver assinatura num rascunho incompleto; as pendências continuam a impedir conclusão. Alterar conteúdo assinado exige nova recolha.
8. Antes/depois dos ensaios, ler os avisos de operação apresentados no formulário. A aplicação não comprova a execução física dessas ações.

Desligar um tipo de equipamento conserva os dados para eventual reativação. Reduzir a quantidade é uma remoção explícita: escolher os elementos corretos e confirmar. Não usar redução de quantidade para esconder pendências.

## Pré-visualizar e concluir

- Consultar a FS e cada local de checklist antes de concluir. A pré-visualização deve representar o conteúdo atual, inclusive alterações por guardar. Distinguir consulta de rascunho e documento final na interface, sem sobrepor instruções de preenchimento ao relatório.
- Rever materiais, tempos, NC, fotografias e assinaturas. O código atual tem uma limitação conhecida de cinco materiais na impressão; esta melhoria continua pendente e deve ser resolvida antes da aceitação correspondente.
- Resolver campos obrigatórios e formatos inválidos. Não apagar registos para ultrapassar um erro.
- Confirmar conclusão só quando o estado mostrado corresponder ao resultado efetivo. No ambiente de desenvolvimento, o backend pode guardar, arquivar e gerar PDFs reais sobre dados fictícios; comunicações externas permanecem bloqueadas/simuladas. Indicar quais efeitos foram executados localmente e quais foram apenas simulados.
- «Pedido aceite», «em processamento», «arquivado», «envio aceite» e «entrega confirmada» são estados diferentes. Uma resposta ambígua não justifica repetir uma operação que possa duplicar efeitos.

## Quando há uma interrupção

Se a ligação falhar ou a aplicação regressar de suspensão, conservar o conteúdo e esperar pela recuperação da sessão/revisão. Uma cópia local não comprova gravação externa. Não limpar dados do navegador como primeira tentativa de recuperação de trabalho não confirmado.

Instalar como PWA facilita o acesso; o comportamento observado não garante abrir todas as folhas nem concluir trabalhos sem rede. A interface deve explicar o que está disponível no dispositivo e o que depende de serviço.
