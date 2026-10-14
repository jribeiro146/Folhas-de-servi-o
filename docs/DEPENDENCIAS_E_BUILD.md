# Dependências e repetibilidade da entrega (M15)

## Âmbito

O ZIP identifica um commit limpo e inclui código, ferramentas e requisitos. O builder lê os blobs Git, conserva metadados ZIP fixos e escreve o manifesto em UTF-8 com LF explícito. Os mesmos blobs e SHA produzem os mesmos bytes do pacote em Windows e Linux; a tradução automática CRLF do manifesto foi eliminada. O teste compara os bytes com a serialização canónica e compara manifestos de árvores LF/CRLF, além de gerar o mesmo ZIP duas vezes.

O manifesto identifica o código e as dependências declaradas. Não é um inventário dos pacotes instalados nem uma assinatura do fornecedor. Não se regenera dentro de uma imagem em execução. Uma alteração dos requisitos ou do Dockerfile muda a identidade da entrega.

## Imagem e Python

A base Docker usa o digest `sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f` de `python:3.13-slim`, observado nas duas imagens do [run 38243794527](https://github.com/jribeiro146/Folhas-de-servi-o/actions/runs/38243794527), em 12/10/2026. Não é uma escolha automática da versão mais recente. A fixação por digest segue o [procedimento Docker](https://docs.docker.com/build/building/best-practices/#pin-base-image-versions).

`requirements-linux-constraints.txt` fixa as versões de pip, Gunicorn, dependências diretas/transitivas e pytest que os logs desse run identificaram. O Docker aplica o ficheiro em todas as instalações Python, tanto no runtime como no target qa. Os requisitos funcionais continuam em `requirements-server.txt` e `requirements-smoke.txt`. As entradas de pytest só limitam versões: [constraints não instalam pacotes por si](https://pip.pypa.io/en/stable/user_guide/#constraints-files). A imagem de produção continua sem pytest; a imagem qa acrescenta os testes sobre o runtime. `pip check` recusa incompatibilidades durante o build e o ensaio.

Este conjunto destina-se ao Docker Linux/Python 3.13. Não é um lock validado do ambiente Windows, do Plesk sem Docker ou de outra versão Python. Esses destinos mantêm os requisitos existentes e exigem os seus próprios ensaios. O ficheiro de constraints é obrigatório no pacote e entra no manifesto.

## Evidência de cada execução

`tools/validate_docker.sh` usa a mesma matriz existente: UID/GID 10001:10001 e 100:101, Compose 5.6.0 e 2.27.0. O workflow já permite a branch de testes autorizada `codex/validacao-linux-20261012`; a branch local P2, por si só, não desencadeia o workflow por push.

Além do SHA do commit, ZIP, identidade do código e imagem, os anexos de cada identidade incluem:

- `runtime-pip.json` / `qa-pip.json`: nomes e versões efetivamente instalados, sem URLs de origem nem credenciais;
- `runtime-pip-check.txt` / `qa-pip-check.txt`: compatibilidade dos requisitos instalados;
- `runtime-debian.tsv` / `qa-debian.tsv`: versões dos pacotes Debian, incluindo dependências do Chromium e fontes;
- `runtime-python.txt` / `qa-python.txt`, `runtime-chromium.txt` / `qa-chromium.txt` e `qa-image.txt`;
- resultados Python/JavaScript, PDF real, permissões POSIX, inicialização, migração, backup e restauro já previstos.

Os comandos de inventário e ensaio usam `--network none`; só o build descarrega dependências. Não são carregadas configurações ou filas operacionais.

## Limites e atualização

Os repositórios APT continuam a fornecer Chromium/fontes/Git e respetivas dependências na versão disponível durante o build. O inventário torna essa variação explícita. Não se promete igualdade binária de imagens reconstruídas: versões Python fixas não fixam hashes das wheels, APT ou metadados do Docker. Para instalar os mesmos bytes, conservar e distribuir a imagem já validada por digest; reconstruir requer nova validação. O ZIP reproduzível tem um âmbito diferente da imagem Docker.

Atualizar quando houver uma correção necessária, incompatibilidade ou atualização de manutenção escolhida. Rever as notas dos fornecedores, escolher versões/digest explicitamente e alterar este conjunto numa mudança identificada. Nunca fazer upgrade automático durante a instalação em produção. Executar os testes proporcionais, obter revisão independente e repetir a matriz Docker no commit exato, comparando inventários runtime/qa e verificando que pytest não entrou no runtime. Preservar hashes, logs, motivo e resultados. Se o build ou um teste falhar, investigar antes de nova execução; não repetir até obter verde.

Estas alterações não migram dados nem mudam a chave de segurança. A instalação/reversão operacional mantém os procedimentos de backup, contrato de dados e autorização próprios. O teste Ubuntu não substitui o smoke PDF e as integrações no AlmaLinux do destino.

## Estado da validação

A evidência de 12/10/2026 é a base das versões escolhidas e pertence ao commit `0869bfa`, anterior a M15. O novo Dockerfile/manifesto só pode ser declarado validado em Linux após novo run do commit que os contém. A aprovação M15 não encerra o P3 de recuperação offline entre abas que permanece registado em M22.

O primeiro ensaio M15, [run 38543107104](https://github.com/jribeiro146/Folhas-de-servi-o/actions/runs/38543107104), no commit `7f3911b`, confirmou os inventários (15 pacotes runtime, 20 qa, todas as versões coincidentes com constraints e `pip check` sem incompatibilidades) e ZIP Windows/Linux idêntico: `1b85896618a207cf44771efe19c80597498c9b1be4824953b029ad03f4693c83`. Python 3.13.16 e Chromium 155.0.8059.39 foram efetivamente observados. Os dois perfis passaram init, migração e backup/restauro, mas falharam em quatro testes desatualizados: fixtures sem a identidade estável de administrador e comparação literal do cabeçalho de cache. O PDF e o Compose posteriores não chegaram a executar; este run não é aceitação completa.

As fixtures passaram a usar os GUIDs fictícios da política administrativa atual; a página continua obrigada a `no-store`, permitindo as diretivas adicionais de proteção. Não se alterou a autorização nem a cache da aplicação. A suite completa Windows após estas correções passou: 1516 testes, quatro skips de capacidades POSIX/symlink, zero falhas. A correção foi aprovada na segunda revisão M15 `20261014T094545Z_fd1128f8_8a921e`, check0, e validada no run `38544935797`, commit `fd1128f824b86b0143b11f8d32e4c3459affbf39`. Cada perfil passou 1517 Python/3 skips Windows, o PDF real e Compose; JS174. O ZIP Windows/Linux foi idêntico: `47d2cbf023e6012c07d3adf9acad0fec9e7337768d4aed7cc55faa84478e6227`. Inventários runtime15/QA20 coincidem com constraints, quatro pip check passaram e pytest só consta do QA. Estes resultados pertencem a fd1128f; alterações posteriores exigem a validação identificada no estado da entrega.
