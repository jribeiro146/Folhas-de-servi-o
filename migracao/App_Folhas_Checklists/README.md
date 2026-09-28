# Migração — aplicação completa de folhas de serviço e checklists

Âmbito corrigido em 28/09/2026: **inclui frontend e backend real dos formulários**. Fica excluída apenas a aplicação administrativa — registo administrativo, faturação, grelhas/fórmulas comerciais e ferramentas respetivas.

## Começar na nova pasta

1. Copiar esta pasta completa `App_Folhas_Checklists`. A pasta `aplicacao/` contém agora o projeto executável autónomo; substitui a antiga pasta chamada `frontend/`.
2. Usar Python 3.13 (versão validada nesta preparação), preferencialmente num ambiente virtual. Na pasta `aplicacao/`, instalar as dependências se necessário:

   ```powershell
   python -m pip install -r requirements-dev.txt
   ```

3. Validar o arranque isolado, sem abrir um servidor nem contactar serviços externos:

   ```powershell
   python -B tools/run_test_version.py --check
   ```

4. Para utilizar a aplicação completa com dados fictícios e backend local:

   ```powershell
   python -B tools/run_test_version.py --port 5012
   ```

   Abrir `http://127.0.0.1:5012`. Este lançador exclui o `.env`, limpa as variáveis operacionais herdadas, cria dados numa pasta temporária nova e bloqueia ligações de saída. Não precisa de credenciais. Para terminar, usar Ctrl+C. Cada arranque cria uma demonstração nova; não é armazenamento permanente.
5. Executar os testes Python isolados:

   ```powershell
   python -B tools/validate_migration.py
   ```

6. Na raiz do pacote, com Node.js 22 ou superior, conferir integridade e testes JavaScript:

   ```powershell
   node ferramentas/validar.cjs
   ```

O [laboratório de regras](laboratorio/index.html) continua disponível como recurso auxiliar sem servidor; não substitui a aplicação completa em `aplicacao/`.

## Conteúdo

| Local | Conteúdo |
|---|---|
| `aplicacao/src/web/` | Flask, rotas, templates, interface JS/CSS, PWA, logótipo e recursos de apresentação |
| `aplicacao/src/services/` | Excel/LINK, gravação/rascunhos, revisão e recuperação, assinaturas, fotografias, arquivo, PDF, Graph, email, Teams e filas |
| `aplicacao/src/document_schema.py`, `maintenance_schema.py`, `field_map.py` | Regras reais do documento/checklist e contrato LINK |
| `aplicacao/tools/` | Arranque seguro, isolamento, geração de fixture sintética e testes da migração |
| `aplicacao/tests/` | Testes Python e JavaScript; fixture Excel sintética sem dados de clientes |
| `aplicacao/requirements*.txt` | Dependências da app, servidor e desenvolvimento |
| `aplicacao/Dockerfile`, `docker-compose.yml`, `passenger_wsgi.py` | Entradas e referência de alojamento, a adaptar ao destino; não executadas nesta entrega |
| `dados/` | Catálogos exportados, 34 perguntas SADI, mapa LINK, documento vazio e oito cenários fictícios |
| `documentacao/` | Inventário, regras, contratos, melhorias, plano, utilização, validação e backend incluído |
| `proveniencia.json`, `manifest-sha256.json` | Origem, adaptações e integridade do pacote |
| `AGENTS.md` | Regras de desenvolvimento e isolamento |

## Backend e credenciais

O backend dos formulários **está incluído**. Não é necessário reconstruí-lo só para usar esta base noutra pasta. As melhorias documentadas continuam a ser trabalho futuro sobre a base existente.

As credenciais disponíveis já foram transferidas separadamente para `Migração/Configuracao_Privada`, conforme pedido do utilizador. Essa pasta não pertence ao código público, não é carregada pelo teste e nunca deve entrar no frontend ou na imagem Docker. Para uma integração real, seguir o README dessa pasta e configurar os serviços do destino explicitamente.

## Limites conhecidos

As melhorias de 28/09/2026 estão descritas em [09_MELHORIAS_E_VALIDACAO.md](documentacao/09_MELHORIAS_E_VALIDACAO.md). A pasta `aplicacao/` é a única fonte executável: foram removidas 37 cópias idênticas que restavam em `frontend/`, após comparação por SHA-256 e registo da base em Git.

- A cópia reflete a árvore de trabalho local, incluindo alterações preexistentes; não certifica a versão instalada no servidor.
- A checklist SADI nesta origem local continua condicionada à demonstração isolada. Copiar o backend não a ativa automaticamente em produção.
- Os caminhos de dados por defeito foram adaptados apenas nesta cópia para não apontarem para a instalação original. Os testes usam sempre diretórios temporários novos fora do OneDrive.
- O catálogo de técnicos e as fixtures de desenvolvimento são fictícios. A imagem corporativa usada no rodapé dos emails foi incluída por ser uma dependência do serviço; não foram copiadas assinaturas de clientes.
- A PWA existente não oferece trabalho integral offline. A impressão inclui agora os 12 materiais suportados, com continuação da tabela e identificação nas páginas.
- PDF local requer Chrome/Chromium/Edge instalado. O Dockerfile passou a instalar Chromium e o Compose inclui um worker separado; a imagem ainda precisa de ser construída e testada num ambiente com Docker. Seguir o [guia de alojamento](documentacao/10_ALOJAMENTO_DOCKER.md).
- Código de integrações incluído não significa credenciais validadas, envio real testado ou autorização para ligar a dados operacionais.

Consultar [validação da entrega](documentacao/07_VALIDACAO_DA_ENTREGA.md) para os resultados e limites efetivamente observados.
