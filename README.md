# Folhas de Servico

Esta árvore é a base canónica das Folhas de Serviço e checklists SADI. Consultar primeiro o [estado da entrega de 14/10/2026](docs/ESTADO_ENTREGA_2026-10-14.md): alterações aprovadas, evidência, configuração e pendências. A retoma M22 corrige a recuperação entre três abas e tem validação própria; o estado da entrega identifica o pacote ensaiado e o parecer. Não há aprovação de instalação em produção.

Para uma instalação autorizada em Plesk/Docker, usar o ZIP do commit validado com manifesto e checksum, seguindo [a entrega Docker](docs/10_ALOJAMENTO_DOCKER.md) e [a preparação do servidor](docs/IMPLEMENTACAO_SERVIDOR.md). Não instalar por cópia seletiva de ficheiros. A tag antiga `v1.0.0-servidor` e as notas SADI de setembro são referências históricas.
A SADI fica ativa por defeito com `FS_ENVIRONMENT=production`, autenticação Microsoft e backend Graph. O ficheiro `.env.production.example` inclui `FS_MAINTENANCE_ENABLED=true`. Um valor explícito `false` no servidor mantém a funcionalidade desligada. São necessários Chromium e armazenamento privado persistente em `FS_APP_DATA_DIR`.
Para a migracao do sincronismo transacional, seguir tambem `docs/SINCRONIZACAO_V2_ADMIN.md`.
Para localizacao, rotacao e operacao dos logs, seguir `docs/LOGGING.md`.

Para manutenção do estado de edição, consultar [Quarentena de edição — M04](docs/QUARENTENA_EDICAO.md):
comportamento, mapa dos componentes, diagnóstico de cópias antigas, testes e limites.
Para cancelamento, consultar [Cancelamento recuperável — M05](docs/CANCELAMENTO_RECUPERAVEL.md):
cópia local antes do commit, movimento remoto por identidade e tratamento de resultados incertos.
Para converter formatos antigos, consultar [Migração offline — M06](docs/MIGRACAO_OFFLINE.md):
inventário, cópia privada, aplicação recuperável e quarentena sem repetição de envios.
Para cópias e recuperação, consultar [Backup/restauro offline — M07](docs/BACKUP_RESTAURO.md):
cópia consistente, chave separada, restauro verificado e bloqueio até reconciliação.
Para gerar uma entrega do commit exato, consultar [Identidade e pacote — M13](docs/IDENTIDADE_DA_VERSAO.md).
O arranque operacional exige manifesto; desenvolvimento usa o lançador sintético.
A entrega Docker (M14) usa esse artefacto. Os ensaios sintéticos Linux/Docker foram executados nos commits identificados em [Validação CI](docs/VALIDACAO_CI.md). A [nota ao instalador](docs/VALIDACAO_DOCKER_INSTALADOR.md) conserva os procedimentos e a verificação específica do destino AlmaLinux.
Os testes locais não aprovam uma instalação real.

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
`special_admin` apenas quando o email e os GUIDs Microsoft de tenant e objeto coincidem com `FS_ADMIN_TENANT_ID` e `FS_ADMIN_OBJECT_ID`. Configuração ausente ou sessão antiga sem identidade completa recusa o privilégio; ver [identidade administrativa](docs/IDENTIDADE_ADMINISTRATIVA.md). O acesso SADI
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
de `editing-state.sqlite3`. Instalações novas criam o formato atual; bases antigas exigem preparação offline M06, sem criação ou conversão implícita do legado.

Na revisão de 07/10/2026, as respostas atrasadas de gravação ou retoma deixaram de
reativar uma página expulsa ou de limpar a sua recuperação pendente. Uma resposta
de verificação de uma sessão antiga também não afeta a sessão nova; estas
verificações não se sobrepõem. A rota antiga `/api/graph/jobs/<id>/retry` passa
pelas mesmas proteções do painel: apenas o administrador Microsoft, CSRF, motivo
obrigatório, avaliação de envio incerto e auditoria. Integrações que usavam essa
rota sem estes requisitos devem passar a usar o painel administrativo.

O registo de 07/10 descrevia também alterações no pacote de demonstração. A consolidação posterior é feita nesta árvore canónica; não pressupor igualdade com `App_Folhas_Checklists/aplicacao` nem copiar uma árvore sobre a outra. Não houve deploy, reinício operacional ou envio real nesta sequência.

## Revisão da sincronização de 07/10/2026

O log recebido confirma erros 500 quando um refresh retira um Excel entre a
enumeração e a leitura do resumo. A listagem tolera agora essa remoção, volta a
conferir o inventário antes da resposta e preserva os outros ficheiros. Permissões
inválidas e Excel existentes mas inválidos não são tratados como remoções.

Quando a espera da interface excede 20 segundos, consulta-se ainda a lista do
último inventário, preservando o formulário aberto e o aviso de confirmação
pendente. Este caso foi reproduzido em teste; os refreshes do log recebido foram
inferiores a quatro segundos, pelo que não explicam esses incidentes específicos.

Com `FS_LOG_REQUESTS=true`, recusas passam a incluir número de folha, referência
anónima, código de erro e contagens de campos em falta/inválidos, quando disponíveis.
Não são registados valores do documento, nomes de clientes, anexos ou tokens.
Os HTTP 400 históricos de rascunho/envio ainda exigem a mensagem vista pelo técnico.
Estas alterações locais não confirmam uma instalação ou correção no servidor.

## Validação Linux/Docker

Procedimento, âmbito e resultados em [docs/VALIDACAO_CI.md](docs/VALIDACAO_CI.md).
