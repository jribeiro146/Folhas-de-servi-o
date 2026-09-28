# Folhas de Servico

## Entrega de migração — 28/09/2026

O [pacote de migração revisto](migracao/App_Folhas_Checklists/README.md) está em `migracao/`. Inclui melhorias de SharePoint, interface móvel, SADI de demonstração, PDF e preparação Docker/worker, com validação e revisão independente documentadas.

A [nota de passagem ao implementador Plesk](migracao/ENTREGA_PLESK_2026-09-28.md) contém o comentário de entrega, o contexto de construção da imagem e as verificações necessárias. Este pacote deve ser comparado com a versão efetivamente instalada antes de qualquer integração ou instalação; adicioná-lo ao repositório não atualiza a aplicação da raiz nem o servidor.

## Aplicação existente na raiz

Versao recomendada para servidor: `v1.0.0-servidor`.

Para deploy em Plesk/Docker, usar a tag acima e seguir `docs/IMPLEMENTACAO_SERVIDOR.md`.
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
4. Nao gerar PDF na webapp.
5. Preferir `http://localhost:5001` ou a proxima porta livre.

Notas de consistencia:

- `src/web/` e a unica webapp ativa do projeto.
- Dados locais, `.env`, Excels operacionais, releases geradas e caches nao fazem parte da versao limpa.
- Para servidor Linux/Plesk/Docker usar `requirements-server.txt`.
- Os logs operacionais ficam em `FS_APP_DATA_DIR/logs`, fora do codigo e do document root.
