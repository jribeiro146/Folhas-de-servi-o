# Validação Linux/Docker e rastreabilidade

## Candidato final confirmado em 14/10/2026

[Run 38558533929](https://github.com/jribeiro146/Folhas-de-servi-o/actions/runs/38558533929), commit **`b4614b5ed4ab6daa174b23bd77c932d03e88e3c3`**: os três jobs passaram. Cada perfil Docker aprovou **1537 Python**, com três skips exclusivos de Windows; JavaScript aprovou **175**. UID/GID 10001:10001 e 100:101, Compose 5.6.0 e 2.27.0. PDF Chromium real de duas páginas em ambos; init/factories, recusa de legado/adulteração, migração, reposição pré-M06, backup/verify/activate/restore/release e permissões POSIX passaram.

O ZIP Windows e os dois Linux são idênticos: **`ec07dcddd13f602009b2d590fafb943d296089b0f20c4e5d8d92f84b8fd2bcb0`**. Identidade de fonte: `aa2043fa033fbf0c660e89bac6325930bacf2a43d50483baa4e170cdd6e6719e`, 122 ficheiros. Python 3.13.16, Chromium 155.0.8059.39; inventários de 15 pacotes runtime/20 QA coincidem com constraints, quatro pip check passaram, pytest ausente do runtime. [Evidência permanente final](EVIDENCIA_CI_2026-10-14_FINAL.json). Logs e anexos completos foram preservados localmente, com digests conferidos contra a API.

O run anterior `38556971728`, em b61b5e8, falhou durante o build por não copiar `requirements-browser-tests.txt`; só JavaScript passou, sem executar Python/PDF/Compose posteriores. O COPY foi corrigido para incluir todos os requisitos declarados, mantendo a instalação pip e a verificação do manifesto. O resultado falhado não foi apagado nem classificado como sucesso.

A suite Windows completa do candidato b4614b5 passou **1536 Python/4 skips**, 416,59 s. A primeira suite em b61b5e8 tinha 1535 aprovados/4 skips/1 falha HTTP400 num teste que esperava500 simulado. Dez reproduções isoladas e a suite posterior passaram; a causa não foi demonstrada. O diagnóstico da asserção foi melhorado, sem alterar o comportamento SADI; logs anteriores permanecem preservados.

A atualização documental posterior apenas regista esta evidência e usa `[skip ci]`; não é um novo ensaio nem produz automaticamente outro ZIP validado. O artefacto ensaiado continua a ser b4614b5 com o checksum acima. M22 permanece pendente; retenção não elimina dados; a instalação/AlmaLinux permanece fora destes testes.

## Histórico M15 de 14/10

**M15 confirmado em 14/10/2026:** [run 38544935797](https://github.com/jribeiro146/Folhas-de-servi-o/actions/runs/38544935797), commit `fd1128f824b86b0143b11f8d32e4c3459affbf39`, três jobs aprovados. Cada perfil Docker passou 1517 Python/3 skips Windows; JavaScript passou 174. PDF real de duas páginas, permissões POSIX, init/migração/backup/activate/restauro, Compose 5.6.0 e 2.27.0 passaram. As duas identidades foram 10001:10001 e 100:101. Windows e ambos Linux produziram o mesmo ZIP SHA256 `47d2cbf023e6012c07d3adf9acad0fec9e7337768d4aed7cc55faa84478e6227`; source `816e6506c4c9f373154adef0fe6236f8f0b2b32443399ee95bae6ee2749bad61`. [Evidência permanente M15](EVIDENCIA_CI_2026-10-14_M15.json).

O primeiro run `38543107104` falhou em quatro fixtures desatualizadas e permanece registado como falha. Correções e limites em [dependências e build](DEPENDENCIAS_E_BUILD.md). M16/M17/M18/M12 são posteriores a fd1128f e foram incluídos no CI final b4614b5 acima; a aprovação M15 não os valida retroativamente. O ensaio específico no destino AlmaLinux continua a cargo do instalador.

## Histórico confirmado de 12/10

**Resultado confirmado em 12/10/2026:** validação sintética Linux/Docker aprovada
para o commit `0869bfa810794d80dbde6494cd603f6a3d764597`.
[Run 38243794527](https://github.com/jribeiro146/Folhas-de-servi-o/actions/runs/38243794527)
concluído com os três jobs verdes, depois de duas revisões reais do Claude.

| Ensaio | Resultado |
|---|---|
| Python, UID/GID 10001:10001, Compose 5.6.0 | 1094 aprovados, 3 ignorados, zero falhas; 66.84 s |
| Python, UID/GID 100:101, Compose 2.27.0 | 1094 aprovados, 3 ignorados, zero falhas; 68.09 s |
| JavaScript, Node 22 | 79 aprovados, zero falhas |
| Init, factories, recusa legada, migração e reposição pré-M06 | PASS nas duas imagens |
| Backup, verify, activate, restore com retenção e release sintético | PASS nas duas imagens |
| PDF Chromium | Duas páginas verificadas em cada imagem; utilizador correto |
| Compose válido e recusa de create_host_path=true | PASS nas duas versões |
| Permissões POSIX SADI, migração e backup | Executados e aprovados nas duas imagens |

Os três skips de cada imagem são específicos de Windows: dois casos UNC e um de
junções. O teste de finalização SADI que apresentou WinError 5 em Windows passou
nas duas imagens Linux; isso não demonstra a causa nem resolve o episódio Windows.

A [evidência resumida permanente](EVIDENCIA_CI_2026-10-12.json) guarda hashes,
identidades, versões, revisão Claude, limites e resultados. Os anexos completos
foram também descarregados e conferidos por SHA256 no registo local do projeto.
Os dois runners produziram o mesmo ZIP SHA256:
`15705b9df70dbd07a86655b48d97321d50a6937fcf15f22619adab74249646e0`.
O source_sha256 é
`49a3dd6fcda4597be22b9d203590d21d8a2942755a88eed15397cdefc4048522`.

**Nota histórica de empacotamento (commit 0869bfa):** o builder Windows escrevia CRLF no
único fim de linha do manifesto e produz ZIP SHA256
`618a768d477ab7fdce8309533dd7dc174aefc43b0c6c159d50e8c0350aebd92e`.
Normalizar apenas esse fim de linha para LF, preservando todas as outras entradas
e metadados, reproduziu exatamente o hash dos dois runners. A cópia local
`ci-validated-0869bfa-linux-identical.zip` foi assim conferida byte a byte por hash.
Não houve alteração do código ou da identidade operacional. Comparar sempre o
checksum do ZIP efetivamente entregue; não atribuir igualdade binária entre
plataformas àquele builder. O M15 uniformiza esse fim de linha e fixa a base e
dependências Python do Docker; ver [dependências e build](DEPENDENCIAS_E_BUILD.md)
para o âmbito, inventários, atualizações e validação do código novo.

A publicação posterior deste resultado altera apenas documentação e usa `[skip ci]`
para evitar repetir o mesmo ensaio. O SHA testado continua a ser o indicado acima;
não confundir um commit de documentação posterior com uma nova execução.


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
- Imagem padrão: UID/GID 10001:10001, Compose 5.6.0.
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
| 12/10/2026 | Claude segunda revisão / commit 0869bfa | approve, sem achados/contexto em falta; check exit 0 |
| 12/10/2026 | Run 38243794527 | Três jobs aprovados; evidência conferida na tabela inicial |

Segunda preparação após o parecer Claude 20261012T112630Z_38671394_87a3e4:
20 testes CLI aprovados. Confirmado com os binários oficiais portáteis Compose
2.27.0 e 5.6.0, usando apenas `config`, sem Docker Engine: false aceite e true
recusado nas duas versões. O verificador anterior aceitava incorretamente a
omissão produzida por true no 5.6.0; a reprodução e a correção estão registadas.
Isto valida a normalização, não constitui execução de containers.

## Reprodução pelo instalador e limites

Partindo de um ZIP verificado e extraído, executar no host isolado:

```bash
bash tools/validate_docker.sh /caminho/absoluto/release /tmp/evidencia-nova 10001 10001
# Nova pasta de evidência na segunda execução:
bash tools/validate_docker.sh /caminho/absoluto/release /tmp/evidencia-hs4-nova 100 101
```

A máquina precisa de Bash, Python 3 e Docker/Compose v2 ou superior. Não usar o daemon de
produção. O roteiro detalhado está em [VALIDACAO_DOCKER_INSTALADOR.md](VALIDACAO_DOCKER_INSTALADOR.md).
O verificador aceita o false omitido pelo compose-go 2.1/Compose 2.27.0 identificado no JSON
**normalizado**. Nas restantes versões exige false explícito, pois as versões
recentes omitem true. Nunca aceita true, strings, null ou código montado por bind.

Ubuntu no GitHub não reproduz kernel 4.18, cgroups v1 e seccomp do AlmaLinux de
produção. O profissional ainda confirma o smoke PDF nesse destino, com HOME
gravável e FS_PDF_BROWSER_NO_SANDBOX=true conforme reportado, mais acessos Microsoft,
volumes reais, backup e procedimento de instalação. Não se declara produção
validada por um run sintético. Builds futuros podem descarregar dependências
mais recentes; guardar os IDs das imagens e repetir a validação da imagem a instalar.

Referências: [setup-compose v2](https://github.com/docker/setup-compose-action/tree/v2)
existe e suporta a seleção explícita de versão. A matriz fixa 5.6.0 (compose-go
2.16.1) e 2.27.0 (compose-go 2.1.0), evitando depender da versão pré-instalada
do runner. A versão efetiva fica em compose-version.txt.
