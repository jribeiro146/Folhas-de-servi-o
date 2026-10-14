# Contexto das revisões da identificação de versão

**Contexto histórico da revisão M13, preservado como evidência daquela fase.** A entrega Docker e o M15 foram implementados e ensaiados posteriormente; o estado atual está em [ESTADO_ENTREGA_2026-10-14.md](ESTADO_ENTREGA_2026-10-14.md) e [VALIDACAO_CI.md](VALIDACAO_CI.md). As referências a etapas futuras abaixo não são pendências atuais.

A primeira revisão Claude do commit `49ffbf7` pediu três ajustes: logging estruturado da falha do worker, ensaios da recusa nos pontos de entrada e diagnóstico de ficheiros divergentes. A retificação mantém o âmbito de identidade/manifesto; o contrato dos dados e o build Docker final continuam separados.

- O worker calcula a identificação uma vez dentro do tratamento de erros. A recusa devolve 1 e emite `queue_worker_failed`.
- `create_app` e `build_queue` aceitam uma raiz injetada por código para ensaios sintéticos. Não existe parâmetro HTTP nem variável de ambiente para a selecionar; as entradas operacionais continuam a usar a raiz da instalação. O HTML é verificado contra `v=<hash[:16]>`, não apenas contra a igualdade de duas execuções no mesmo segundo.
- O manifesto inclui hashes por caminho. O erro mostra caminhos relativos adicionados/removidos/alterados, escapados e limitados, sem conteúdo. Não regenerar manifestos no servidor para fazer a verificação passar.
- A extensão `.webmanifest`, presente no projeto, também normaliza BOM/CRLF. A regressão correspondente falhava antes desta retificação.
- Ligações simbólicas/junções, incluindo ligações quebradas, são recusadas. O ensaio de criação de symlink fica explicitamente ignorado quando o Windows não concede essa operação; requer execução em Linux. Caches Python continuam excluídas.

## Contexto solicitado pelo revisor

`src/__init__.py` e `src/services/__init__.py` contêm apenas comentários. O empacotador não importa `src.config` nem `src.web.application`; há um ensaio por subprocesso que verifica os módulos importados ao executar o comando.

As extensões existentes em `src` nesta revisão são `.css`, `.html`, `.js`, `.png`, `.py` e `.webmanifest`. Todas as extensões de texto existentes são normalizadas; PNG é comparado em bytes. Não há `.gitattributes` neste repositório. A normalização também suporta `.json`, `.svg` e `.txt` para código/recursos futuros.

Os templates usam `asset_version` como parâmetro de URL em `url_for`; não fazem interpretação como data. O service worker conserva por agora a sua constante própria `BUILD_VERSION` e não interpreta `ASSET_VERSION`. A versão de 16 caracteres hexadecimais é um valor válido de query string. Os testes PWA existentes continuam a ser executados; harmonizar a política de cache por release continua na verificação de interfaces/empacotamento, não foi afirmado como concluído aqui.

Os caminhos operacionais normais são exteriores a `src`: cache/edição/SADI/finalização sob diretórios configurados de dados, logs sob `FS_APP_DATA_DIR/logs`, PDFs junto ao arquivo e temporários do navegador em diretório temporário. `document_style_service` só lê os recursos estáticos; não os reescreve. A escrita de observações internas na web ocorre junto ao Excel recebido do serviço de ficheiros, não em recursos estáticos. Uma configuração administrativa que aponte dados/PDF/logs para o código é inválida; a validação final de configuração deve recusá-la. Esta identificação não afirma impedir toda a configuração errada, mas uma alteração posterior em src deixa o manifesto divergente.

O Dockerfile copia `src`, a entrada WSGI e os requisitos de servidor/smoke. `.dockerignore` exclui caches Python e dados operacionais. O manifesto será criado dentro do artefacto a partir dos ficheiros efetivamente copiados quando a etapa Docker for implementada. Não estamos a reutilizar entre plataformas um manifesto de uma árvore com ficheiros extra não entregues. Ainda não houve execução Docker/Linux neste computador.

## Retificação para a terceira ronda

A segunda revisão confirmou os ajustes anteriores e detetou o risco de manifesto ignorado num checkout em desenvolvimento. O CLI passa a exigir `--root`; a API de escrita também recusa `.git` (diretório ou ficheiro de worktree), antes de escrever. O exemplo da documentação usa uma cópia de staging e explica a recuperação de um manifesto obsoleto apenas no desenvolvimento. Três testes por subprocesso reproduziram a falha e passaram após a correção; usam uma cópia sintética do comando/módulo para não escrever no checkout real mesmo na fase vermelha.

O guia existente `docs/IMPLEMENTACAO_SERVIDOR.md` já prescreve Python 3.13 preferencial ou 3.12 sujeito a ensaio. Docker e CI usam 3.13. Não temos evidência da versão atual de todo o servidor; confirmar antes da instalação continua a ser requisito da entrega. Foi acrescentada recusa controlada antes de usar `Path.is_junction`, para que versões abaixo de 3.12 indiquem a versão necessária. O teste simula `sys.version_info`; não é uma execução em Python 3.12. Esta limitação não altera o comportamento nos runtimes suportados. Não foi feito deploy.

Foram também executados os dois ensaios adicionais sugeridos: uma junção real Windows para diretório sintético é recusada (e o alvo conserva-se após retirar apenas a junção); vinte nomes longos com caracteres não ASCII produzem no diagnóstico apenas doze nomes truncados a 160 caracteres, escapados como ASCII. O teste de symlink continua explicitamente ignorado por falta de privilégio no Windows. No teste de nomes longos usa-se o prefixo Windows de caminhos extensos para não atingir MAX_PATH antes do comportamento sob teste; o nome cabe no limite em bytes de Linux.

## Resultado da terceira ronda e correção local posterior

O terceiro parecer, sobre `3f470f4`, identificou que a guarda ainda permitia uma subpasta de um checkout. O cenário foi reproduzido em quatro combinações API/CLI e `.git` diretório/ficheiro. A correção percorre o destino resolvido e todos os ascendentes antes de escrever qualquer manifesto ou temporário. Staging tem de ser exterior a qualquer repositório. Esta correção posterior ainda necessita de revisão; três rondas já foram usadas e o resultado será apresentado ao utilizador conforme AGENTS.md.

Resposta ao contexto em falta: a ferramenta e o módulo foram acrescentados apenas à base de produção `qa-output/production-delivery-20260928`. Não foram portados para o pacote/demo `App_Folhas_Checklists/aplicacao`; preservar esse pacote separado é a decisão vigente. O caso do revisor nessa árvore não é executável hoje por faltar lá a ferramenta, mas a vulnerabilidade da guarda é reproduzível com uma subpasta sintética e foi corrigida genericamente.

O texto abaixo foi confirmado diretamente em `docs/IMPLEMENTACAO_SERVIDOR.md`, secção «Versoes e dependencias», linha 36. Não se envia o guia inteiro porque contém identificadores operacionais não necessários a esta revisão:

> Para servidor, usar preferencialmente Python 3.13.x. Se o Plesk nao disponibilizar Python 3.13, usar Python 3.12.x e testar antes de producao.

Isto confirma a coerência documental; não identifica o Python atualmente instalado no Plesk. A obrigação de confirmar e ensaiar o runtime antes de instalar continua explícita. A recusa de produção sem manifesto será ligada ao empacotamento/readiness conforme o plano; esta fundação não foi apresentada como release pronta.
