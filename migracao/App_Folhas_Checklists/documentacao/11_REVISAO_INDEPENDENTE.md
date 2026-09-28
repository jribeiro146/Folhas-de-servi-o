# Revisão independente — 28/09/2026

## Conclusão

Revisão concluída sobre as alterações locais relativamente à base Git `faa7ef8`. Foi identificado um problema P2 na correlação de atualizações concorrentes da lista. A equipa de implementação corrigiu-o e a revisão independente confirmou a correção. Não ficaram achados P1/P2 abertos no âmbito revisto.

Esta conclusão permite aceitar as melhorias da cópia local para continuação dos ensaios. Não comprova prontidão de produção, funcionamento da imagem Linux nem integração operacional. Mantêm-se as verificações de destino e dispositivos descritas nos documentos 09, 10 e 12.

## Independência e âmbito

A revisão foi feita por um agente criado após a implementação, sem participação nas alterações de código. Uma sub-revisão independente verificou interface, PWA e configuração Docker. Os revisores não corrigiram a implementação; o único ficheiro criado pela revisão foi este relatório. O achado foi devolvido à equipa e novamente verificado depois da correção.

Foram lidos `AGENTS.md`, os diffs de implementação, os novos testes e ferramentas, os documentos 09/10/12 e os contratos relacionados. A análise incidiu em:

- publicação completa e atómica do inventário SharePoint, falhas parciais de conteúdo e conservação de rascunhos/anexos;
- exclusão entre processos, recuperação de interrupção e correlação entre pedido, estado e resposta da API;
- conservação da edição/pesquisa ao atualizar a lista e apresentação persistente de falhas;
- pendências SADI, estados de assinatura, confirmações de remoção, materiais e paginação;
- PWA, isolamento das ferramentas sintéticas, configuração Docker e limites declarados na documentação.

Também foi repetida a comparação dos 37 ficheiros removidos de `frontend/` com os correspondentes de `aplicacao/` na base Git: todos eram idênticos por SHA-256. A atualização final do manifesto/proveniência pertence ao fecho da integração e não foi tratada como um defeito durante a revisão.

## Achado e correção

**R1 — P2 — Uma atualização concluída podia originar uma falsa falha. Resolvido.**

Local original: `aplicacao/src/web/static/js/document-editor.js:1746`, condição de espera por `last_completed_refresh_id`; contrato persistente em `aplicacao/src/services/graph_sync_coordinator.py`.

Reprodução: o browser pede A; A termina; antes do próximo poll, outro cliente pede B e B também termina. O backend conservava apenas o identificador de B. Como a interface exigia igualdade com A, executava 40 polls e mostrava «Não foi possível confirmar o fim desta atualização», mantendo a lista antiga apesar de o inventário B estar confirmado. A sub-revisão reproduziu o comportamento com o controlador real e transportes simulados.

A correção acrescenta `generation_id`, `refresh_sequence` e `last_completed_sequence` ao estado persistente, protegido pelos mesmos locks. A API devolve a geração/sequência do pedido original. A interface aceita a conclusão do próprio pedido ou de um pedido posterior na mesma geração. Estados antigos continuam legíveis; perda/corrupção do estado inicia uma geração diferente.

A repetição verificou A/B concluídos entre polls, rejeição de sequência anterior e de outra geração, preservação do token original na API, migração do estado antigo e conclusão explícita de uma execução interrompida. Estes casos passaram.

## Verificação executada pela revisão

Na pasta `aplicacao/`:

```text
python -B tools/validate_migration.py -k 'active_file_index or graph_refresh_coordinator or document_reports'
34 passed, 362 deselected

python -B tools/validate_migration.py -k 'file_refresh_api or hosting_readiness'
12 passed, 384 deselected

node --test --test-isolation=none tests/js/*.test.cjs
47 passed, 0 failed
```

Os 46 casos Python selecionados usaram o lançador oficial, novas pastas temporárias, fixtures fictícias, rede de saída bloqueada e integrações desligadas. A primeira tentativa no sandbox encontrou `WinError 5` na criação da pasta temporária do pytest; a repetição autorizada fora dessa restrição conservou todo o isolamento da aplicação. Não se interpretaram as falhas de permissões como defeitos do produto.

Os testes incluem concorrência entre processos reais com transporte Graph sintético, paginação incompleta, falha de download, inventário ausente/inválido/ilegível, conservação de rascunhos, materiais 1–12, texto extenso, manifest público e modo explícito de ambiente. `git diff --check` não encontrou erros.

## Limites

A revisão não contactou SharePoint, Microsoft, email, Teams ou outros serviços operacionais; não leu credenciais privadas, não iniciou filas reais e não fez deploy. A restrição existente da SADI à demonstração isolada foi preservada.

As evidências de PDF real e da matriz browser constantes dos documentos 09/12 foram analisadas como registos da equipa de implementação. A revisão independente repetiu os testes de código e contratos descritos acima; não repetiu toda essa inspeção visual nem a bateria Python completa. Os resultados completos finais pertencem ao documento 09.

Docker não foi construído neste computador. Não foram ensaiados dispositivos físicos, Safari/iOS, toque/caneta, zoom nativo, teclado virtual, suspensão real ou leitor de ecrã. A validação local não substitui esses ensaios nem uma comparação com a configuração efetiva do servidor.
