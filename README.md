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

## Administração operacional

A conta Microsoft autenticada `jribeiro@sensorpoint.pt` recebe o papel
`special_admin`, calculado no servidor pelo endereço completo. O acesso SADI
existente mantém-se. Abrir **Administração operacional** na barra lateral ou
`/admin` para consultar reservas, gravações automáticas, fila e histórico.

**Expulsar e libertar** revoga as sessões e a propriedade da folha e impede a
reentrada dos utilizadores expulsos nessa folha até **Repor acesso**. A restauração
não recupera tokens antigos nem retira a reserva de outro técnico. Mesmo uma aba
antiga do administrador fica revogada; o seu acesso ao painel permanece disponível.
As gravações no servidor são preservadas. Alterações ainda só no dispositivo não
são transferidas pelo desbloqueio: contactar o técnico antes de intervir.

O browser verifica a sessão a cada 15 segundos quando está visível. Escritas com
uma sessão revogada são recusadas imediatamente no servidor. Cada intervenção
exige motivo e CSRF; a libertação e a auditoria são uma transação SQLite conjunta.
Uma lista desatualizada, uma escrita ou finalização em curso impedem a libertação.

O painel permite repetir falhas conhecidas e paradas e pedir atualização Graph.
Envios incertos, resultados sem comprovativo e dados inválidos continuam retidos
para revisão. Não há alterações às permissões Entra, às credenciais ou aos dados
comerciais. O painel não funciona sem autenticação Microsoft e não divulga tokens
de edição nem conteúdo dos autosaves. A tabela `admin_actions` integra o backup
de `editing-state.sqlite3` e é criada no primeiro arranque.

Na revisão de 07/10/2026, as respostas atrasadas de gravação ou retoma deixaram de
reativar uma página expulsa ou de limpar a sua recuperação pendente. Uma resposta
de verificação de uma sessão antiga também não afeta a sessão nova; estas
verificações não se sobrepõem. A rota antiga `/api/graph/jobs/<id>/retry` passa
pelas mesmas proteções do painel: apenas o administrador Microsoft, CSRF, motivo
obrigatório, avaliação de envio incerto e auditoria. Integrações que usavam essa
rota sem estes requisitos devem passar a usar o painel administrativo.

A implementação foi aplicada também ao pacote de migração, preservando o perfil
de demonstração. Não houve deploy, reinício operacional ou envio real.
