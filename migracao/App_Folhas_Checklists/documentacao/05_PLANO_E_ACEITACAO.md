# Plano para continuar a aplicação noutra pasta

Atualização de implementação: consultar `09_MELHORIAS_E_VALIDACAO.md` para o trabalho concluído, evidência e pendências. Este plano conserva as decisões funcionais e a sequência de referência.

O frontend e o backend dos formulários estão incluídos. O trabalho começa a partir desta base executável; não é necessário construir um backend novo para substituir o existente. A aplicação administrativa permanece fora do âmbito.

## Sequência de trabalho

| Etapa | Trabalho | Verificação |
|---|---|---|
| 1 — Transferência | Copiar `App_Folhas_Checklists` completa; preparar Python/dependências. | Integridade, testes JS, testes Python e arranque `--check`. |
| 2 — Utilização isolada | Arrancar `aplicacao/tools/run_test_version.py`. | Formulários com backend local, rascunhos e SADI sintéticos; sem acesso aos dados originais. |
| 3 — Rever regras | Ler documentos 02–04 e decidir divergências de validação, materiais, assinatura e idiomas. | Decisões explícitas e critérios de aceitação antes de alterar comportamentos. |
| 4 — Melhorias da app | Corrigir UX, conteúdo truncado, pendências e consistência de dados. | Matriz A01–A24 do documento 04, com mocks e dados fictícios. |
| 5 — PDF e mobilidade | Validar documentos longos, fotografias, assinatura por toque e suspensão. | Evidência própria do dispositivo/browser e renderizador de destino. |
| 6 — Integração autorizada | Configurar backend com segredos privados e diretórios próprios. | Rever identidade, retorno do login, permissões, destinos e filas; começar por mocks. |
| 7 — Alojamento | Preparar configuração específica Docker ou WSGI, recuperação e validação. | Só publicar com autorização; não assumir que o Compose de referência reproduz produção. |

## Decisões funcionais pendentes

- Cliente ausente na FS não dispensa automaticamente a assinatura do cliente SADI. Explicar ou definir formalmente uma nova política.
- Na demo atual pode recolher-se assinatura com campos por preencher, mas completar/alterar conteúdo poderá invalidá-la. Finalizar exige resolver as pendências.
- Resolvido nesta entrega: UI limitada aos 12 materiais suportados e impressão sem corte nas primeiras cinco linhas. A paginação foi ensaiada com relatório extenso.
- Uniformizar duração, arredondamento, validações numéricas e IDs entre JS e Python.
- Decidir a extensão da tradução PT/EN da checklist.
- Offline integral exige uma fase própria; PWA instalada não significa documentos, fotos e conclusão disponíveis sem rede.
- Outros sistemas têm opções na FS, mas só a checklist SADI está definida. Não inventar catálogos técnicos.
- O código SADI local continua limitado à demo; conciliar com a versão implantada antes de decidir ativação operacional.

## Definição de pronto das melhorias

Campos e relatórios coerentes, sem truncagem; rascunhos/recuperação sem perda; estados de edição e assinatura claros; testes funcionais com resultados por cenário; relatórios extensos inspecionados; ações alcançáveis em telemóvel/teclado; integrações reais validadas apenas quando autorizadas. Um teste com mock não comprova entrega de email ou arquivo remoto.

## Prompt de passagem

> Continua o desenvolvimento desta aplicação completa de folhas de serviço e checklists. Lê README.md, AGENTS.md e documentos 01–08. A pasta aplicacao contém o frontend e o backend real. Exclui apenas o módulo administrativo, faturação e Registo Admin. Usa o lançador de teste isolado e os mocks; preserva regras, dados e proteções existentes. Implementa melhorias priorizadas sem reescrever desnecessariamente os serviços. Credenciais ficam fora do frontend; não contactar serviços, enviar mensagens, usar filas operacionais ou publicar sem a autorização correspondente.
