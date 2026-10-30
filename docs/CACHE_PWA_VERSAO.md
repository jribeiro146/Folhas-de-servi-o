# Cache pública e atualização da PWA (M19)

O servidor liga o conteúdo de `/service-worker.js` ao SHA-256 integral da versão admitida pelo contrato de runtime. A cache chama-se `sensorpoint-service-static-<sha256>`; cada recurso público tem `?v=<primeiros 16 caracteres>`, como os URLs dos templates. O cabeçalho `X-App-Version` permite recusar respostas de outra versão. A identidade inclui o código e os ficheiros de entrega; não é um identificador de dados nem um segredo.

## Recursos e falhas

Só a lista explícita de CSS, JavaScript, ícones, manifesto e página offline do pacote entra na Cache API. A instalação verifica e consome integralmente os corpos de todas as respostas antes de escrever. Isso evita esgotar as ligações HTTP/1 enquanto outros downloads aguardam; mantém uma cópia pública em memória até à escrita. Um ficheiro em falta, resposta opaca/redirecionada, versão diferente ou falha de armazenamento impede a instalação. O servidor recusa um `v` obsoleto ou repetido com HTTP 409 em vez de atribuir esse URL aos bytes atuais.

A leitura da cache exige o URL exato e a identidade completa. Um worker antigo pode passar um recurso de uma página nova pela rede, se o cabeçalho corresponder ao `v` pedido, mas não o guarda na cache antiga. Sem rede, uma versão diferente recebe 503; não existe recuperação apenas pelo nome do ficheiro.

A Cache API não guarda HTML autenticado, APIs, folhas, assinaturas, fotografias, PDFs ou URLs assinados. Navegações vão à rede e, quando a ligação falha, só recebem a página offline pública da versão ativa. APIs GET vão à rede; mutações não são intercetadas. Respostas dinâmicas e privadas têm `Cache-Control: no-store` no servidor. Não se confunde esta cache pública com as cópias de recuperação já mantidas pelo coordenador de edição.

## Atualização sem perder o formulário

O worker não chama `skipWaiting` nem `clients.claim`. O JavaScript não recarrega a página em `controllerchange` e não envia mensagens para forçar a ativação. Quando existe uma atualização à espera, uma faixa pede que o utilizador confirme a gravação no servidor e feche todas as abas e janelas desta aplicação antes de a reabrir. A faixa não guarda dados, não comprova a gravação e não altera o formulário.

A nova versão é ativada quando deixam de existir clientes controlados pelo worker anterior. Só nessa ativação elimina as outras caches com o prefixo desta aplicação. Outras caches do domínio ficam intactas. A primeira instalação também não toma conta de páginas já abertas; controla uma navegação posterior.

**Transição do worker histórico:** um worker que já esteja instalado conserva o comportamento do seu próprio código até ser substituído. Estas proteções não corrigem retroativamente o worker histórico que fazia correspondência apenas pelo caminho. Para concluir a primeira transição, confirmar o trabalho guardado, fechar todas as janelas/abas/PWA e reabrir com ligação. Não instruir o técnico a limpar dados do navegador enquanto houver trabalho por sincronizar.

A política conserva abas abertas; não promete que uma aplicação inteira funcione offline, nem que um formulário antigo seja compatível com todas as futuras APIs. A migração operacional continua a exigir o procedimento de manutenção e contrato de runtime. Uma atualização que não consegue descarregar toda a cache fica por instalar e deve ser repetida com ligação.

## Validação repetível

- `python -B -m pytest -p no:cacheprovider tests/test_pwa_release.py tests/test_release_identity.py tests/test_runtime_admission.py tests/test_editing_state_service.py tests/test_maintenance_access.py -q` (isolamento pelo conftest; rede externa bloqueada).
- `node --test tests/js/*.test.cjs`: cache exata, mistura de versões, instalação incompleta, privacidade, ativação e aviso.
- `python -B tools/serve_pwa_synthetic.py --port 8765`: servidor apenas em 127.0.0.1, sem importar a aplicação/configuração, sem ler dados ou escrever filas. Usa os recursos reais e uma página fictícia com textarea. POST `/__test/release/b` troca a identidade sintética A por B em memória. Encerrar após o ensaio.
- Num perfil Chromium descartável: instalar A, navegar de novo para obter controlo, escrever no campo, trocar para B, pedir `registration.update()`, confirmar o worker em espera e o campo intacto; testar offline/online e os URLs A/B; fechar todos os clientes A, abrir B e verificar a ativação e remoção da cache A. Usar apenas o servidor local.
- `python -B tools/check_pwa_update.py` automatiza esse cenário com o Playwright Python opcional e Chromium já instalado. Cria o servidor numa porta loopback livre, bloqueia rede externa em Python e a resolução de nomes externos no Chromium; navega apenas na página fictícia e recursos locais. Encerra os recursos ao terminar. Resultados e imagens ficam em `output/playwright/`. Não depende de credenciais nem de uma aplicação em execução.

Este ensaio de cache não substitui a matriz funcional completa de web/worker de M16 nem demonstra a causa das queixas históricas. Os testes de servidor usam Flask real; o ensaio de atualização usa o servidor sintético para mudar de versão sem modificar uma instalação operacional.

