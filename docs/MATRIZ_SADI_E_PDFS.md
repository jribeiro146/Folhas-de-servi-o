# Matriz SADI, acessos e PDFs (M18)

## Execução isolada

`python -B tools/check_sadi_pdf_matrix.py` usa as rotas Flask, sessões assinadas, política de acesso, armazenamento privado, assinaturas, fila e preparação de email reais, com dados fictícios. O Chromium renderiza os templates reais. O login Microsoft, armazenamento Graph e transporte de email são simulados; não há entrega a destinatários.

Preparar uma venv de QA com `requirements-server.txt` e `requirements-browser-tests.txt`, e instalar previamente o Chromium com `python -m playwright install chromium`. A dependência pytest permite reutilizar a fábrica sintética dos testes, evitando uma segunda configuração divergente. Estas dependências opcionais não são instaladas no Docker de produção. O ensaio observado usou Python 3.13.1, pytest 9.0.2, Playwright 1.61.0, Chromium 149.0.7827.55 e pypdf 6.20.0; as constraints Linux de QA podem selecionar pytest 9.1.1 dentro do intervalo declarado.

O lançador configura uma pasta temporária nova e bloqueia a rede antes de importar a aplicação. Mantém as flags operacionais de comunicação desativadas e injeta transportes fictícios estritos. O navegador admite apenas ficheiros locais/data; qualquer pedido diferente faz falhar a matriz. A cópia de código com manifesto e revisão inventada é uma fixture de teste, não um pacote para instalação. O processo fecha Chromium e o seu contexto mesmo quando o ensaio falha.

## Critérios e cobertura

| Caso | Evidência e resultado exigido |
|---|---|
| João e Ana | Leitura, validação, gravação/reabertura privadas; preview e PDFs permitidos; cabeçalhos `no-store` |
| Outra conta | Folha pública sem conteúdo SADI; preview, bundle e downloads privados devolvem 404. No cenário com reserva de João, bootstrap devolve 423 sem conteúdo privado |
| Dois locais | Cada checklist PDF contém apenas o marcador do próprio local; fotos, texto e assinaturas legíveis |
| Cliente ausente | Dispensa apenas a assinatura do cliente; a assinatura do técnico continua obrigatória, mesmo com tentativa de dispensa forjada |
| Alteração posterior | Mudar um local invalida a assinatura desse local, conserva as do outro e não modifica o JSON privado durante a validação |
| Dados antigos | Fixture sintética escrita pelo commit `1ede4d8`, lida por João e Ana sem migração nem alteração de bytes, hash, mtime ou permissões; conta restante sem acesso |
| Publicação/email | Três PDFs no bundle privado; arquivo público sem SADI; exatamente um email simulado com um PDF da folha de serviço; hash do anexo igual ao PDF produzido |
| Falha ao publicar o diretório privado | PermissionError injetado em rename: HTTP 500, rascunho conservado, sem bundle parcial nem trabalho enfileirado; repetição explícita após remover a falha conclui |
| Cache, recuperação e chaves | Regressões existentes de service worker/JavaScript, acesso e chave SADI; retoma M22 identificada abaixo |

`tests/test_sadi_matrix.py` cobre dispensa, invalidação por local e falha de publicação. `tests/test_maintenance_access.py` reutiliza a mesma fábrica e verifica também legado e recusas. A matriz PDF usa o cliente HTTP de teste Flask; não é uma nova jornada de cliques no editor. Os percursos de navegador pertencem a [M16](PERCURSOS_INTEGRADOS.md), e os testes de cache continuam no M19. Não se acrescentam tipos de checklist nem se alteram perguntas ou dados operacionais.

## Evidência de 14/10/2026

- Regressões Python: **276 aprovados, 1 skip POSIX no Windows**, 118,57 s. Comando: `python -B -m pytest -p no:cacheprovider tests/test_sadi_matrix.py tests/test_maintenance.py tests/test_maintenance_access.py tests/test_sadi_signature_key.py tests/test_document_reports.py tests/test_mail_signature.py tests/test_durable_mail.py tests/test_graph_job_visibility.py tests/test_diagnostic_privacy.py tests/test_archive_and_web.py -q`, com `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`. Log local `qa-output/consolidacao-20261007/p2-m18-python-r1.txt`.
- JavaScript: **175 aprovados**, 5,615 s, `node --test tests/js/*.test.cjs`; log `p2-m18-js-r1.txt` no mesmo diretório de evidência.
- Matriz PDF: cinco grupos PASS; `output/pdf/m18-fst-d675ae78/result.json`, log `p2-m18-pdf-dev3.txt`. Identidade da árvore testada: `dbbfa577d058db1b0d8ce8862f04b24cfee5345c8fe1e1095a426a7aabfc2cac`, 120 ficheiros de fonte. O relatório identifica corretamente a árvore ainda sem commit; o manifesto fictício é separado.
- Inspeção visual: todos os PDFs renderizados para PNG com Poppler; sete páginas distintas inspecionadas, sem sobreposições, cortes ou texto ilegível. Alfa e Beta têm três páginas cada; a folha e o anexo têm uma página cada. O anexo tem o mesmo PNG da folha, embora os bytes PDF difiram devido às renderizações separadas. Registo local `visual-review.json` contém os hashes dos ficheiros inspecionados.

Os artefactos ficam ignorados pelo Git, com o armazenamento fictício preservado para diagnóstico. O lançador só escreve `result.json` depois de todos os controlos automáticos passarem; a observação visual é uma etapa separada, registada depois, e não uma afirmação automática do script. Ensaios preliminares preservados corrigiram expectativas do próprio harness: o bootstrap de outra conta encontra uma reserva ativa (423), e os títulos extraídos do PDF refletem o CSS em maiúsculas. Não exigiram alterar código operacional.

## Investigação Windows e limites

O log histórico `ci-pre-review-python.txt` contém 1084 aprovados, 4 skips e uma falha WinError 5 em `os.rename` da publicação privada. O ensaio limitado subsequente `ci-sadi-diagnostic.txt` passou 24 testes com um skip; as suites posteriores M15/M16/M17 e a atual também não demonstram a causa original. Não há evidência para atribuí-la ao antivírus, OneDrive ou a um defeito específico de handles. Não se oculta o resultado antigo nem se acrescenta retry automático para o mascarar.

A nova injeção determinística verifica a conservação do rascunho e a recuperação explícita nessa fronteira; não reproduz a causa ambiental do WinError 5. As permissões POSIX são verificadas separadamente no CI Linux. Este ensaio não é uma execução no servidor AlmaLinux nem altera a sua configuração.

O diagnóstico histórico M22 de bootstrap offline e três abas conserva a falha em `p2-m22-offline-bootstrap-reproduction.txt`. Na retoma autorizada de 14/10, o fecho da editora repõe retry mesmo sem sessionToken e uma nova sondagem descarta o estado antigo de outra aba. Os testes de recuperação e o ensaio Chromium integrado identificam esta correção; consultar [chave e recuperação](ASSINATURAS_SADI_E_CHAVE.md), [percursos](PERCURSOS_INTEGRADOS.md) e o [estado atual](ESTADO_ENTREGA_2026-10-14.md). O resultado histórico M18 não aprova retroativamente a retoma M22.
