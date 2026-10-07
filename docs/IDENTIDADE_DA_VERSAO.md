# Identidade do código em execução

A aplicação calcula SHA-256 do conteúdo e nomes dos ficheiros em `src`, da entrada WSGI e dos requisitos de servidor/smoke presentes. Texto é normalizado para UTF-8 sem BOM e finais de linha LF; imagens são comparadas em bytes. Caches Python ficam excluídas. O código não importa a configuração nem a aplicação para criar esta identificação.

O cabeçalho HTTP `X-App-Version`, a resposta autenticada de `/api/admin/overview` e os registos/resultados do worker identificam o conteúdo carregado no arranque. O parâmetro dos recursos do navegador deriva desse conteúdo e é igual nos vários processos da mesma versão. Não depende do segundo em que arrancaram.

Para um checkout/artefacto fixo, o preparador da release executa:

```powershell
python -B tools/build_release.py --revision SHA_COMPLETO_DO_COMMIT
```

O comando grava `release-manifest.json` na raiz do artefacto. O SHA Git é fornecido pelo preparador; o comando verifica o formato e regista o conteúdo, não certifica por si só a origem Git. Não gerar o manifesto enquanto se continua a editar código. O manifesto deve acompanhar o pacote final, mas fica fora do Git para evitar autorreferência do commit. A integração do comando no build Docker/CI será feita na etapa de empacotamento.

Com manifesto, web e worker recusam arrancar se o conteúdo atual divergir (por exemplo, uma montagem `src` de outra versão). Sem manifesto, um checkout de desenvolvimento apresenta `revision=working-tree` e `verified=false`; isso não é uma release certificada. `verified` significa apenas que os ficheiros correspondem ao manifesto, não assinatura criptográfica de um fornecedor.

O manifesto guarda também hashes por ficheiro. Uma divergência indica caminhos relativos acrescentados, removidos e alterados, sem conteúdo dos ficheiros e com limites de tamanho no diagnóstico. O worker regista a falha no evento estruturado `queue_worker_failed`. **Não regenerar o manifesto no servidor para esconder uma divergência:** reinstalar o artefacto correto ou corrigir a montagem e verificar de novo. A geração pertence à preparação de um artefacto fixo e revisto.

Esta alteração identifica o conteúdo no arranque. Não deteta uma edição manual posterior dos ficheiros num processo já aberto e ainda não é o contrato de compatibilidade das bases/fila. Não editar código de um processo em execução; a atualização requer artefacto fixo e reinício coordenado. O controlo de formatos persistentes e as condições finais de readiness serão implementados nas etapas seguintes.
