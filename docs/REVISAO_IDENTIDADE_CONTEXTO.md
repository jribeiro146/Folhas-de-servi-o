# Contexto da segunda revisão da identificação de versão

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
