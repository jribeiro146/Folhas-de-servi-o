# Notas da versão — Checklists de manutenção SADI · 25/09/2026

## Âmbito do pacote

Esta é a versão de produção das checklists de manutenção SADI do modelo Excel. A SADI fica ativa por defeito em `FS_ENVIRONMENT=production`, com autenticação Microsoft e backend Graph. O exemplo de configuração de produção inclui `FS_MAINTENANCE_ENABLED=true`; um valor explícito `false` no servidor mantém a funcionalidade desligada. O lançador de testes é separado e não é usado na instalação de produção.

- Depois do rascunho, a folha de serviço e as checklists aparecem em separadores. Cada local tem configuração, respostas, fotografias, observações e assinaturas próprias do técnico e do cliente.
- Centrais convencionais, endereçáveis e repetidores são repetidos por equipamento. O campo «Local do repetidor» é obrigatório. A secção 4, Periféricos e ensaios, é independente das centrais e aparece uma vez por local.
- Fotografias e observações finais aparecem antes das assinaturas, sem acrescentar numeração à do Excel. Os campos de observações e justificação começam numa linha e crescem com o texto. O campo «Hora de saída» foi retirado.
- Respostas OK/NC/NA, justificação obrigatória de NC, periodicidade, cobertura condicional dos ensaios, quantidades e assinaturas são validadas no navegador e no servidor. As assinaturas são invalidadas quando o conteúdo assinado muda.
- A pré-visualização apresenta a folha e cada checklist em separadores. A finalização gera um PDF da folha e um por local. Uma falha de geração preserva o rascunho.
- O email ao cliente inclui **só o PDF da folha de serviço**. Na demo, os PDFs das checklists ficam no conjunto arquivado; em produção ficam na pasta privada da app, disponíveis apenas para as contas autorizadas. A simulação da demo não envia comunicação.
- O modelo visual da checklist acompanha o da folha de serviço. As orientações de preenchimento aparecem no formulário, sem poluir o relatório final.
- Cada local permite registar «Cliente não presente na obra», dispensando só a assinatura do cliente. A opção fica identificada no PDF e é independente da folha de serviço e dos outros locais. **A identificação e a assinatura do técnico são sempre obrigatórias na checklist**, sem opção de dispensa. Esta regra não altera as assinaturas da folha de serviço. Alterar a ausência do cliente invalida as assinaturas anteriores desse local.
- É possível recolher assinaturas durante o preenchimento; os campos em falta bloqueiam a finalização. «Verificar checklist» apresenta as pendências. Uma NC justificada permite concluir a checklist.

## Gravação e compatibilidade

Os rascunhos podem ser guardados e retomados por local, com respostas e equipamentos independentes. Aumentar quantidades acrescenta blocos; reduzir exige escolher os blocos a remover e confirmar quando contêm dados. Desativar um equipamento preserva as respostas no rascunho e exclui-o da validação e do PDF.

Folhas antigas sem checklists continuam compatíveis. A definição versionada centraliza as perguntas, condições e validações. Alterar conteúdo assinado exige novas assinaturas nos locais afetados. Os pré-requisitos de edição, controlo de revisão e finalização atómica fazem parte deste pacote; impedem sobrescritas e permitem recuperar de falhas de geração.

## Verificação isolada

- `python -B -m pytest -q`: 286 testes aprovados, incluindo ativação SADI por defeito em produção, desativação explícita, acesso reservado, assinatura obrigatória do técnico e arquivo com serviços simulados.
- Suite JavaScript (`node --test` em todos os ficheiros `tests/js/*.test.cjs`): 19 testes aprovados, incluindo 8 SADI.
- `python -B tools/run_test_version.py --check`: `CHECK_OK`, com dados fictícios, rede de saída bloqueada e email, Teams e workers desligados.
- `python -B tools/verify_sadi_pdf_demo.py`: três PDFs gerados; simulação com um anexo (folha de serviço) e duas checklists guardadas, sem comunicação externa.
- Verificação manual no navegador: ausência do cliente dispensa apenas a assinatura desse cliente; o cartão do técnico continua disponível e a finalização exige a sua assinatura.
- O pacote foi montado sobre a `main` num worktree separado. Inclui apenas a SADI e os pré-requisitos de validação, edição e finalização atómica; alterações pendentes de administração, desempenho, registos, relatórios e artefactos temporários ficaram fora.

## Acesso reservado e armazenamento

Apenas **acarvalho@sensorpoint.pt** e **jribeiro@sensorpoint.pt** podem abrir, preencher, validar, assinar e consultar as checklists/PDFs SADI. O servidor aplica esta restrição às respostas e rotas diretas. Os restantes técnicos podem editar a folha de serviço, sem receber os dados SADI; as validações de finalização continuam a exigir a checklist completa. Os dois utilizadores autorizados podem editar a checklist de um rascunho SADI criado por outro técnico, mantendo o controlo de revisão.

Em produção, os dados da checklist e os PDFs ficam em `FS_APP_DATA_DIR/sadi`, fora da pasta pública e da sincronização SharePoint. O arquivo Graph continua a receber a folha de serviço e os seus artefactos habituais, sem o JSON ou PDFs SADI. A demo local mantém o seu armazenamento temporário isolado.

## O que alterar no servidor

Fazer backup consistente de `FS_APP_DATA_DIR`, do arquivo e da configuração atual. Garantir que `FS_APP_DATA_DIR` é persistente entre deploys, privado, gravável pela app e incluído no backup. A pasta `sadi` guarda as checklists e PDFs; `editing-state` guarda rascunhos em curso.

Instalar Chromium no host Plesk/Passenger e, se não estiver no `PATH`, definir `FS_PDF_BROWSER_PATH` para o executável. O Dockerfile desta versão já o inclui. Validar que o utilizador da app consegue gerar um PDF de teste. Manter `FS_ENVIRONMENT=production`, `FS_STORAGE_BACKEND=graph`, `FS_AUTH_PROVIDER=microsoft` e `FS_GRAPH_QUEUE_IN_WEB=false`; não configurar `FS_TEST_SYNTHETIC=true` no servidor.

A dependência de finalização atómica também altera o fluxo comum de arquivo das folhas de serviço, mesmo com a checklist desativada. Validar esse fluxo em ambiente de ensaio antes de publicar o código no servidor.

Preparar Chromium, armazenamento privado, permissões e backup antes da atualização. Atualizar app e worker para a mesma revisão, conservando volumes e configuração, e reiniciar pelo procedimento normal. A SADI já fica ativa por defeito em produção; se a configuração existente tiver `FS_MAINTENANCE_ENABLED=false`, alterar para `true`. Atualizar o código não substitui as variáveis existentes do Plesk nem o seu ficheiro de segredos. Não é necessária migração do esquema Graph nem nova permissão Microsoft. Esta preparação não executa deploy nem processa filas.
