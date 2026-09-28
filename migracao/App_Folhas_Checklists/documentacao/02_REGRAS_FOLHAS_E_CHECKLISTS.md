# Regras funcionais — folhas de serviço e checklists

Data da análise: 28-09-2026. Âmbito: modelo funcional, preenchimento, validação e utilização da app. O backend dos formulários, armazenamento, transportes, autenticação, filas e implementações de assinatura estão incluídos em aplicacao/. Apenas a administração fica fora da migração. Os testes continuam com dados fictícios e transportes simulados.

Este documento resulta de leitura estática do código e dos testes existentes no diretório de trabalho, incluindo alterações ainda não commitadas. Não foram executados testes nem a aplicação nesta análise. «Implementado» significa observado no código; um teste citado é uma especificação existente, não uma execução confirmada nesta entrega. As referências `ficheiro:linha` dizem respeito à árvore original na data da análise.

## 1. Modelo e fronteiras funcionais

- Uma folha de serviço contém identificação, cliente/contactos/local, tipos de serviço, sistemas, pedido, relatório, materiais, registos de técnicos/horas, assinatura do cliente e, quando aplicável, checklists de manutenção por local.
- O formulário admite guardar rascunhos incompletos; a validação de conclusão ocorre ao finalizar. A interface refere explicitamente esta possibilidade em `src/web/templates/field_app.html:421`; o validador de frontend a distingue em `src/web/static/js/document-validation.js:9`.
- As checklists presentes no código são **SADI**. Não existem neste catálogo regras implementadas para todos os outros sistemas; a seleção desses sistemas não implica que tenham uma checklist própria.
- A checklist só se aplica quando `service_types.manutencao` e `equipments.sadi` estão ambos ativos (`src/maintenance_schema.py:134`; `src/web/static/js/maintenance-model.js:4`). Manutenção de outro sistema e assistência SADI não a ativam.
- A implementação original limita validar/assinar checklists à demo isolada (`src/web/application.py:718`, `src/web/application.py:733`) e impede finalizar documentos com checklists fora dessa demo (`src/web/application.py:1670`). Não apresentar esta funcionalidade como já operacional em produção. No novo frontend, desenvolver com adaptadores simulados e tratar a disponibilização operacional como trabalho futuro.

## 2. Folha de serviço: dicionário de campos

Os campos simples são normalizados para texto sem espaços iniciais/finais. Valores ausentes tornam-se texto vazio. Campos desconhecidos não entram no documento normalizado. Fonte: `src/document_schema.py:77`, `src/document_schema.py:181`, `src/document_schema.py:847`.

| Chave | Significado e regra atual |
|---|---|
| `document_language` | `pt` ou `en`; qualquer outro valor passa para `pt`. |
| `service_number` | Número da folha, apenas leitura na UI; a aplicação original repõe a identidade canónica do ficheiro. |
| `customer_name` | Nome do cliente; obrigatório para finalizar. |
| `requested_by`, `request_date` | Requisitante e data do pedido; opcionais na validação funcional. |
| `customer_number` | Número do cliente; texto opcional. |
| `nif_number`, `vat_number` | Duas chaves para um único valor fiscal; prevalece NIF não vazio e o valor é replicado nas duas. Não há cálculo/verificação de dígito de controlo. |
| `customer_email`, `customer_phone` | Contactos do cliente. Email usa input HTML de email; a validação na finalização original depende da disponibilidade do serviço de email. |
| `site_contact`, `site_phone` | Pessoa/contacto e telefone no local; opcionais. |
| `local_store`, `store_number` | Identificação do local/loja e número da loja; opcionais na FS. Não substituem a identificação obrigatória de cada local SADI. |
| `work_number` | Número de obra; 1–4 algarismos normalizados para exatamente 4; inválido fica vazio. Apresentado como ligação/valor não editável na UI de campo. |
| `contract_number` | Número do contrato; texto opcional. |
| `address` | Morada composta, com remoção de código postal/localidade repetidos. |
| `requested_tasks` | Trabalhos a efetuar/avaria reportada; texto multilinha opcional na validação atual. |
| `intervention_report` | Relatório técnico da intervenção; texto multilinha opcional na validação atual. |
| `customer_signer_name` | Primeiro e último nome do signatário; obrigatório se cliente presente. |
| `customer_signature_date` | Data da assinatura; obrigatória se cliente presente. |

Fontes: `src/document_schema.py:60`, `src/document_schema.py:149`, `src/document_schema.py:188`, `src/document_schema.py:376`; `src/web/templates/field_app.html:216`, `src/web/templates/field_app.html:247`, `src/web/templates/field_app.html:251`, `src/web/templates/field_app.html:281`; `src/web/application.py:706`, `src/web/application.py:1676`.

| Estrutura | Conteúdo |
|---|---|
| `service_types` | Mapa das oito opções abaixo para booleanos; múltipla seleção permitida. |
| `equipments` | Mapa dos dez sistemas abaixo para booleanos; múltipla seleção permitida. |
| `materials_used` | Booleano; inicia a `false`. |
| `materials[]` | Linhas `{ref, description, qty}`; máximo 12 no normalizador. |
| `technician_records[]` | Linhas `{technician, start_time, end_time, total_hours, total_hours_overridden, date}`; máximo 4. |
| `client_not_present` | Booleano que dispensa a assinatura do cliente da folha e os respetivos nome/data. |
| `maintenance_checklists[]` | Checklists por local, com o esquema descrito na secção 5. |

Fontes: `src/document_schema.py:101`, `src/document_schema.py:130`, `src/document_schema.py:138`, `src/document_schema.py:168`, `src/document_schema.py:215`.

### 2.1 Tipos de serviço

| Chave da app | Nome | Rótulo legado LINK |
|---|---|---|
| `piquete` | Piquete | PIQ |
| `assistencia` | Assistência | ASSIST |
| `manutencao` | Manutenção | MAN |
| `formacao` | Formação | FORM |
| `colocacao_servico` | Colocação de serviço | COL.SERV |
| `reparacao_oficina` | Reparação oficina | REP.OF |
| `garantia` | Garantia | GAR |
| `instalacao` | Instalação | INST |

Fonte: `src/document_schema.py:18`. O mapa legado ainda contém `ACOMP.COM`, mas essa opção não existe em `SERVICE_TYPE_OPTIONS`; não acrescentá-la silenciosamente ao novo formulário (`src/field_map.py:98`).

### 2.2 Sistemas

| Chave | Nome aprovado na app | Rótulo legado LINK |
|---|---|---|
| `sadi` | SADI | SADI |
| `vss` | VSS | CCTV |
| `sadco` | SADCO | PA/VA |
| `sadir` | SADIR | SAI |
| `sadei` | SADEI | EXT |
| `sca` | SCA | SCA |
| `eas` | EAS | EAS |
| `sadg` | SADG | SADG |
| `sch` | SCH | SCH |
| `other` | OTHER | OTHER |

Fonte: `src/document_schema.py:29`. Compatibilidade: converter `adco` em `sadco` e `ther` em `other` somente quando a chave atual não existir (`src/document_schema.py:209`). Preservar esta nomenclatura e ordem. A opção OTHER não tem campo descritivo obrigatório no esquema atual.

### 2.3 Catálogo de técnicos e número de obra

O projeto contém um catálogo local de técnicos, mas a validação do documento aceita texto não vazio, sem confirmar pertença à lista (`src/document_schema.py:42`, `src/document_schema.py:384`). No pacote de migração usar apenas técnicos fictícios/configuráveis, sem copiar nomes de colaboradores. A UI original usa uma lista de seleção (`src/web/templates/field_app.html:563`).

O número de obra aceita inteiros de 0 a 9999, incluindo zero, e strings com 1–4 dígitos ASCII. Exemplos: `7` → `0007`; `1234` → `1234`; valores negativos, frações, booleanos, sinais, letras e cinco dígitos são inválidos. A leitura legada dá prioridade ao valor de obra não vazio do Excel; um valor Excel não vazio mas inválido impede recuperar o número antigo do JSON. Fonte: `src/document_schema.py:105`, `src/document_schema.py:252`; testes em `tests/test_document_schema.py:55`, `tests/test_document_schema.py:67`, `tests/test_document_schema.py:83`.

## 3. Folha de serviço: regras de preenchimento e cálculo

### 3.1 Obrigatoriedade e datas

1. Exigir nome do cliente.
2. Exigir pelo menos um registo técnico. Uma lista vazia gera uma linha vazia e os cinco erros correspondentes.
3. Em cada linha técnica usada, exigir nome, início, fim, total de horas e data. Ignorar linhas inteiramente vazias. Um total calculado a partir de início/fim satisfaz o campo total.
4. Se o cliente estiver presente, exigir nome do signatário com pelo menos duas palavras separadas por espaços, data válida e desenho da assinatura do cliente. Acentos, apóstrofos e nomes compostos não são proibidos.
5. Se `client_not_present=true`, dispensar apenas nome/data/assinatura do cliente da FS. Continuar a exigir cliente e registos técnicos. A interface apaga a assinatura da FS e a exportação legada acrescenta a indicação de ausência nas observações.
6. Datas técnicas e da assinatura devem usar `AAAA-MM-DD` e existir no calendário. Início/fim devem ser `HH:MM`, entre 00:00 e 23:59. A data do pedido não tem a mesma verificação explícita no esquema de conclusão.

Fontes: `src/document_schema.py:376`, `src/document_schema.py:397`, `src/document_schema.py:423`, `src/document_schema.py:549`; `src/web/static/js/document-editor.js:1266`, `src/web/static/js/document-editor.js:1359`; `src/web/application.py:1696`. Especificação em `tests/test_required_fields.py:20`, `tests/test_required_fields.py:33`, `tests/test_required_fields.py:40`, `tests/test_required_fields.py:52`, `tests/test_required_fields.py:63`, `tests/test_required_fields.py:81`.

Não são atualmente obrigatórios: pedido, relatório, telefone, email, morada, NIF, contrato, número da loja, tipo de serviço ou sistema. Não confundir campos úteis com requisitos já implementados. A migração poderá melhorar a orientação sem tornar estes campos bloqueantes por decisão implícita.

### 3.2 Horas

- O total automático é a diferença entre fim e início. Se o fim for inferior ao início, conta-se a passagem da meia-noite. `23:30`–`01:00` resulta em `1 h 30 min`. Início igual ao fim produz `0 min`; não representa 24 horas.
- A duração é exibida como `H h MM min`, `H h` ou `M min`; não é uma hora do relógio.
- O utilizador pode corrigir o total efetivo. `total_hours_overridden=true` impede que alterações subsequentes ao início/fim substituam esse total. Mostrar o valor calculado e permitir «repor cálculo». Apagar o total manual volta ao modo automático na UI.
- Valores numéricos simples representam horas decimais: `1,5` → `1 h 30 min`. São também aceites formas como `01:30`, `1 h 30 min` e `90 min`, sujeitas ao parser. Total negativo não deve ser aceite.
- O esquema não exige total positivo nem igual ao intervalo; o total manual permite descontar pausas ou corrigir o tempo efetivo. Não existe no modelo campo autónomo de pausa, data final ou limite de duração do turno.
- Guardar a duração internamente em minutos inteiros é uma **proposta de migração**, não o formato atual, que usa texto.

Fontes: `src/document_schema.py:494`, `src/document_schema.py:600`, `src/document_schema.py:625`, `src/document_schema.py:637`, `src/document_schema.py:665`, `src/document_schema.py:679`; `src/web/static/js/document-editor.js:1041`, `src/web/static/js/document-editor.js:1119`. Testes: `tests/test_document_schema.py:235`, `tests/test_document_schema.py:266`.

### 3.3 Materiais

- Usar referência, descrição e quantidade por linha. Não existem neste esquema preço, IVA, desconto ou total financeiro por material.
- Linhas vazias são eliminadas pelo normalizador. Se `materials_used=false`, as linhas são substituídas por uma linha vazia. Documentos antigos sem esse booleano derivam a utilização da existência de linhas preenchidas.
- O limite de 12 aplica-se antes de filtrar linhas vazias: entradas além das primeiras 12 não sobrevivem à normalização. Na nova UI, impedir acrescentar além do limite e mostrar erro em importações excedentes, evitando perda silenciosa.
- A quantidade normaliza vírgula decimal e zeros finais, mas não possui validação de obrigatoriedade, sinal positivo ou tipo estritamente numérico. Texto não numérico pode permanecer. Regras mais estritas são uma proposta, a decidir.

Fontes: `src/document_schema.py:215`, `src/document_schema.py:474`, `src/document_schema.py:786`; teste `tests/test_document_schema.py:218`.

## 4. Compatibilidade com o modelo LINK no backend incluído

O mapa `src/field_map.py` descreve a sheet LINK: categorias na linha 1, rótulos na linha 2, valores na linha 3. É o contrato usado pelos serviços Excel da aplicação incluída. Campos de faturação e fórmulas administrativas não pertencem ao núcleo FS/checklist solicitado.

- `Folha nº` e `Cliente nome` são obrigatórios no mapa LINK; o número da folha é somente leitura (`src/field_map.py:73`, `src/field_map.py:113`). O esquema da FS valida nome do cliente e obtém o número pela identidade do documento, razão pela qual a mesma lista de obrigatórios não deve ser copiada sem contexto.
- `TOTAL`, `TOTAL s/ MAT/PLAF` e `N.º de obra` são somente leitura no mapa (`src/field_map.py:154`, `src/field_map.py:166`).
- Os aliases legados dos sistemas não são nomes novos. Preservar explicitamente a tabela de correspondência da secção 2.2.
- A conversão legada escreve só a data, fim e total da primeira linha técnica nos campos estruturados de tempo; lista os técnicos únicos e acrescenta todos os registos/materiais nas observações (`src/document_schema.py:319`, `src/document_schema.py:347`, `src/document_schema.py:559`). **O modelo da nova app deve preservar todas as linhas como dados estruturados**; não reduzir o documento a esse formato.
- `requested_tasks` é escrito tanto em «Avaria reportada» como em «Descrição do trabalho executado», enquanto o relatório usa «Relatório Técnico» (`src/document_schema.py:346`, `src/document_schema.py:354`). Evitar trocar trabalhos pedidos com trabalhos executados na nova interface; rever nomes e contrato antes de reutilizar o alias.
- A morada é reunida/separada eliminando repetições de código postal/localidade. O campo legado `CP` usa os dois primeiros algarismos encontrados no código postal, não a localidade (`src/document_schema.py:731`, `src/document_schema.py:781`). No novo modelo preferir rua, código postal e localidade explícitos, preservando o texto original durante eventual importação.

## 5. Checklist SADI: estrutura por local

Versão atual: `sadi-2`. Um documento pode conter vários locais; cada local tem identidade estável e independente, grupos de equipamentos, respostas comuns, fotos e duas assinaturas. Fonte: `src/maintenance_schema.py:19`, `src/maintenance_schema.py:138`.

| Campo do local | Regra |
|---|---|
| `id` | Obrigatório, 8–64 caracteres de `a-z`, `A-Z`, `0-9`, `_`, `-`; único entre locais. A UI cria UUID. |
| `version` | Deve ser `sadi-2`; `sadi-1` tem transformação de compatibilidade descrita adiante. |
| `location` | Identificação do local obrigatória. |
| `date` | Data da manutenção obrigatória e válida em `AAAA-MM-DD`. |
| `technician` | Técnico de serviço obrigatório; texto. |
| `scie` | Responsável SCIE opcional. |
| `period` | `monthly`, `quarterly`, `half_yearly`, `annual`, `other`. |
| `period_other` | Obrigatório apenas em periodicidade `other`. |
| `observations` | Observações gerais, opcionais. |
| `configuration` | Para cada grupo `conventional`, `addressable`, `repeater`: `null` por responder, `true` existe, `false` não existe. É obrigatório responder aos três. |
| `conventional[]`, `addressable[]`, `repeater[]` | Instâncias por equipamento, com identidade, campos e respostas próprios. |
| `general` | Duas respostas comuns ao sistema. |
| `peripherals`, `trials` | Oito verificações de periféricos e sete ensaios, uma vez por local, sempre presentes. |
| `peripheral_observations` | Observações dos periféricos/ensaios, opcionais. |
| `coverage_percent`, `coverage_areas` | Cobertura testada; regras na secção 7. |
| `photos[]` | Fotografias opcionais do local. |
| `final_observations` | Observações finais opcionais do local. |
| `signatures` | Assinaturas reconhecidas de `technician` e `customer`. |
| `signature_drafts` | Capturas ainda não confirmadas; não contam como assinatura válida. |
| `peripheral_history` | Histórico das respostas antigas por central, preservado na transformação de versão. |
| `departure` | Campo legado conservado por compatibilidade; já não é mostrado nem obrigatório. Não reintroduzir «hora de saída» como requisito. |

Fontes: `src/maintenance_schema.py:67`, `src/maintenance_schema.py:78`, `src/maintenance_schema.py:138`, `src/maintenance_schema.py:238`, `src/maintenance_schema.py:250`, `src/maintenance_schema.py:381`; `src/web/static/js/maintenance-editor.js:41`.

Ao criar um local, a UI sugere local/loja da FS com «Local N» e copia data/nome do primeiro registo técnico. São valores iniciais editáveis; não há regra de sincronização permanente com a FS (`src/web/static/js/maintenance-editor.js:41`).

### 5.1 Equipamentos

| Grupo | Campos obrigatórios de cada instância ativa |
|---|---|
| Central convencional | Marca, modelo, local da central, número de zonas total, zonas em uso, detetores, botoneiras, sirenes. |
| Central endereçável | Marca, modelo, local da central, número de loops da central, loops em uso. |
| Repetidor | Marca, modelo, local do repetidor. |

Todos os equipamentos também têm `id`, respostas `checks` e observações opcionais. IDs devem ser válidos e únicos entre equipamentos ativos do mesmo local. Quantidades de zonas/loops/dispositivos são inteiros não negativos; zero é aceite. Zonas/loops em uso não podem superar o total. Se um grupo existe, requer pelo menos uma instância; se não existe, os dados ficam guardados no rascunho mas não são exigidos nem apresentados no documento ativo. Fonte: `src/maintenance_schema.py:68`, `src/maintenance_schema.py:285`, `src/maintenance_schema.py:328`; testes `tests/test_maintenance.py:113`, `tests/test_maintenance.py:271`.

A quantidade de locais/equipamentos na UI deve ser inteira e superior a zero. Acima de 100 a interface pede confirmação, mas 100 não é um limite rígido de domínio. Reduzir a quantidade abre uma seleção explícita dos elementos a remover; o número escolhido tem de coincidir com a redução. Não apagar arbitrariamente os últimos elementos. Fonte: `src/web/static/js/maintenance-editor.js:162`.

## 6. Catálogo integral de perguntas SADI

Cada resposta é `{answer, justification}`. `answer` deve ser `OK` (cumpre função), `NC` (não conforme) ou `NA` (não aplicável). Toda NC exige justificação própria, não apenas uma observação geral. NC justificada não impede completar a checklist. NA e OK não exigem justificação. Não inferir resposta a partir de texto ou da ausência de equipamento. Fontes: `src/maintenance_schema.py:185`, `src/maintenance_schema.py:276`; `tests/test_maintenance.py:94`.

### 6.1 Sistema — duas verificações

| ID | Pergunta |
|---|---|
| B22 | Verificação de que não existiram alterações ao projeto e/ou Medidas de Autoproteção. |
| B23 | Verificação dos eventos registados nos registos das Medidas de Autoproteção. |

Fonte: `src/maintenance_schema.py:21`.

### 6.2 Centrais convencionais — seis por central

| ID | Pergunta |
|---|---|
| B44 | Verificar funcionamento geral incluindo teclado e chaves. |
| B45 | Verificar funcionalidade e nível de luminosidade dos leds de falha e/ou alarme. |
| B46 | Verificar estado da alimentação (230 V / 50 Hz). |
| B47 | Medição da carga e validade das baterias, verificação da tensão de entrada/saída, limpeza e reaperto de bornes. |
| B48 | Confirmação de ligação entre Central Principal e PC de operacionalização quando exista. |
| B49 | Verificação da acessibilidade e existência de sinalética das centrais. |

Fonte: `src/maintenance_schema.py:25`.

### 6.3 Centrais endereçáveis — sete por central

| ID | Pergunta |
|---|---|
| B68 | Funcionamento geral incluindo teclado e chaves. |
| B69 | Funcionalidade e luminosidade dos leds de falha e/ou alarme. |
| B70 | Estado da alimentação (230 V / 50 Hz). |
| B71 | Carga/validade das baterias, tensão de entrada/saída, limpeza/reaperto de bornes. |
| B72 | Confirmação de ligação entre centrais, quando em rede, e repetidores. |
| B73 | Ligação entre Central Principal e PC de operacionalização quando exista. |
| B74 | Acessibilidade e sinalética das centrais. |

B68–B71 e B73–B74 reutilizam literalmente os textos equivalentes das convencionais no código. Fonte: `src/maintenance_schema.py:33`.

### 6.4 Repetidores — quatro por repetidor

| ID | Pergunta |
|---|---|
| B90 | Funcionamento geral incluindo teclado e chaves. |
| B91 | Funcionalidade e luminosidade dos leds de falha e/ou alarme. |
| B92 | Carga/validade das baterias, tensão de entrada/saída, limpeza/reaperto de bornes. |
| B93 | Acessibilidade e sinalética dos repetidores. |

Fonte: `src/maintenance_schema.py:39`.

### 6.5 Periféricos — oito comuns ao local

| ID | Pergunta |
|---|---|
| B107 | Verificação da alimentação de fontes auxiliares da instalação. |
| B108 | Verificação da carga e validade das baterias e reaperto de bornes das fontes auxiliares. |
| B109 | Verificação do estado geral de conservação dos detetores. |
| B110 | Verificação do estado geral de conservação, acessibilidade e sinalização dos botões de alarme. |
| B111 | Verificação do estado geral de conservação das sirenes. |
| B112 | Verificação do estado geral das cablagens. |
| B113 | Inspeção visual de mudanças estruturais ou ocupacionais que afetem requisitos de localização de botões manuais, detetores e sirenes. |
| B115 | Confirmar espaço desimpedido de pelo menos 0,5 m em todas as direções abaixo de cada detetor e botões manuais desobstruídos e conspícuos. |

Fonte: `src/maintenance_schema.py:44`. Os textos longos acima estão abreviados apenas para leitura; a definição original contém o texto integral e deve ser a fonte do catálogo apresentado.

### 6.6 Ensaios — sete comuns ao local

| ID | Pergunta |
|---|---|
| B121 | Ensaios e testes às funções gerais das centrais. |
| B122 | Ensaio e teste de todos os detetores. |
| B123 | Ensaio e teste de todos os botões. |
| B124 | Ensaio e teste de todas as sirenes. |
| B125 | Ensaio funcional dos comandos. |
| B126 | Ensaio da comunicação de alarme ao corpo de bombeiros ou central recetora de alarmes. |
| B127 | Execução de simulação de um alarme por zona e análise das ativações (*). |

Fonte: `src/maintenance_schema.py:54`. Com uma unidade de cada tipo existem 34 respostas (2 + 6 + 7 + 4 + 8 + 7); cada unidade adicional repete apenas as perguntas do seu grupo. Sem centrais/repetidores continuam obrigatórias as 17 perguntas comuns.

## 7. Periodicidade, cobertura e avisos de operação

- Periodicidades: mensal, trimestral, semestral, anual e outra. «Outra» exige descrição.
- Mensal/trimestral/semestral exigem percentagem **ou** áreas testadas. A regra é uma alternativa, não obriga ambos.
- Percentagem, quando preenchida, deve estar entre 0 e 100 inclusive; aceita vírgula decimal. `0` satisfaz atualmente a presença. Não existe soma de cobertura entre visitas ou verificação automática de que o ano atingiu 100%.
- Periféricos e ensaios são comuns ao local e continuam obrigatórios mesmo quando todos os grupos de centrais/repetidores estão marcados «Não». Usar NA onde for adequado é uma decisão de quem preenche, sem auto-resposta.
- O objetivo anual de 100% de dispositivos e zonas aparece num aviso textual, não num controlo de histórico anual implementado.

Fontes: `src/maintenance_schema.py:63`, `src/maintenance_schema.py:271`, `src/maintenance_schema.py:306`; testes `tests/test_maintenance.py:103`, `tests/js/maintenance-model.test.cjs:24`.

Preservar os avisos originais no formulário. O relatório impresso conserva resultados, cobertura e legenda; não apresenta estes avisos operacionais:

1. **Antes dos ensaios:** verificar que os ocupantes foram avisados; colocar sistema em modo teste e/ou verificar se comandos podem ser ativados.
2. **Ensaio B127:** alguns módulos de dispositivos de proteção poderão ter de ser desativados.
3. **Depois dos ensaios:** repor todos os sistemas em situação normal e retirar modo teste/service.
4. **Cobertura parcial:** indicar percentagem ou, preferencialmente, áreas testadas para permitir cobertura anual completa.

São instruções funcionais extraídas do projeto, não certificação técnica ou avaliação da conformidade normativa atual. A app não verifica fisicamente que as ações foram realizadas.

## 8. Fotografias e assinaturas por local

### 8.1 Fotografias SADI

- Opcionais, máximo 10 por local. Cada foto possui ID único dentro do local, nome, legenda opcional, imagem e eventual erro de validação.
- A UI aceita JPG, PNG e WebP de origem, até 10.000.000 bytes por ficheiro, reduz o maior lado a 1600 px e converte/comprime para JPEG. Não confundir o formato de origem WebP com o formato guardado.
- A representação normalizada aceita apenas dados embutidos JPEG/PNG válidos, no máximo 1.000.000 bytes descodificados e dimensão de 1 a 1600 px em cada eixo. URLs externas e imagens malformadas não são válidas.
- Imagem inválida é mantida como erro visível sem conteúdo renderizável; bloqueia conclusão até remoção/correção. Não deve desaparecer silenciosamente.
- Adicionar/remover foto ou alterar legenda modifica o conteúdo do local e invalida as suas assinaturas. Esperar que as fotos terminem de preparar antes de assinar/finalizar.
- As fotografias gerais da FS seguem outro fluxo e outros limites na implementação original (10 MiB por ficheiro e 50 MiB por conjunto na UI); não aplicar os limites SADI indiscriminadamente (`src/web/static/js/document-editor.js:81`).

Fontes: `src/maintenance_schema.py:82`, `src/maintenance_schema.py:86`, `src/maintenance_schema.py:113`, `src/maintenance_schema.py:250`; `src/web/static/js/maintenance-editor.js:71`, `src/web/static/js/maintenance-editor.js:98`, `src/web/static/js/maintenance-editor.js:344`. Testes: `tests/test_maintenance.py:210`, `tests/test_maintenance.py:219`.

### 8.2 Assinaturas SADI

- Cada local requer assinatura do técnico e do cliente para concluir. A exceção `client_not_present` da FS **não dispensa** a assinatura da checklist (`src/maintenance_schema.py:381`). Apresentar esta diferença na UI até existir decisão funcional explícita.
- É permitido recolher e guardar assinatura com campos por preencher ou NC ainda sem justificação. A checklist continuará incompleta e a finalização será recusada. Não reintroduzir um bloqueio de assinatura por incompletude: a interface e testes mais recentes especificam esta possibilidade (`src/web/static/js/maintenance-editor.js:133`; `tests/test_maintenance.py:470`).
- Para guardar assinatura é exigido papel válido (`technician`/`customer`), nome não vazio, data válida, ID/versão de local válidos e uma imagem PNG com traço visível. Captura vazia não é assinatura.
- A validação original aceita imagem PNG até 1.000.000 caracteres de data URL, dimensões 2–2000 px por 2–1000 px e pelo menos 12 píxeis de traço visível, segundo o limiar do código. Este é um mecanismo técnico existente, não uma garantia de identidade ou valor legal.
- A assinatura é vinculada ao conteúdo do local, nome do cliente, número da folha, aplicabilidade Manutenção+SADI, papel, nome/data/imagem do signatário. Alterar esses elementos invalida-a; alterar outro local não deve invalidar locais intactos.
- Alterar nome do cliente, número da folha ou ativação de Manutenção+SADI invalida todas as assinaturas SADI. Alterar apenas conteúdo de um local afeta esse local.
- Dados de equipamentos inativos não entram no conteúdo ativo assinado. Fotos/observações finais opcionais vazias não invalidam uma assinatura `sadi-2` antiga apenas por terem sido introduzidas no esquema.
- `signature_drafts` preserva capturas não confirmadas durante a gravação do rascunho; nunca deve ser tratado como assinatura aceite nem aparecer como assinatura válida no PDF.
- No novo frontend, representar «capturada», «por guardar», «guardada» e «invalidada» com clareza. A confirmação do conteúdo é responsabilidade do backend incluído, não de um token inventado no navegador. Tokens usados em demonstrações devem ser explicitamente fictícios.

Fontes: `src/maintenance_schema.py:164`, `src/maintenance_schema.py:320`, `src/maintenance_schema.py:348`, `src/maintenance_schema.py:374`; `src/web/static/js/maintenance-editor.js:28`, `src/web/static/js/maintenance-editor.js:153`, `src/web/static/js/maintenance-editor.js:277`. Testes: `tests/test_maintenance.py:126`, `tests/test_maintenance.py:155`, `tests/test_maintenance.py:171`, `tests/test_maintenance.py:181`, `tests/test_maintenance.py:514`.

## 9. Estados de checklist e conservação de informação

| Estado visual | Critério atual |
|---|---|
| Por preencher | Existem erros e ainda não há periodicidade, resposta geral ou escolha Sim/Não da configuração. |
| Em preenchimento | Existem erros e já existe um desses sinais de preenchimento. |
| Por assinar | Não há erros funcionais locais, mas falta token de uma das duas assinaturas na UI. |
| Completa | Sem erros locais e com ambos os tokens na UI. A confirmação final original verifica validade efetiva do conteúdo. |

Fonte: `src/web/static/js/maintenance-model.js:48`. O rótulo visual «Completa» não equivale a finalizada, arquivada, enviada ou entregue. Não tratar a mera presença de token como garantia criptográfica.

Ao desligar temporariamente um grupo de equipamento, conservar os seus registos; ao voltar a ligá-lo, recuperar conteúdo. Uma redução explícita da quantidade é diferente e remove os elementos escolhidos com confirmação. As secções de periféricos/ensaios mantêm-se independentes destas operações. Fontes: `src/maintenance_schema.py:155`, `src/maintenance_schema.py:285`; `src/web/static/js/maintenance-editor.js:162`; `tests/test_maintenance.py:271`.

### 9.1 Compatibilidade `sadi-1` → `sadi-2`

A versão anterior tinha respostas de periféricos/ensaios nas centrais. Na transformação observada:

1. Preservar histórico das centrais, inclusive inativas.
2. Copiar para a secção comum apenas respostas completas idênticas (resposta e justificação) em todas as centrais ativas.
3. Em divergências, deixar a nova resposta vazia em vez de escolher arbitrariamente.
4. Aplicar unanimidade também à percentagem e áreas; reunir observações ativas com rótulo de origem.
5. Atualizar versão para `sadi-2` e remover assinaturas anteriores.
6. Fazer transformação em memória; uma leitura não deve reescrever o original.

Fonte: `src/maintenance_schema.py:199`; testes `tests/test_maintenance.py:239`, `tests/test_maintenance.py:254`, `tests/test_maintenance.py:441`. Só implementar importação de dados reais quando houver um contrato/escopo autorizado; para desenvolvimento bastam fixtures fictícias.

## 10. Divergências e decisões necessárias antes de consolidar regras

Estas observações resultam de análise estática; não foram exploradas contra dados reais.

| Prioridade | Situação observada | Ação proposta para a nova app |
|---|---|---|
| Alta | A FS dispensa assinatura por ausência do cliente; cada checklist continua a exigir assinatura do cliente. | Explicar a diferença e decidir uma política única ou uma exceção formal específica de checklist. Não herdar dispensa silenciosamente. |
| Alta | A UI SADI pede «Primeiro e último nome», mas `make_signature` e o cliente exigem apenas nome não vazio. | Aplicar o mesmo requisito explícito de duas palavras nas duas camadas, se essa for a regra aprovada. Referências: `src/maintenance_schema.py:350`, `src/web/static/js/maintenance-editor.js:212`, `src/web/static/js/maintenance-editor.js:279`. |
| Alta | O validador JS de checklist não verifica ID/versão do local nem IDs únicos dos equipamentos; Python verifica. A UI pode aparentar completude e falhar depois. | Partilhar esquema/validação e apresentar os mesmos erros antes de finalizar. Referências: `src/web/static/js/maintenance-model.js:5`, `src/maintenance_schema.py:265`, `src/maintenance_schema.py:296`. |
| Alta | Na normalização da FS, linhas além de 12 materiais/4 técnicos são cortadas. | Prevenir excesso na UI e rejeitar importação excedente com mensagem, sem truncar dados. Referências: `src/document_schema.py:479`, `src/document_schema.py:499`. |
| Média | Parsers de duração diferem: Python ignora componentes depois dos primeiros dois no formato com `:` e aceita fragmentos `h/min` com texto sobrante; JavaScript exige formato mais estrito. Arredondamento Decimal/Python e Math.round também pode divergir em frações de minuto. | Um parser único estrito para entrada, armazenamento inteiro em minutos e casos de teste partilhados. Referências: `src/document_schema.py:637`, `src/web/static/js/document-editor.js:1041`. |
| Média | Não há limite máximo funcional de locais/equipamentos e a UI apenas confirma valores >100. | Definir limites razoáveis de carga e paginação/virtualização para uso móvel; não assumir que a confirmação evita consumo excessivo. Referência: `src/web/static/js/maintenance-editor.js:177`. |
| Média | Materiais admitem linhas incompletas, quantidades de texto ou negativas; relatório pode estar vazio. | Confirmar a regra de negócio e tornar erros/mínimos consistentes, mantendo rascunho livre. Referências: `src/document_schema.py:474`, `src/document_schema.py:786`, `src/document_schema.py:60`. |
| Média | Cobertura 0% é aceite e não há soma anual/controlo de zonas visitadas. | Mostrar aviso de 0%, permitir áreas estruturadas e propor histórico de cobertura apenas numa fase posterior. Não afirmar cumprimento anual automático. Referência: `src/maintenance_schema.py:308`. |
| Média | O estado «Por preencher» pode persistir depois de preencher apenas local, técnico, data ou fotos, porque o classificador olha apenas para periodicidade, gerais e configuração. | Calcular progresso a partir de todos os campos relevantes e separar percentagem preenchida de conformidade/assinaturas. Referência: `src/web/static/js/maintenance-model.js:48`. |
| Média | Teste JS com título “invalid photographs ... block signing” descreve bloqueio de assinatura, mas a implementação mais recente permite assinatura de checklist incompleta. O teste só exercita validação local, não o ato de assinar. | Atualizar nomenclatura e critérios de aceite; preservar o fluxo assinar rascunho → completar → recolher nova assinatura se conteúdo mudar. Referências: `tests/js/maintenance-model.test.cjs:47`, `tests/test_maintenance.py:470`. |
| Média | NIF/VAT/telefone/data do pedido não têm validação funcional equivalente a datas técnicas; email depende parcialmente da integração. | Validar formato no domínio da nova app sem depender de transporte, definindo país/contexto antes de criar bloqueios. |
| Baixa | `ACOMP.COM` só existe no mapa legado e o alias «Descrição do trabalho executado» recebe trabalhos pedidos. | Documentar compatibilidade e decidir o catálogo/nomenclatura antes de construir importação. Referências: `src/field_map.py:98`, `src/document_schema.py:354`. |

## 11. Critérios de aceite para continuar a aplicação noutra pasta

Todos os exemplos devem usar dados fictícios, transportes simulados e armazenamento exclusivo de demonstração.

1. Abrir e guardar uma FS incompleta; reabrir sem perda de valores e sem exigir campos finais.
2. Validar o cliente e todas as linhas técnicas usadas; ignorar linha totalmente vazia; limitar a quatro linhas sem perda silenciosa.
3. Calcular intervalo normal e passagem da meia-noite; aceitar ajuste manual, mantê-lo após alterar horas e repor cálculo explicitamente.
4. Rejeitar datas impossíveis, hora inválida, total malformado e signatário de uma única palavra na FS.
5. Alternar cliente ausente/presente; indicar a dispensa da FS e a política distinta da checklist.
6. Alternar Manutenção+SADI; preservar dados sem considerar ativa uma checklist de outro serviço.
7. Criar vários locais e equipamentos, desligar/religar grupos sem apagar dados, reduzir quantidade escolhendo exatamente o que será removido.
8. Mostrar e validar todas as perguntas do catálogo, incluindo NC com justificação por pergunta e NA explícito.
9. Exigir periféricos/ensaios mesmo sem centrais; validar cobertura parcial, 0%, 100%, 101% e decimal com vírgula.
10. Assinar um rascunho incompleto fictício, manter pendências e bloquear conclusão até resolução; alterar conteúdo deve invalidar a assinatura afetada.
11. Verificar isolamento das assinaturas entre locais e invalidação global ao mudar cliente/identidade/aplicabilidade.
12. Processar imagens de origem permitidas, respeitar limites, mostrar falhas, aguardar preparação antes de concluir e impedir URLs de imagem externas.
13. Transformar uma fixture `sadi-1` com respostas coincidentes e outra com conflitos, preservando histórico e exigindo novas assinaturas.
14. Distinguir rascunho, checklist completa, finalização simulada e futura confirmação externa. Nenhuma demonstração deve afirmar envio real ou entrega.

## 12. Fontes principais para manutenção destas regras

- `src/document_schema.py`: catálogo, payload, normalização, compatibilidade e validação da FS.
- `src/field_map.py`: contrato histórico LINK e campos administrativos apenas como referência.
- `src/maintenance_schema.py`: definição SADI, perguntas, estrutura, limites, transformação e validação.
- `src/web/static/js/document-validation.js` e trechos de `document-editor.js`: comportamento efetivo do formulário FS.
- `src/web/static/js/maintenance-model.js` e `maintenance-editor.js`: estados, configuração, UI e validação local SADI.
- `src/web/application.py:1668`: ponto observado de agregação das regras antes de finalizar; incluído no backend transportado em aplicacao/src/web/application.py.
- `tests/test_document_schema.py`, `tests/test_required_fields.py`, `tests/test_maintenance.py`, `tests/js/document-validation.test.cjs`, `tests/js/maintenance-model.test.cjs`: casos existentes de especificação/regressão. Rever dependências e isolamento antes de os executar noutro projeto.
