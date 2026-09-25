# Notas da versão — checklists SADI (2026-09-25)

## Âmbito do pacote

Esta versão acrescenta as checklists de manutenção SADI do modelo Excel à demo local e, mediante ativação explícita, ao servidor de produção. Em produção exige `FS_MAINTENANCE_ENABLED=true`, autenticação Microsoft e backend Graph.

- Depois do rascunho, a folha de serviço e as checklists aparecem em separadores. Cada local tem configuração, respostas, fotografias, observações e assinaturas próprias do técnico e do cliente.
- Centrais convencionais, endereçáveis e repetidores são repetidos por equipamento. O campo «Local do repetidor» é obrigatório. A secção 4, Periféricos e ensaios, é independente das centrais e aparece uma vez por local.
- Respostas OK/NC/NA, justificação obrigatória de NC, periodicidade, cobertura condicional dos ensaios, quantidades e assinaturas são validadas no navegador e no servidor. As assinaturas são invalidadas quando o conteúdo assinado muda.
- A pré-visualização apresenta a folha e cada checklist em separadores. A finalização gera um PDF da folha e um por local. Uma falha de geração preserva o rascunho.
- O email ao cliente inclui **só o PDF da folha de serviço**. Na demo, os PDFs das checklists ficam no conjunto arquivado; em produção ficam na pasta privada da app, disponíveis apenas para as contas autorizadas. A simulação da demo não envia comunicação.
- O modelo visual da checklist acompanha o da folha de serviço. As orientações de preenchimento aparecem no formulário, sem poluir o relatório final.
- Cada local permite registar «Cliente não presente na obra» e «Técnico presente, sem assinatura». Cada opção dispensa só a assinatura correspondente, fica identificada no PDF e é independente da folha de serviço e dos outros locais. A identificação do técnico e as restantes validações continuam obrigatórias. Alterar uma destas opções invalida as assinaturas anteriores desse local.

## Verificação isolada

- `python -B -m pytest -q`: suite Python aprovada, incluindo testes SADI e de acesso em produção com serviços simulados.
- Suite JavaScript (`node --test` em todos os ficheiros `tests/js/*.test.cjs`): 19 testes aprovados, incluindo 8 SADI.
- `python -B tools/run_test_version.py --check`: `CHECK_OK`, com dados fictícios, rede de saída bloqueada e email, Teams e workers desligados.
- `python -B tools/verify_sadi_pdf_demo.py`: três PDFs gerados; simulação com um anexo (folha de serviço) e duas checklists guardadas, sem comunicação externa.
- O pacote foi montado sobre a `main` local num worktree separado. Inclui apenas a SADI e os pré-requisitos de validação, edição e finalização atómica; alterações pendentes de administração, desempenho, registos, relatórios e artefactos temporários ficaram fora.

## Acesso reservado e armazenamento

Apenas **acarvalho@sensorpoint.pt** e **jribeiro@sensorpoint.pt** podem abrir, preencher, validar, assinar e consultar as checklists/PDFs SADI. O servidor aplica esta restrição às respostas e rotas diretas. Os restantes técnicos podem editar a folha de serviço, sem receber os dados SADI; as validações de finalização continuam a exigir a checklist completa. Os dois utilizadores autorizados podem editar a checklist de um rascunho SADI criado por outro técnico, mantendo o controlo de revisão.

Em produção, os dados da checklist e os PDFs ficam em `FS_APP_DATA_DIR/sadi`, fora da pasta pública e da sincronização SharePoint. O arquivo Graph continua a receber a folha de serviço e os seus artefactos habituais, sem o JSON ou PDFs SADI. A demo local mantém o seu armazenamento temporário isolado.

## O que alterar no servidor

Fazer backup consistente de `FS_APP_DATA_DIR`, do arquivo e da configuração atual. Garantir que `FS_APP_DATA_DIR` é persistente entre deploys, privado, gravável pela app e incluído no backup. A pasta `sadi` guarda as checklists e PDFs; `editing-state` guarda rascunhos em curso.

Instalar Chromium no host Plesk/Passenger e, se não estiver no `PATH`, definir `FS_PDF_BROWSER_PATH` para o executável. O Dockerfile desta versão já o inclui. Validar que o utilizador da app consegue gerar um PDF de teste. Manter `FS_ENVIRONMENT=production`, `FS_STORAGE_BACKEND=graph`, `FS_AUTH_PROVIDER=microsoft` e `FS_GRAPH_QUEUE_IN_WEB=false`; não configurar `FS_TEST_SYNTHETIC=true` no servidor.

A dependência de finalização atómica também altera o fluxo comum de arquivo das folhas de serviço, mesmo com a checklist desativada. Validar esse fluxo em ambiente de ensaio antes de publicar o código no servidor.

Atualizar app e worker para a mesma revisão, conservando volumes e configuração. Depois de validar armazenamento, permissões, PDFs e backup, definir `FS_MAINTENANCE_ENABLED=true` e reiniciar a app pelo procedimento normal. A flag vem desligada por defeito. Não é necessária migração do esquema Graph nem nova permissão Microsoft. Esta preparação não executa deploy nem processa filas.
