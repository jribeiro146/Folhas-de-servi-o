# Validação Linux/Docker e rastreabilidade

## Objetivo e autorização

Em 12/10/2026, João autorizou publicar uma branch de testes e usar GitHub Actions,
pois este computador não suporta uma VM local. O processo não instala nada no PC
nem faz deploy, merge na main ou operações no Plesk. Backup, .env operacional,
credenciais e dados de clientes não entram no GitHub, nas imagens ou nos pareceres.

Base desta etapa: `4ae35db0a28597577bdde2791bb4122fb6471df2`, P1 local aprovado.
Código e documentação da aplicação interna continuam a ser revistos pelo Claude,
com testes proporcionais, uma alteração de cada vez e até três rondas técnicas.
Falha de serviço/quota não é aprovação nem motivo para repetir sem limite.

## O que corre

Workflow `.github/workflows/production-validation.yml`, em runners GitHub Ubuntu
24.04 descartáveis. Os pushes em `codex/validacao-linux-*` iniciam a execução.
Também admite PR e execução manual; esta última depende de o workflow existir na
branch por defeito. Não é necessário criar um PR ou alterar main para este ensaio.

- JavaScript: bateria Node 22, uma vez por execução.
- Imagem padrão: UID/GID 10001:10001, Compose do runner.
- Imagem compatível com o dono dos dados comunicado: UID/GID 100:101, Compose 2.27.0.
- Cada imagem usa o ZIP gerado do HEAD limpo e valida o manifesto no build.
- `tools/validate_docker.sh` verifica identidade, init vazio, factories web/worker,
  recusa de dados legados com preservação, migração offline, reposição pré-M06,
  adulteração recusada, backup/verify/activate/restauro e libertação sintética.
- A bateria Python completa corre no target qa derivado da imagem runtime, como
  utilizador sem privilégios e com rede desligada, incluindo os casos POSIX.
- Chromium gera e verifica um PDF real. Compose normalizado é verificado pela
  mesma ferramenta usada pelo instalador, com caso válido e recusa de bind true.

Só os builds descarregam dependências. Todos os `docker run` usam `--network none`,
sem configuração ou montagens operacionais. Os volumes têm nomes aleatórios e
são novos; a chave do ensaio é gerada de raiz, separada e montada só para leitura.
Nenhuma reconciliação automática é feita sobre operações reais. O runner é
eliminado pelo GitHub; em execução manual, o script preserva os recursos fictícios
para inspeção, identificados em `resources-prefix.txt`. Nunca faz prune ou down -v.

## Evidência e critérios

[Execuções no GitHub](https://github.com/jribeiro146/Folhas-de-servi-o/actions/workflows/production-validation.yml)

O histórico Git identifica alterações; cada run mostra commit, jobs, duração e
resultado. Os anexos de 30 dias incluem identificação do ZIP/source/image, versões
Docker/Compose/kernel, JSON dos comandos, relatório pytest/JUnit e resultado PDF.
O job publica evidência também em falha; uma falha anterior à produção de um
ficheiro pode deixar esse ficheiro ausente. Não se publica a chave, volumes ou
configuração expandida. Logs completos de build ficam no próprio run.

Aceitar apenas o SHA exato com todos os jobs aprovados, zero testes falhados,
skips explicados e ensaios POSIX executados. Falhas param a aceitação; investigar,
registar a causa e rever/testar a correção antes de novo push. Não há rerun
periódico. Um commit novo cancela o run anterior da mesma branch. Limites por job:
10 minutos JavaScript, 35 minutos por identidade Docker.

Este documento descreve o procedimento. A tabela abaixo regista os resultados
observados; o link do run é a evidência, não uma promessa de resultado.

| Data | Revisão/etapa | Resultado observado |
|---|---|---|
| 12/10/2026 | Base 4ae35db0; preparação da validação CI | Linux/Docker pendente; ainda não publicado nesta etapa |
| 12/10/2026 | Verificador e pacote, Windows | 52 aprovados / 1 ignorado; Node: 79 aprovados; actionlint e Bash sem erros |
| 12/10/2026 | Bateria Python Windows | 1084 aprovados / 4 ignorados / 1 falha: WinError 5 ao renomear o bundle privado SADI; código desse fluxo inalterado |
| 12/10/2026 | Diagnóstico SADI sem alteração | Grupo completo: 24 aprovados / 1 ignorado. Falha não reproduzida; causa do bloqueio Windows por confirmar, log original preservado |

## Reprodução pelo instalador e limites

Partindo de um ZIP verificado e extraído, executar no host isolado:

```bash
bash tools/validate_docker.sh /caminho/absoluto/release /tmp/evidencia-nova 10001 10001
# Nova pasta de evidência na segunda execução:
bash tools/validate_docker.sh /caminho/absoluto/release /tmp/evidencia-hs4-nova 100 101
```

A máquina precisa de Bash, Python 3 e Docker/Compose v2. Não usar o daemon de
produção. O roteiro detalhado está em [VALIDACAO_DOCKER_INSTALADOR.md](VALIDACAO_DOCKER_INSTALADOR.md).
O verificador aceita o false omitido pelo compose-go 2.1/Compose 2.27 no JSON
**normalizado**, sem aceitar true, strings, null ou código montado por bind.

Ubuntu no GitHub não reproduz kernel 4.18, cgroups v1 e seccomp do AlmaLinux de
produção. O profissional ainda confirma o smoke PDF nesse destino, com HOME
gravável e FS_PDF_BROWSER_NO_SANDBOX=true conforme reportado, mais acessos Microsoft,
volumes reais, backup e procedimento de instalação. Não se declara produção
validada por um run sintético. Builds futuros podem descarregar dependências
mais recentes; guardar os IDs das imagens e repetir a validação da imagem a instalar.
