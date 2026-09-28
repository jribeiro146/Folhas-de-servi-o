# Validação da aplicação completa migrada

Este documento regista a preparação inicial. A validação posterior às melhorias está em `09_MELHORIAS_E_VALIDACAO.md`, com uma execução completa nova, ensaios de interface/PDF e revisão independente.

Atualizado em 28/09/2026 após a clarificação de que só a administração fica excluída. O pacote inclui agora o backend real dos formulários em `aplicacao/`.

## Resultados observados

| Verificação | Resultado |
|---|---|
| Sintaxe Python | 40 ficheiros de código/ferramentas analisados sem erros, incluindo 36 módulos de `src/`. |
| Imports locais | Todos os imports `src.*` resolvem dentro de `aplicacao/`; não há dependência da administração. |
| Arranque Flask isolado | `python -B tools/run_test_version.py --check` terminou com `CHECK_OK`: página, lista de folhas e bootstrap de edição responderam com sucesso. |
| Dados de arranque | Três originais sintéticos e um rascunho em diretório temporário novo; armazenamento local, saída de rede bloqueada, email/Teams/worker desligados. |
| Testes Python | 360 casos cobertos com sucesso após os ajustes de anonimização: 359 passaram na execução completa e o caso restante passou na repetição dirigida (359 desmarcados nessa repetição). |
| Testes JavaScript | 32 passaram; zero falhas e zero testes ignorados. Sintaxe JavaScript válida. |
| Inventário final | 139 ficheiros no pacote, incluindo o manifesto; 138 entradas verificadas por SHA-256. |
| Fixture | Workbook gerado de raiz por `build_synthetic_fixture.py`, com LINK/FS, metadados e valores fictícios; nenhum Excel original de clientes foi copiado. |
| Preservação | Hashes das fontes originais mantidos. Adaptações limitadas à cópia, documentadas em `proveniencia.json`. |
| Segredos | Segredo Graph e webhook comparados sem exposição: ausentes do pacote da app. O valor FS_SECRET_KEY da origem é um marcador inseguro já referido pela lista de rejeição e pelos testes do código, não uma chave de sessão válida; a pendência está documentada no handoff privado. |

## Testes Python e correções da preparação

Comando principal executado na pasta `aplicacao/`:

```powershell
python -B tools/validate_migration.py
```

O primeiro ensaio dentro do sandbox sofreu restrições de acesso às pastas temporárias. A execução através da permissão da ferramenta manteve o mesmo isolamento de dados e rede e permitiu correr a bateria.

A fixture sintética inicialmente tinha contacto/telefone de instalação, mas o cenário original testava o fallback para «Pedido por»/telefone do cliente. O gerador foi ajustado para preservar esse cenário. A substituição do nome do técnico exigiu também corrigir a expectativa de iniciais para `TA`, conforme o algoritmo existente (primeiro e último nome). Não foi alterado o serviço para fazer os testes passar.

Depois da última execução completa (359 passaram, uma expectativa de nome ainda falhou), executou-se:

```powershell
python -B tools/validate_migration.py -k test_web_api_flow_send_and_cancel
```

Resultado: **1 passou, 359 desmarcados**. Este cenário exercita criar rascunho, guardar assinatura sintética, finalizar/arquivar localmente, repetir com idempotência e cancelar outra folha fictícia. Os 360 casos passaram entre a execução completa e a repetição dirigida; não se afirma que o último comando voltou a executar todos.

As suites cobrem regras FS/SADI, ficheiros/Excel, revisão/recuperação, assinaturas, fotografias, finalização, regressões de segurança, Graph/email/Teams com mocks, filas temporárias, logging e configuração. Os ensaios PDF automatizados usam renderizadores/subprocessos simulados; não certificam um navegador PDF real instalado.

## Verificação JavaScript e integridade

Na raiz do pacote:

```powershell
node ferramentas/validar.cjs
```

O verificador confere manifesto, JSON, sintaxe JS e os 32 testes. O preload rejeita os transportes Node cobertos por `bloquear-rede.cjs`; não é uma firewall do sistema operativo. Os próprios testes usam DOM/transportes simulados.

Resultado observado: **32 passaram, zero falhas, zero ignorados**; sintaxe JavaScript e integridade verificadas com sucesso.

A verificação admite apenas a fixture Excel sintética identificada e `.env.example` sem segredos; continua a rejeitar outros Excels, bases de dados, logs e ficheiros de segredos. Para conferir só a cópia: `node ferramentas/validar.cjs --integridade`.

## Limitações

- Nenhuma autenticação real Microsoft, comunicação externa, fila operacional ou deploy foi executado.
- Não foi iniciado um servidor persistente; o arranque foi exercitado através do cliente de teste Flask em processo.
- A inspeção visual anterior do laboratório local ficou bloqueada pela política de URLs do navegador. Não se contornou essa restrição, e não houve nova validação visual da aplicação nesta preparação.
- Não se validaram PDF real em Chromium/Edge, documentos longos em impressão, Docker, telemóveis físicos, Safari/iOS ou funcionamento integral offline.
- A checklist SADI continua condicionada à demo no código local. As notas de produção referem outra revisão, não copiada automaticamente.
- A imagem incluída no código é o rodapé corporativo do email; não foram copiadas assinaturas de clientes. Os nomes de técnico apresentados na app são de demonstração.
- As melhorias documentadas continuam pendentes; esta tarefa corrigiu o âmbito e a portabilidade do pacote, não o código operacional.
