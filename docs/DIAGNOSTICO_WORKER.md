# Diagnóstico do worker independente (M11)

O painel operacional do administrador mostra o estado do processo separado,
independentemente de a página web responder. A execução interna
`FS_GRAPH_QUEUE_IN_WEB=true` não publica neste canal. O modo recomendado em
Passenger/Docker continua a ser worker externo com essa opção a false.

## Configuração e armazenamento

Definir **o mesmo diretório privado e exclusivo desta instalação** em web e worker:

```text
FS_WORKER_HEALTH_DIR=/srv/folhas-private/worker-health
```

É uma configuração adicional, opcional em instalações existentes. Sem ela o painel
diz «Diagnóstico não configurado»; não afirma que o worker esteja parado.
O Compose entregue define `/app/logs/worker-health`, dentro do bind privado de
logs já partilhado pelos dois serviços. Não cria outro volume nem inicia serviços.
O instalador deve confirmar escrita do worker e leitura da web, idealmente sob o
mesmo UID/GID, e que essa pasta não é servida por HTTP. Novas pastas têm modo POSIX
0700; ficheiros de estado 0600. No Windows aplicar ACL privada equivalente.

O caminho tem de ser absoluto, local, sem ligações/junções e fora das pastas
APP_DATA_DIR, cache Graph, Excel, src, tools e .git. Não usar o LOG_DIR por
defeito dentro de APP_DATA_DIR para este canal. A pasta pode ser criada antes
da admissão: isso é deliberado e não cria/toca nas bases de dados recusadas.
O código não altera permissões de diretórios existentes. A configuração do
instalador e o sistema de ficheiros privado são pressupostos de confiança.

O ficheiro `worker.json` contém somente formato, hash do caminho canónico de dados,
fase, timestamps, código fixo de resultado e identidade conferida do código.
Não contém caminhos, utilizadores, identificadores de trabalho, destinatários,
mensagens, URLs nem exceções. O hash de armazenamento impede usar acidentalmente
um canal de outro caminho; não é autenticação nem prova criptográfica da origem.
Os caminhos de APP_DATA_DIR nos processos devem ser iguais, incluindo montagens.

## Estados e interpretação

| Estado | Significado |
|---|---|
| Sem arranque registado | Ficheiro ausente; pode nunca ter arrancado, ter sido removido ou nunca ter conseguido escrever |
| Arranque em curso | O processo iniciou o diagnóstico, ainda está a construir/admitir a fila |
| Ciclo em curso | Entrou no processamento; sinal periódico não prova progresso de um trabalho |
| Ativo entre ciclos | Terminou um ciclo e aguarda o próximo; a fila pode ter retenções/falhas |
| Ciclo adiado | Contrato temporariamente ocupado ou mutex da fila pertencente a outro processador |
| Processo terminado | Execução única terminou ou foi observada KeyboardInterrupt/SystemExit |
| Sem sinal recente | Passaram mais de 35 s sem observação; pode estar parado, bloqueado ou sem conseguir escrever diagnóstico |
| Versão diferente | SHA256 do worker diferente do web; identidade continua visível nos outros estados |
| Arranque recusado / terminou com erro | Código fixo distingue identidade, contrato, armazenamento ou erro genérico |
| Configuração inválida / indisponível / outro armazenamento | Não há observação fiável; nunca é apresentado como saudável |

Há heartbeat a cada 10 s, também durante um ciclo longo e no intervalo entre ciclos.
Os tempos de início/fim do ciclo permitem verificar falta de progresso apesar
do heartbeat. `--interval` aceita valores finitos entre 1 e 3600 s; o padrão é 5 s.
«Ciclo concluído» não significa que todos os trabalhos tenham sucesso nem confirma
entrega de e-mail/Teams: consultar o estado e eventual reconciliação do trabalho.
Falhas de trabalhos tratadas pela fila continuam a aparecer nos contadores/painel.

A consulta do painel é uma fotografia com hora de observação; carregar
«Atualizar diagnóstico» para obter novo estado. Não é monitorização de Plesk,
um alarme automático, medição de CPU nem prova de processo vivo por PID.
Fim abrupto (kill, queda de máquina ou SIGTERM sem tratamento) deixa o último sinal
envelhecer. A ausência de sinal recente não identifica a causa.
Na execução única é normal ver «Processo terminado», com resultado/hora do último
ciclo; não existe inferência automática da periodicidade do agendador.

Um mutex do sistema operativo protege o canal durante toda a invocação.
Uma segunda invocação configurada no mesmo canal sai com código 1 e aviso
`worker_health_owner_busy`, sem substituir o diagnóstico nem abrir outra fila.
Não apagar `writer.lock`: o OS liberta o bloqueio após morte do processo.
O mutex da própria fila mantém a proteção entre processadores sem este canal.
A aquisição efetiva desse mutex é agora refletida em `worker_acquired`.

Escritas são substituições atómicas com fsync do ficheiro. Falha de escrita
não impede o worker de processar; produz um aviso fixo
`worker_health_unavailable`, sem erro privado. O estado anterior envelhece.
Configuração inválida desativa o canal e deixa aviso fixo; não contorna a admissão.
O leitor limita o tamanho a 4096 bytes e valida/projeta campos permitidos.
Não há garantia de durabilidade do diagnóstico perante perda de energia;
os dados operacionais e decisões de envio não dependem dele.

## Consulta quando a própria web é recusada

O painel mantém autenticação Microsoft, identidade administrativa, admissão
do contrato e respostas no-store. Não abre uma exceção HTTP para dados recusados.
O instalador pode ler o canal localmente, sem importar a app, carregar .env,
abrir SQLite, construir transportes ou escrever ficheiros:

```bash
python -B tools/worker_health.py --health-dir /srv/folhas-private/worker-health --data-dir /srv/folhas-private/data
```

O exemplo anterior corresponde a uma instalação direta com esses caminhos.
**No Docker entregue, APP_DATA_DIR é /app/data**, e o hash guardado corresponde
a esse caminho visto pelo worker; não corresponde ao caminho do volume no host.
Se o contentor app ainda estiver em execução, a leitura usa:

```bash
docker compose exec app python -B tools/worker_health.py --health-dir /app/logs/worker-health --data-dir /app/data
```

Esse comando só executa o leitor; não inicia nem reinicia os serviços. Se o
contentor não estiver disponível, o instalador pode executar o leitor entregue
num host Linux, sem a app e sem montar/abrir o volume de dados:

```bash
python -B tools/worker_health.py --health-dir /var/www/vhosts/service.sensorpoint.pt/private/folhas-servico/logs/worker-health --data-dir /app/data
```

Substituir apenas o caminho de health-dir pelo FS_LOG_HOST_DIR efetivo seguido de
worker-health. Neste segundo exemplo, data-dir é a identidade canónica vista no
contentor, não uma indicação para procurar ou abrir dados no host. O cálculo usa
Path.resolve: exige o mesmo sistema de caminhos (POSIX) e /app/data sem um
redirecionamento diferente por symlink no host. Para ler uma cópia noutro sistema,
usar o leitor num ambiente com a mesma interpretação do caminho. Usar o caminho
do volume no host ou outro APP_DATA_DIR devolve wrong_storage/código 1, mesmo com
o worker ativo; isso indica canal/identidade incompatíveis, não worker parado.

Opcional: `--expected-sha256 HASH_COMPLETO` para comparar com a versão pretendida.
Saída JSON sanitizada; código 0 apenas para idle/cycling da versão esperada
(se fornecida), 1 nos restantes estados. Código 0 prova observação recente, não
progresso. Não usar esta saída, isoladamente, como autorização para reiniciar,
repetir envios ou mudar o contrato. O comando não pressupõe acesso a um servidor;
os exemplos são procedimentos a executar pelo instalador.

## Pendências e Teams

O painel lê na fila admitida a idade do mais antigo trabalho não concluído
(inclui held, pending, running e failed) e a quantidade de notify_teams não
concluídos. Ignora datas não numéricas, negativas ou futuras para a idade.
Mostra separadamente Teams em falta e conflitos já detetados pela fila/M08.
Não expõe payloads nem tenta reparar/repetir operações a partir deste diagnóstico.
As consultas são agregações de leitura; custos a 10 mil/100 mil trabalhos e
retenção continuam no M12, sem eliminação automática introduzida aqui.

## Verificação e limites

Testes sintéticos dedicados cobrem lifecycle, heartbeat durante ciclo longo,
queda real de processo/libertação do mutex, exclusão concorrente, comparação
de versão e armazenamento, dados malformados/tamanho/privacidade, recusa real
de identidade/contrato/formato com preservação das bases, contrato ocupado,
falha de escrita do diagnóstico, CLI independente, controlo de acesso e backlog.
Testes JavaScript verificam os rótulos, hora da fotografia e Teams.
As regressões existentes de admissão, identidade, formatos, propriedade da fila,
administração e privacidade são executadas antes da revisão independente.

No checkpoint original M11 não houve execução Linux nova nem sessão de navegador real. A evidência posterior está em [VALIDACAO_CI.md](VALIDACAO_CI.md) (M15 inclui M11) e [PERCURSOS_INTEGRADOS.md](PERCURSOS_INTEGRADOS.md). Cada resultado pertence ao seu SHA; não houve arranque de produção, reinício ou envio operacional.
