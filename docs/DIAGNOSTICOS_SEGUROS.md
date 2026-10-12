# Diagnósticos seguros — P2 M09

Implementação local de 12/10/2026. Complementa a identidade administrativa M02 e a propriedade dos trabalhos M03. Não implica configuração, ensaio de serviços reais ou instalação.

## Contratos e permissões

| Superfície | Acesso e resposta |
|---|---|
| `GET /api/graph/status` | Mantém a autenticação normal da aplicação. Técnicos recebem backend, disponibilidade Graph e atualização da lista. Só o administrador M02 recebe indicadores booleanos de configuração e contagens da fila. |
| `GET /api/graph/test`, `GET /api/mail/test` | Exigem a identidade administrativa configurada antes de contactar o fornecedor. Sem autenticação: 401; identidade insuficiente: 403 `diagnostic_access_denied`. Sucesso não devolve objetos Graph, remetente, destinatário de teste ou caminhos. |
| `POST /api/graph/sync`, `GET /api/files?refresh=1` | Continuam disponíveis aos técnicos autenticados; a projeção segura preserva o protocolo de atualização da lista. |
| Callback/início Microsoft | Redirecionam falhas com mensagem fixa, código e referência. Descrições do fornecedor e mensagens de exceção não entram em `Location`. A recusa do fornecedor limpa state/nonce/next pendentes. |
| Administração operacional | Mantém autorização e CSRF existentes. Trabalhos devolvem fase enumerada e códigos, sem `last_error` bruto. Histórico devolve ação, data, rótulo do administrador, alvo e motivo sanitizados; não exporta email nem detalhes internos de auditoria. |

Em modo local sem autenticação Microsoft, os dois testes de fornecedor ficam recusados. Isto não impede abrir/editar documentos nem atualizar a lista. A identidade administrativa é definida em `IDENTIDADE_ADMINISTRATIVA.md`; não existe fallback por email ou role de sessão.

`safe_refresh_status` preserva `in_progress`, outcomes enumerados, IDs limitados, sequências e tempos numéricos, assim como disponibilidade, desatualização e contagens do inventário. Substitui erros por aviso fixo de lista possivelmente incompleta/desatualizada. Não exporta listas internas `synced_files`/`unavailable_files`. `safe_service_status` permite apenas booleanos e nomes conhecidos de configurações em falta, nunca os respetivos valores. `safe_queue_summary` permite apenas contagens conhecidas.

## Referências e mensagens

As falhas de diagnóstico devolvem `code`, `reference` e mensagem segura; a referência corresponde a `X-Request-ID` e integra também o texto de `error`, para aparecer nos toasts existentes. O mesmo se aplica às recusas `admin_action_rejected`. Falhas Microsoft redirecionam com estes campos na query e a página de login mostra a referência. Eventos estruturados permitem procurar a referência/código sem guardar tokens nem corpos de resposta do fornecedor. Exemplos: `graph_diagnostic_failed`, `mail_diagnostic_configuration_failed`, `mail_diagnostic_failed`, `auth_graph_preflight_failed`, `auth_start_failed`, `auth_callback_failed`, `mail_preflight_failed`.

Os logs conservam causas enumeradas (`missing_mail_send`, `invalid_client_secret`, `invalid_access_token`, `configuration_missing`, `identity_invalid`, `token_rejected`, `access_denied`, `rate_limited`, `unavailable`, `configuration_invalid` ou `provider_rejected`) e o status HTTP numérico obtido da cadeia explícita de exceções. São pistas de diagnóstico, não uma decisão de repetição nem prova de entrega. Códigos OAuth do callback só entram no log se pertencem à lista conhecida; a descrição não entra. Para classificar a falha Microsoft, lê-se no máximo 64 KiB do corpo em memória e conserva-se só a causa enumerada. Não são enviadas mensagens livres do fornecedor ao log por este mecanismo.

Falhas inesperadas das operações de documentos devolvem `operation_failed`. Mensagens de validação/conflito existentes continuam úteis, passando por sanitização limitada a 4096 caracteres. Nem todas estas respostas antigas têm `reference` no corpo; o cabeçalho de correlação mantém-se. A informação de conflito e os dados funcionais necessários à edição mantêm o contrato existente.

O transporte Microsoft não inclui corpos HTTP nem motivos de URLError nas mensagens públicas. Os códigos `microsoft_http_failed`/`microsoft_unavailable` distinguem a classe de falha. Não são diagnósticos do conteúdo do fornecedor nem comprovação de disponibilidade real.

## Logs e evidência interna

O formatter JSON aplica remoção de bearer tokens, atribuições de segredos, data URLs, URLs HTTP completas, emails e caminhos absolutos Windows/UNC/POSIX em mensagens, valores estruturados, stack e exceções. Conserva códigos, referências, tipos de exceção e metadados operacionais. Rotas estruturadas provêm do template Flask, sem query nem nomes concretos de documentos. A remoção conservadora de caminhos pode também encurtar o texto que os acompanha.

A fila e a auditoria privada conservam os registos originais para reconciliação. Esta alteração é uma projeção de saída: não migra, limpa ou publica bases de dados, nem altera a decisão de repetição de um envio. A auditoria original pode conter informação sensível e não deve ser copiada como diagnóstico exportado. Os dados funcionais autorizados de documentos e reservas não são anonimizados por este ponto.

O cartão administrativo mostra a referência `job.id` e distingue falha de processamento, resultado incerto que exige reconciliação e estado inválido que exige recuperação. Procurar esse ID no campo `job_id` do evento `graph_job_failed` dos logs do worker. Esse evento conserva tipo/causa/status seguros e exceção sanitizada. O teste percorre a gravação real em SQLite e comprova que o erro original permanece privado e o log exportado contém ID/causa sem os marcadores. Trabalhos antigos podem não ter logs retidos; não inferir uma causa perdida nem reenviar por falta de evidência.

Não há uma garantia de reconhecer qualquer segredo arbitrário em texto livre. As respostas de fornecedor usam mensagens fixas/projeções, enquanto a sanitização textual cobre os formatos explicitamente testados e complementa a proibição de registar conteúdo privado. Não acrescentar novos campos brutos às projeções.

## Verificação e manutenção

### Correção do rótulo administrativo após a terceira revisão

João autorizou até três novas rondas por cada um dos dois achados em 12/10/2026. O primeiro é a distinção entre espera e resultado incerto. `safe_job` mantém a guarda conservadora `reconciliation_required` e `can_retry`, mas calcula separadamente a categoria apresentada. Trabalhos `held`/`pending`/`running` sem evidência de envio, nas fases de preparação, não recebem `job_reconciliation_required`; a interface também não lhes apresenta o aviso genérico de revisão. Evidência de envio incerto ou falha sem prova suficiente conserva a categoria e o bloqueio. Estados inválidos continuam a exigir recuperação. Não há alteração de persistência, schema ou reenvio.

Validação dirigida: 150 Python aprovados em 24,74 s (`test_diagnostic_privacy`, `test_operational_admin`, `test_admin_identity`, `test_graph_job_visibility`; log `p2-m09-label-r1.txt`), incluindo as duas espécies de trabalho de email e os estados/fases de preparação. JavaScript completo: 90 aprovados em 0,72 s (`p2-m09-label-node-r1.txt`). A recusa de domínio é o segundo achado, ainda por corrigir nesta revisão isolada. Esta aprovação isolada, se obtida, não fecha o M09 sem rever cumulativamente o conjunto.

Testes sintéticos com `tests/conftest.py`/`tools/test_runtime.py`: diretórios temporários novos, rede bloqueada, mail/Teams/worker no web desativados. Com `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, executar `python -B -m pytest -p no:cacheprovider` nos grupos relevantes.

- Regressão de diagnóstico, logging, Microsoft, admin, arquivo/web, visibilidade de trabalhos, identidade administrativa e atualização Graph/lista: 255 aprovados, 71,29 s (`qa-output/consolidacao-20261007/p2-m09-regression-final.txt`, relativo à raiz Migração).
- Após a projeção final do histórico administrativo: 97 aprovados, 27,44 s, nos quatro módulos `test_diagnostic_privacy`, `test_operational_admin`, `test_admin_identity`, `test_logging_config` (`p2-m09-admin-final.txt`).
- Após a adaptação da apresentação do histórico: `node --test tests/js/*.test.cjs`, 79 aprovados, 1,29 s (`p2-m09-node-final.txt`).
- Ronda 2, depois de conservar causas/status seguros nos logs e mostrar referência no login: os mesmos dez módulos de regressão passaram, 266 aprovados em 41,65 s (`p2-m09-regression-r2-final.txt`). Inclui causas/configuração, status HTTP do callback, OAuth permitido, referência visível, erro externo sanitizado no login e preservação do número da linha no traceback. JavaScript não mudou nesta ronda; mantém-se a evidência anterior. A tentativa anterior teve 265 aprovados/1 falha de expectativa exata de campos no teste temporal M01; expectativa adaptada ao campo novo e causa local `identity_invalid` (`p2-m09-regression-r2.txt`).
- Ronda 3, após incluir a referência no texto visível, rótulos administrativos e causa no evento do worker: 288 Python aprovados, 80,99 s, nos dez módulos anteriores mais `test_durable_mail` e `test_durable_teams` (`p2-m09-regression-r3.txt`). JavaScript: 87 aprovados, 2,83 s (`p2-m09-node-r3.txt`), incluindo as quatro ações reais extraídas do editor com HTTP simulado e a apresentação administrativa; nenhum POST repetido para obter a referência.

Os casos incluem matriz administrador/técnico/anónimo, ausência de chamada externa antes da autorização, preservação dos IDs/sequências de atualização, marcadores fictícios em JSON/Location/logs, caminhos com espaços, preflight de email, callback e erros de transporte, e conservação do original privado da auditoria. Os resultados anteriores não são somados como testes únicos.

Não houve ensaio Microsoft/Graph real, browser integrado nem CI remoto desta revisão. A matriz integrada continua em M16. O parecer/check e o commit exato ficam no registo de revisão da raiz; uma aprovação anterior não cobre alterações posteriores.

Não exige variáveis novas nem migração. Para reverter, regressar ao artefacto completo anterior segundo o procedimento de instalação, preservando as bases privadas; não substituir isoladamente JavaScript ou módulos em execução. A reversão também repõe a exposição anterior dos diagnósticos e deve ser avaliada antes de instalar.
