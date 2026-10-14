# Estado da entrega — 14/10/2026

## Árvore e estado atual

A base canónica é `qa-output/production-delivery-20260928`, com Git próprio, branch local `codex/p2-folhas-sadi-20261012`. `App_Folhas_Checklists/aplicacao` é o pacote/demo, com diferenças históricas; não é uma cópia desta entrega. Não copiar uma árvore sobre a outra. O [inventário de 07/10](RECONCILIACAO_2026-10-07.md) fica como proveniência, não como lista atual de trabalho por executar.

M01–M22 estão implementados e validados no âmbito técnico documentado abaixo. A correção final M22 foi aprovada em `20261014T131543Z_496bbaa8_86934d`, sem achados/contexto em falta, check0 no commit `496bbaa80bca59955164e7932a7973ea16509efd`. Repõe o retry após bootstrap offline sem reserva e consulta novamente as abas ativas. O parecer faz análise estática; os testes foram executados pelo Codex. Não houve deploy, merge em main, reinício do servidor ou comunicação operacional.

O artefacto final ensaiado é **`496bbaa80bca59955164e7932a7973ea16509efd`**, [run 38570674039](https://github.com/jribeiro146/Folhas-de-servi-o/actions/runs/38570674039), publicado apenas na branch autorizada `codex/validacao-linux-20261012`. Cada perfil Docker passou **1537 Python/3 skips**; JavaScript passou **180**. A suite Windows passou **1536/4 skips** e o ensaio Chromium passou **doze grupos**, incluindo três abas reais. ZIP Windows e dois Linux idênticos: **`51d1db08daa0b2c525c0b329ecf60aaa792a5f96ca8eea8443f8e57736eaecfc`**; source `f676978aa63eb08c177c9313a9c38f43a085b3d603068ee84f38a06cf912b050`, 122 ficheiros. [Evidência M22](EVIDENCIA_CI_2026-10-14_M22.json) e [histórico CI](VALIDACAO_CI.md).

O pacote anterior b4614b5/run38558533929 não contém esta correção e permanece identificado no histórico M20. O commit posterior ao candidato M22 apenas regista resultados em documentação, com `[skip ci]`: não é outro CI nem outro ZIP validado. Não houve publicação em main ou instalação.

## Mapa de mudanças e provas

| Área | Decisão e commit | Evidência/guia |
|---|---|---|
| P1: edição, cancelamento, formatos e entrega | Consolidação `4ae35db0`; bases legadas não se convertem no arranque; instalação por manifesto | [Quarentena](QUARENTENA_EDICAO.md), [cancelamento](CANCELAMENTO_RECUPERAVEL.md), [M06](MIGRACAO_OFFLINE.md), [M07](BACKUP_RESTAURO.md), [pacote](IDENTIDADE_DA_VERSAO.md), [Docker](10_ALOJAMENTO_DOCKER.md); CI `0869bfa` e depois M15 |
| M01/M02 | `5e237039` / `211cf1be`: identidade Microsoft coerente e administrador por email+tenant+objeto | [Autenticação](AUTENTICACAO_MICROSOFT.md), [identidade administrativa](IDENTIDADE_ADMINISTRATIVA.md); 212/231 Python nos respetivos fechos, com 1 skip Windows |
| M03/M09 | `a1e65afd` / `94f2d1f`: estado de trabalho só para dono/admin e diagnósticos sem conteúdo privado | [Trabalhos privados](ESTADO_PRIVADO_TRABALHOS.md), [diagnósticos](DIAGNOSTICOS_SEGUROS.md); regressões de autorização/privacidade e CI M15 |
| M08 | `884b7669`: decisões com prova/auditoria e revalidação do commit local sem libertar envios incertos | [Reconciliação](RECONCILIACAO_TRABALHOS.md); 444 Python/101 JS no fecho |
| M10/M11 | `be7101d1` / `8acaba82`: acompanhamento só por GET, limitado à sessão; presença do worker independente da web | [Retoma](RETOMA_ACOMPANHAMENTO_EMAIL.md), [worker](DIAGNOSTICO_WORKER.md); 227 Python/139 JS e 371 Python/110 JS nos respetivos fechos |
| M19/M21 | `44e24745` / `0e6d61ff`: cache por versão sem substituir formulário aberto; concorrência Graph por identidade | [Cache](CACHE_PWA_VERSAO.md), [pastas Graph](CRIACAO_PASTAS_GRAPH.md); seis cenários Chromium e 314 Python no M21 |
| M15 | `fd1128f8`: digest base e constraints, manifesto LF, fixtures alinhadas com autorização/cache | [Dependências](DEPENDENCIAS_E_BUILD.md); CI dois perfis, 1517 Python por perfil/174 JS; Windows 1516 Python |
| M16 | `a2b40e95`: autosave conservado após Excel gravado e sidecar falhado; repetição explícita reconcilia | [Percursos integrados](PERCURSOS_INTEGRADOS.md); 1519 Python/4 skips e onze grupos Chromium, worker separado, um email e um Teams simulados |
| M17 | `8370a10c` (código `8a8878ed`): fase de preparação antes do upload; falha anterior ao envio pode ser repetida sem libertar legados incertos | [Queixas e regressões](QUEIXAS_E_REGRESSOES.md); 507 Python/1 skip e 175 JS |
| M18 | `a1cd5d34`: matriz SADI com três perfis, legado, assinaturas, cliente ausente, PDFs reais separados e anexo só da folha | [Matriz SADI/PDF](MATRIZ_SADI_E_PDFS.md); 276 Python/1 skip, 175 JS, cinco grupos PDF e inspeção de todas as páginas |
| M20 | `b4614b5e`: guias reconciliados e COPY de todos os requisitos abrangidos pelo manifesto; sem instalar dependências opcionais no runtime | [CI final](VALIDACAO_CI.md), [evidência](EVIDENCIA_CI_2026-10-14_FINAL.json); 1537 Python por perfil Docker/175 JS, PDF/Compose/migração/restauro |
| M12 | `4b7dd6a0`: evitar varrimentos inúteis de uploads em repouso; simulação 90/180/365 dias, sem eliminar | [Histórico/retenção](HISTORICO_E_RETENCAO.md); 242 Python, quatro benchmarks; 100 mil uploads de 119,4 s para 0,81 s por ciclo no ensaio Windows |
| M22 | `496bbaa8`: retry após bootstrap sem sessionToken e nova sondagem das abas; formulário bloqueado até verificação | [Chave e recuperação](ASSINATURAS_SADI_E_CHAVE.md); 180 JS, doze grupos Chromium, suite Windows e CI do artefacto final acima |

Os números de cada linha pertencem ao respetivo checkpoint e não devem ser somados como testes distintos. Os ensaios são sintéticos, isolados e sem entrega a destinatários. Logs, artefactos e pareceres integrais ficam no registo local de QA; dados operacionais e configuração privada não entram nos pacotes de revisão.

Revisões recentes, todas com `approve`, sem achados/contexto em falta e `check=0` no HEAD indicado: M15 `20261014T094545Z_fd1128f8_8a921e` (segunda ronda), M16 `20261014T102529Z_a2b40e95_2c977e`, M17 `20261014T103655Z_8370a10c_394d4b`, M18 `20261014T105307Z_a1cd5d34_ee754a` e M12 `20261014T111624Z_4b7dd6a0_18e83f` (primeiras rondas). O revisor fez análise estática; a execução dos testes foi feita pelo Codex.

## Configuração e sequência do instalador

1. Conferir o commit aprovado, o checksum do ZIP entregue e `release-manifest.json`. Construir a imagem a partir desse pacote, mantendo código fora dos volumes de dados. Usar as constraints e o digest descritos no [build](DEPENDENCIAS_E_BUILD.md); APT continua mutável, sem promessa de imagem binariamente idêntica.
2. Inventariar as raízes persistentes e parar todos os escritores na intervenção autorizada. Preservar a chave existente e as configurações privadas. A administração exige `FS_ADMIN_TENANT_ID` e `FS_ADMIN_OBJECT_ID`, além do email esperado; são valores do utilizador administrador, não da aplicação. Novo login é necessário para sessões antigas sem identidade completa.
3. Escolher o procedimento pela situação real: volume novo usa [init](PREPARACAO_RUNTIME.md); legado usa [prepare/apply M06](MIGRACAO_OFFLINE.md); atualização compatível usa [backup/verify M07](BACKUP_RESTAURO.md) e activate. Não trocar manualmente PRAGMA, epoch ou hashes para contornar uma recusa.
4. Conferir montagens, dono dos volumes, diretórios de logs/cache/diagnóstico e a mesma chave/configuração nas funções web e worker. `FS_GRAPH_QUEUE_IN_WEB=false` mantém o processamento no worker separado. A presença do site não comprova o worker; usar [o diagnóstico próprio](DIAGNOSTICO_WORKER.md).
5. Manter `FS_SECRET_KEY` estável, armazenada privadamente, e preservar o armazenamento SADI. [Assinaturas e chave](ASSINATURAS_SADI_E_CHAVE.md) explica o efeito de mudar a chave. O instalador ainda confirma Chromium/PDF e permissões no destino AlmaLinux, com dados fictícios; CI Ubuntu/Docker não testa esse kernel/configuração.
6. Se for preciso reverter, seguir [restauro M07](BACKUP_RESTAURO.md) ou a [reversão legada pré-M06](10_ALOJAMENTO_DOCKER.md). Uma cópia antiga não desfaz email/Teams/Graph já aceites: os efeitos posteriores ficam retidos para reconciliação antes de qualquer libertação. Não apagar volumes nem copiar uma base antiga por cima de outra em uso.

Estes são procedimentos de entrega; não constituem uma ação operacional já executada. Guias de setembro e respostas antigas a revisões mantêm contexto histórico; esta página e os guias específicos acima definem a sequência atual.

## Limites e decisões por tomar

- **Retenção:** João escolheu simular 90/180/365 dias e decidir depois. Não existe prazo de eliminação escolhido. Todos os IDs, recibos, incertezas e auditoria continuam protegidos; a ferramenta é só de leitura.
- **Ocorrência Windows no fecho:** a primeira suite completa, em b61b5e8, teve 1535 aprovados/4 skips/1 falha: um teste de falha PDF recebeu HTTP400 antes do erro500 simulado. Dez reproduções isoladas passaram; a asserção foi melhorada para conservar a resposta caso reapareça. A causa não foi demonstrada e não é atribuída à correção Docker. A segunda suite completa, em b4614b5, passou **1536 testes/4 skips**, 416,59 s; isso não demonstra a causa do HTTP400 anterior. O resultado original fica preservado.
- **Windows SADI:** o WinError5 histórico em rename continua sem causa demonstrada. Os testes posteriores passam e uma injeção determinística confirma conservação do rascunho/repetição explícita; isso não identifica a causa ambiental antiga.
- **Destino e fornecedores:** não houve instalação no servidor, login Microsoft real nesta matriz nem envio operacional de email/Teams/Graph. Uma aceitação simulada não prova entrega. Continuam aplicáveis as verificações do instalador e as autorizações operacionais do projeto.
