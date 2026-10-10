# Identidade da versão e entrega reproduzível — M13

Web e worker exigem um manifesto válido antes de abrir o contrato ou construir
serviços persistentes. Sem manifesto, uma cópia operacional recusa o arranque,
inclusive em development. Alterar apenas FS_TEST_SYNTHETIC em produção não
contorna a recusa. Erros iniciais saem por stderr/supervisor, sem criar dados.

## Gerar a entrega

No checkout limpo da revisão testada, usando Python 3.13 e Git local:

```powershell
$revision = git rev-parse HEAD
python -B tools/build_release.py --revision $revision --output "C:\entregas\folhas-servico.zip"
```

A pasta de destino deve existir e o ZIP não pode existir. `--repo` permite indicar
outro checkout local; por defeito usa o checkout da ferramenta. O SHA completo
indicado tem de coincidir com HEAD. Alterações versionadas, staged ou ficheiros
novos não ignorados recusam a construção. Dados privados ignorados não são lidos.
O operador continua responsável por escolher uma revisão realmente testada; Git
limpo não prova que os testes foram executados.

O comando lê objetos Git desse commit, sem filtros do working tree e sem respeitar
export-ignore que pudesse omitir código. Seleciona src, tools, docs, tests, README,
PATCH_NOTES, ficheiros Docker/requisitos e os dois modelos .env sem segredos.
A fixture gerada de raiz tests/fixtures/test_sample.xlsx acompanha os testes por
exceção exata; nenhum outro Excel é permitido nos caminhos selecionados.
Não inclui .git, configurações privadas, dados operacionais ou filas. Dados/cache
reconhecidos dentro dos diretórios de código selecionados causam recusa, mesmo
versionados. Os modelos de configuração e o código têm de continuar revistos:
um nome permitido não é uma análise automática de segredos.

A lista de ficheiros essenciais é conferida. Symlinks/submódulos nos caminhos
selecionados são recusados. Os blobs são copiados para staging temporário fora de
Git e recebem release-manifest.json. O ZIP tem ordem, data, modos e formato fixos;
duas construções do mesmo commit produzem os mesmos bytes. A resposta JSON indica
revision, source_sha256 e artifact_sha256. Conservar esse resultado junto do ZIP.
Uma falha durante a escrita preserva o ZIP parcial; usar um nome novo depois de
diagnosticar, nunca tratá-lo como entrega concluída.

A antiga interface `--root` que assinava uma cópia arbitrária com um SHA indicado
foi retirada. `write_release_manifest` mantém-se como primitiva interna de staging
(e testes), recusa qualquer ascendente Git e não certifica por si só a origem do
código. O caminho operacional é o novo CLI ligado ao commit. O manifesto gerado
fica fora do Git, para evitar autorreferência do SHA do commit.

## O que é verificado

A identidade cobre nomes e conteúdo de src/**, tools/**, passenger_wsgi.py,
requirements*.txt, Dockerfile, .dockerignore e docker-compose*.yml/yaml presentes.
Ficheiros acrescentados, removidos ou alterados mudam a identidade. Caches Python
ficam excluídas. Texto reconhecido é normalizado sem BOM e com LF; binários são
comparados byte a byte. Isto mantém o mesmo identificador entre Windows/Linux.
Docs e testes estão no ZIP, cobertos pelo hash do artefacto; não fazem parte da
identidade operacional. Não guardar dados/cache/logs dentro de src ou tools.

O manifesto guarda hashes individuais e o SHA Git. Uma divergência indica apenas
nomes relativos limitados/escapados, nunca conteúdo. Web/worker recusam antes dos
serviços persistentes; o worker regista queue_worker_failed. Não regenerar o
manifesto no servidor para esconder diferenças: reinstalar o artefacto correto ou
corrigir a montagem. A futura entrega Docker tem de copiar todos os ficheiros
cobertos, sem montagem de código por cima (M14).

X-App-Version, /api/admin/overview autenticado e os registos/resultados do worker
identificam a versão carregada. O parâmetro de versão dos recursos do navegador
deriva do mesmo conteúdo. O contrato persistente usa esse source_sha256; esta
ampliação da cobertura muda-o. Uma atualização requer o procedimento explícito de
[preparação/ativação](PREPARACAO_RUNTIME.md), escritores parados e o SHA anterior,
ou [migração](MIGRACAO_OFFLINE.md) quando os formatos forem legados. Não inicializar
um contrato novo sobre dados existentes nem editar o SHA à mão.

## Desenvolvimento e diagnóstico

Usar `python -B tools/run_test_version.py` ou pytest. O lançador cria uma pasta
sintética nova, bloqueia rede antes dos imports e configura todos estes valores:
FS_ENVIRONMENT=test, FS_TEST_SYNTHETIC=true, FS_STORAGE_BACKEND=local,
FS_AUTH_PROVIDER=none, FS_MAIL_ENABLED=false, FS_TEAMS_NOTIFICATIONS_ENABLED=false,
FS_GRAPH_QUEUE_IN_WEB=false. Apenas esse perfil completo permite working-tree sem
manifesto; se existir um manifesto adulterado, continua a ser recusado. As flags
sozinhas não substituem isolamento/rede bloqueada. Não usar dados reais no perfil.

`get_release_info` permanece uma leitura de diagnóstico: sem manifesto devolve
working-tree/verified=false. `prepare_runtime.py inspect` pode usar essa leitura,
mas não ativa nem inicializa dados. init/activate exigem manifesto verificado,
mesmo num perfil de testes. Ausência de manifesto em init/activate devolve
exit 2 e JSON com code=state_contract_invalid em qualquer perfil, sem criar dados.
inspect requer um contrato existente e apenas o lê. Fixtures que simulam configuração de produção usam
uma cópia com manifesto real e mantêm rede bloqueada/transportes simulados.

## Verificação e limites

Testes cobrem factories sem manifesto e com artefacto válido, bypass incompleto,
adulteração de ferramentas/entrega, identidade multiplataforma, erros limitados,
pacotes determinísticos de repositórios fictícios, dirty/untracked/staged,
SHA incorreto/incompleto, ficheiros obrigatórios ausentes, preservação de destino,
exclusão de dados privados e ausência de imports da configuração/aplicação.

Mínimo Python 3.12; recomendado 3.13. Não foi ensaiada uma instalação Python 3.12.
A verificação ocorre no arranque: não editar ficheiros de processos em execução.
verified significa correspondência ao manifesto; não é assinatura criptográfica
de um fornecedor. O ZIP reproduz o código, não fixa downloads de dependências ou
a imagem base (M15). Linux/Docker e falhas físicas continuam a exigir ensaio no
ambiente adequado. Esta documentação não autoriza publicação ou instalação.
