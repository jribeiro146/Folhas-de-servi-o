# Melhorias e validação — 28/09/2026

Esta entrega desenvolve a cópia local da aplicação de folhas de serviço e checklists. Não altera o servidor nem ativa integrações operacionais. A revisão independente está registada separadamente em `11_REVISAO_INDEPENDENTE.md`.

## Alterações

- **Base única:** `aplicacao/` contém frontend e backend. As 37 cópias de `frontend/` foram comparadas por SHA-256 antes da remoção; eram idênticas. O estado anterior foi registado em Git. A configuração privada está excluída do repositório.
- **SharePoint:** publicação atómica do inventário apenas depois de completar a listagem; falhas de download não anulam remoções confirmadas. Conteúdo indisponível é separado do inventário. Rascunhos e anexos excluídos da lista são conservados. O estado, geração e sequência da atualização são partilhados entre processos, com recuperação de uma atualização interrompida.
- **Lista da aplicação:** aguarda a atualização pedida, aplica o inventário confirmado mesmo quando algum conteúdo falha e mantém pesquisa/documento em edição. Mostra falha e data de confirmação num aviso persistente, incluindo durante atualização automática.
- **Materiais e PDF:** impressão de todos os 12 materiais suportados, cabeçalho da tabela repetido, continuação de textos extensos e identificação/paginação. A interface impede criar uma 13.ª linha de materiais.
- **Interface móvel:** cabeçalho sem altura fixa que corte botões, ações acessíveis em grelha e textos longos com quebra.
- **SADI:** resumo persistente de pendências com navegação ao local/campo/equipamento, mensagens de assinaturas invalidadas e diálogos de confirmação com teclado/foco. As regras existentes de rascunho, assinatura e cliente ausente foram preservadas.
- **PWA:** nova versão dos recursos, indisponibilidade explícita e mensagem offline sem garantir persistência que não tenha sido confirmada. O manifest desta revisão já funcionava; foram acrescentados testes públicos e de sessão sem demo SADI.
- **Alojamento:** Docker com Chromium e HOME gravável; worker separado; aviso para ambiente implícito com Graph/Microsoft; teste de PDF sintético que verifica estrutura e texto. Procedimento de preparação/recuperação em `10_ALOJAMENTO_DOCKER.md`.

## Validação executada

A execução inicial confirmou 360 testes Python. Uma tentativa no sandbox foi impedida por permissões Windows nas pastas temporárias; a repetição autorizada manteve o mesmo lançador, dados sintéticos e rede bloqueada.

| Verificação final | Resultado |
|---|---|
| Bateria Python completa | **396 passaram**, numa única execução, 70,89 s. |
| Bateria JavaScript | **47 passaram**, zero falhas/ignorados. |
| Arranque isolado | `CHECK_OK`: página, lista e bootstrap disponíveis. |
| Smoke PDF real | Duas páginas legíveis, verificação com `pypdf`, sandbox do navegador ativo. |
| Relatórios extensos | FS com três páginas; SADI com sete; 12 materiais, 100 linhas de relatório, dez fotografias e marcadores finais preservados; identificação nas páginas. |
| Interface | 13 verificações funcionais SADI e 12 combinações de largura/idioma/reflow passaram; consola sem erros/avisos; tentativa de 13.º material recusada sem perder os 12 existentes. |
| Revisão do diff | `git diff --check` sem erros. |
| Integridade do pacote | 150 entradas SHA-256 (151 ficheiros incluindo manifesto), JSON e sintaxe JavaScript conferidos; sem ficheiros inesperados. |
| Revisão independente | 46 testes Python dirigidos e 47 JavaScript repetidos pelo revisor; condição P2 corrigida e revalidada; sem achados P1/P2 abertos no âmbito revisto. |

Os testes automáticos de comunicações usam mocks, sem filas, destinatários ou dados reais. A primeira execução após integração tinha 389 sucessos e uma expectativa desatualizada da versão de cache PWA; a expectativa foi corrigida e a bateria completa repetida. Os casos adicionais do contador/geração e da API estão incluídos na contagem final de 396.

A revisão independente identificou uma condição em que uma atualização B concluída antes de a interface observar A provocava um timeout falso. A correção usa geração e sequência persistentes para aceitar uma conclusão posterior comprovada, sem aceitar estados antigos ou de outra geração. Foram acrescentadas regressões no coordenador, na API e no controlador JavaScript. A conclusão da revisão encontra-se em `11_REVISAO_INDEPENDENTE.md`.

O ensaio multiprocesso encontrou ainda uma falha transitória Windows ao substituir um JSON aberto por outro leitor. A publicação atómica repete apenas `os.replace` local, por um período limitado, sem repetir pedidos ou efeitos Graph.

O ensaio `tools/smoke_pdf.py` executou o navegador real no Windows, com sandbox ativo, e validou duas páginas com `pypdf`. `tools/validate_reports.py` produziu uma FS extensa com 100 linhas e 12 materiais e uma SADI com dez fotografias fictícias. Os PDFs foram renderizados para imagens para inspeção de paginação e conteúdo; as contagens e marcadores finais também foram verificados por extração de texto. A inspeção detetou e corrigiu sobreposição da última linha de materiais na fragmentação de um container CSS grid. A versão final usa fluxo normal nos blocos extensos e margem de impressão para os rodapés.

Os detalhes e limites da emulação móvel estão em `12_QA_INTERFACE.md`. O teste de 200% usa CSS zoom para reflow; não comprova zoom nativo, teclado virtual ou dispositivos físicos. O browser e o servidor de teste foram terminados após o ensaio.

Artefactos de QA ficam fora do pacote, em `qa-output/reports/` e `output/playwright/`, ignorados pelo Git. São exemplos sintéticos, não documentos operacionais.

Os ficheiros de texto usam finais de linha LF, fixados por `.gitattributes`, para que checkout em Windows/Linux não altere os hashes do manifesto. Os hashes da captura original permanecem na proveniência; o manifesto identifica os bytes da entrega atual.

## Reproduzir

Na pasta `aplicacao/`:

```powershell
python -B tools/run_test_version.py --check
python -B tools/validate_migration.py
python -B tools/validate_reports.py
python -m pip install -r requirements-smoke.txt
python -B tools/smoke_pdf.py
```

Na raiz do pacote:

```powershell
node ferramentas/validar.cjs
```

Para inspeção da interface, usar o lançador `python -B tools/run_test_version.py --port 5012`. Cada arranque usa novos dados fictícios; não conserva demonstrações entre arranques. O serviço só escuta em loopback e bloqueia rede de saída Python.

## Pendências que exigem o ambiente de destino

- Construir/testar a imagem Docker; Docker não está instalado neste computador.
- Comparar revisão/configuração com o servidor e confirmar identidade, volume, montagens e permissões. As notas antigas não comprovam o estado atual do servidor.
- Ensaiar Android e iPhone físicos, Safari/iOS, toque/caneta e suspensão real. Emulação no browser não substitui esses ensaios.
- Preparar a ativação operacional da SADI e o piloto. Esta cópia conserva a restrição à demo.
- Configurar e validar autenticação/SharePoint reais antes de publicar; envios e deploy continuam a exigir a operação específica autorizada.

Offline integral, novas checklists e tradução técnica completa permanecem evoluções próprias. Não foram alteradas as regras comerciais nem acrescentada administração.
