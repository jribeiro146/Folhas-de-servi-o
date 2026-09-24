# Notas da versão — demo SADI (2026-09-24)

## Âmbito do pacote

Esta versão acrescenta à **demo local isolada** as checklists de manutenção SADI do modelo Excel. A funcionalidade só é ativada quando o processo está em modo de teste sintético, com armazenamento local e sem email, Teams, Graph ou filas operacionais. Um deploy deste commit no servidor de produção **não ativa** as checklists para os técnicos.

- Depois do rascunho, a folha de serviço e as checklists aparecem em separadores. Cada local tem configuração, respostas, fotografias, observações e assinaturas próprias do técnico e do cliente.
- Centrais convencionais, endereçáveis e repetidores são repetidos por equipamento. O campo «Local do repetidor» é obrigatório. A secção 4, Periféricos e ensaios, é independente das centrais e aparece uma vez por local.
- Respostas OK/NC/NA, justificação obrigatória de NC, periodicidade, cobertura condicional dos ensaios, quantidades e assinaturas são validadas no navegador e no servidor. As assinaturas são invalidadas quando o conteúdo assinado muda.
- A pré-visualização apresenta a folha e cada checklist em separadores. A finalização da demo gera um PDF da folha e um por local, guardados no conjunto arquivado. Uma falha de geração preserva o rascunho.
- A simulação de email ao cliente inclui **só o PDF da folha de serviço**. Os PDFs das checklists ficam na pasta do serviço, disponíveis para consulta e download na demo. A simulação não envia comunicação.
- O modelo visual da checklist acompanha o da folha de serviço. As orientações de preenchimento aparecem no formulário, sem poluir o relatório final.

## Verificação isolada

- `python -B -m pytest -q`: 265 testes aprovados, incluindo 58 testes SADI.
- Suite JavaScript (`node --test` em todos os ficheiros `tests/js/*.test.cjs`): 17 testes aprovados, incluindo 6 SADI.
- `python -B tools/run_test_version.py --check`: `CHECK_OK`, com dados fictícios, rede de saída bloqueada e email, Teams e workers desligados.
- `python -B tools/verify_sadi_pdf_demo.py`: três PDFs gerados; simulação com um anexo (folha de serviço) e duas checklists guardadas, sem comunicação externa.
- O pacote foi montado sobre a `main` local num worktree separado. Inclui apenas a SADI e os pré-requisitos de validação, edição e finalização atómica; alterações pendentes de administração, desempenho, registos, relatórios e artefactos temporários ficaram fora.

## Estado do acesso reservado

A restrição das checklists a **acarvalho@sensorpoint.pt** e **jribeiro@sensorpoint.pt** foi definida como requisito, mas **não está implementada neste pacote**. A demo atual usa uma identidade fictícia comum às sessões. Esconder o separador não seria um controlo de acesso. Antes de disponibilizar checklists em produção, é necessário autorizar por identidade Microsoft no servidor e em todas as respostas/API/PDFs, preservar os dados ao guardar a folha por outros técnicos e guardar os artefactos numa área SharePoint com permissões próprias.

## O que alterar no servidor

Para este pacote de **demo**, não alterar `.env`, permissões Microsoft/SharePoint, destinos de email, flags de integração nem a configuração do worker. Manter `FS_ENVIRONMENT=production`, `FS_STORAGE_BACKEND=graph`, `FS_AUTH_PROVIDER=microsoft` e as restantes definições operacionais como já estiverem configuradas. Não configurar `FS_TEST_SYNTHETIC=true` nem iniciar `tools/run_test_version.py` no servidor de produção para expor esta funcionalidade.

A dependência de finalização atómica também altera o fluxo comum de arquivo das folhas de serviço, mesmo com a checklist desativada. Validar esse fluxo em ambiente de ensaio antes de publicar o código no servidor.

Se o código for publicado no servidor, atualizar a app e o worker para a mesma revisão, conservando os volumes e a configuração efetiva. Esta publicação de código, por si, deixa a checklist desativada em produção. Não há nova dependência de servidor nem migração de dados neste pacote. Antes de qualquer atualização operacional, fazer um backup consistente dos dados e preparar a recuperação; esta preparação não executa deploy nem processa filas.
