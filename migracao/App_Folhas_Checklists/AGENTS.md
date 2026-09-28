# Trabalho no pacote de migração

## Âmbito autorizado

Desenvolver a aplicação completa de folhas de serviço e checklists, incluindo frontend e backend dos formulários. O utilizador esclareceu que apenas a parte administrativa fica de fora. Não reconstruir nem trazer o Registo Admin, faturação ou fórmulas comerciais.

O código executável encontra-se em `aplicacao/`. O antigo diretório `frontend/` foi renomeado para reunir interface e backend sem duplicações.

## Segurança e execução

- Ler estas instruções e verificar alterações existentes antes de editar. Preservar trabalho alheio.
- Para desenvolvimento usar `python -B tools/run_test_version.py` na pasta `aplicacao/`. Para validação sem servidor usar `--check`. O lançador configura isolamento antes de importar a app, gera dados fictícios e bloqueia rede de saída.
- Não iniciar `src.main`, `passenger_wsgi` ou `src.queue_worker` para testes sem configurar previamente armazenamento e transportes isolados. Importar `src.web.application` já cria uma app.
- Não carregar credenciais operacionais em testes. O ficheiro privado JSON de transferência fica fora deste pacote e não deve ser importado automaticamente.
- Não enviar emails, Teams ou webhooks nem modificar dados reais sem autorização explícita para a operação. Ter credenciais ou uma flag `production` não autoriza envio.
- Um teste real autorizado deve inspecionar Para/CC/BCC, remetente, assunto `[TESTE]`, corpo e anexos fictícios. Não repetir pedidos de resultado ambíguo sem reconciliação.
- Não iniciar um worker contra filas operacionais. Testar filas somente com bases temporárias e transportes simulados.
- Manter `FS_MAIL_ENABLED=false`, `FS_TEAMS_NOTIFICATIONS_ENABLED=false`, `FS_GRAPH_QUEUE_IN_WEB=false`, `FS_STORAGE_BACKEND=local` nos testes. Estas flags não substituem bloqueio de rede e mocks efetivos.
- Diretórios de dados, cache, PDF, logs e filas devem ser exclusivos do teste e preferencialmente fora do OneDrive. O lançador cria uma pasta nova em cada execução.
- Não copiar registos reais, fotografias de clientes, assinaturas de clientes, tokens/cookies ou filas antigas para fixtures. A fixture Excel incluída foi gerada de raiz com dados fictícios.
- Não imprimir segredos, `.env`, cabeçalhos de autorização ou URLs de webhook. Não publicar a pasta de transferência privada.
- Não alterar a identidade Microsoft, permissões, TLS ou autenticação para contornar falhas de teste.

## Regras e verificação

- Preservar identificadores/versionamento das perguntas e campos. Não truncar dados nem remover silenciosamente registos ocultos.
- Distinguir rascunho, gravação confirmada, conflito, assinatura válida e finalização. Um token fictício não comprova assinatura real; um envio aceite não comprova entrega.
- Seguir a documentação 02–08. As propostas de melhoria não são funcionalidades já implementadas.
- Executar testes proporcionais à alteração. Base: `python -B tools/validate_migration.py` em `aplicacao/` e `node ferramentas/validar.cjs` na raiz do pacote.
- Registar adaptações ao snapshot em `proveniencia.json`; atualizar o manifesto só depois de rever alterações intencionais. Não esconder falhas mudando hashes.
- Não fazer deploy, migrações de produção ou reinícios operacionais sem autorização específica.
