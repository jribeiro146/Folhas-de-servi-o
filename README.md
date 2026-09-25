# Folhas de Servico

Esta revisão inclui a versão de produção das checklists de manutenção SADI.

Para instalar em Plesk/Docker, publicar esta revisão e seguir [as notas de versão SADI](docs/RELEASE_NOTES_SADI_DEMO_2026-09-24.md) e `docs/IMPLEMENTACAO_SERVIDOR.md`. A tag antiga `v1.0.0-servidor` não inclui estas alterações.
A SADI fica ativa por defeito com `FS_ENVIRONMENT=production`, autenticação Microsoft e backend Graph. O ficheiro `.env.production.example` inclui `FS_MAINTENANCE_ENABLED=true`. Um valor explícito `false` no servidor mantém a funcionalidade desligada. São necessários Chromium e armazenamento privado persistente em `FS_APP_DATA_DIR`.
Para a migracao do sincronismo transacional, seguir tambem `docs/SINCRONIZACAO_V2_ADMIN.md`.
Para localizacao, rotacao e operacao dos logs, seguir `docs/LOGGING.md`.

Estrutura principal do projeto:

- `src/`: codigo da aplicacao.
- `src/web/`: webapp oficial, templates e assets ativos.
- `src/services/`: leitura/escrita Excel e arquivo.
- `tests/`: testes automatizados e fixtures.
- `docs/`: documentacao atual de apoio, servidor, Graph e login Microsoft.
- `Dockerfile` e `docker-compose.yml`: execucao em servidor/container.
- `.env.example` e `.env.production.example`: exemplos de configuracao sem segredos.

Pontos de entrada:

- `src/main.py`: arranque da aplicacao local.
- `src/web/application.py`: webapp oficial em Flask.
- `passenger_wsgi.py`: entrada WSGI para Plesk/Passenger/Gunicorn.
- `Dockerfile`: entrada containerizada com Gunicorn.

Fluxo oficial atual:

1. Ler folhas ativas a partir de `Excel/Activas` em modo local ou Microsoft Graph em servidor.
2. Editar apenas a sheet `LINK`.
3. Guardar rascunho ou arquivar/cancelar o Excel.
4. Gerar o PDF da folha e, em Manutenção + SADI, um PDF privado por local. O email ao cliente inclui apenas a folha de serviço.
5. Aceder à app pelo endereço HTTPS configurado no servidor. As checklists são visíveis apenas para as contas Microsoft autorizadas.

O lançador `tools/run_test_version.py` é exclusivo para desenvolvimento: cria dados fictícios e bloqueia comunicações. Não é usado pelo Plesk/Passenger nem pelo Dockerfile de produção.

Notas de consistencia:

- `src/web/` e a unica webapp ativa do projeto.
- Dados locais, `.env`, Excels operacionais, releases geradas e caches nao fazem parte da versao limpa.
- Para servidor Linux/Plesk/Docker usar `requirements-server.txt`.
- Os logs operacionais ficam em `FS_APP_DATA_DIR/logs`, fora do codigo e do document root.
