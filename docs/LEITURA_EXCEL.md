# Leitura interna das Folhas de Serviço — 08/10/2026

**Âmbito confirmado pelo utilizador em 08/10:** apenas o leitor dos ficheiros
XLSX das Folhas de Serviço usado na aplicação web. O Excel de backoffice é um
desenvolvimento separado, excluído deste trabalho, tal como faturação e fórmulas
comerciais. Esta alteração não implementa nem modifica esse backoffice.

Transportada apenas a melhoria de leitura da referência do pacote para a base
de produção, preservando o restante serviço. `read_link` esgota um iterador
limitado às linhas de cabeçalho/dados, e resolve as colunas em memória. A escrita
continua a usar workbook normal, com uma passagem pelos cabeçalhos. Mantêm-se
aliases, duplicados, fallback por coluna em ficheiros sem cabeçalhos reconhecidos,
`data_only` na leitura, fórmulas na escrita e substituição atómica do Excel.

Os workbooks fecham em `finally`; o iterador de leitura é esgotado antes do fecho.
Remove-se `gc.collect()` obrigatório por operação. Fixa-se openpyxl **3.1.5**, a
versão usada nos testes, nos requisitos de servidor e desktop. A restrição antiga
permitia 3.1.0; o changelog oficial documenta correções posteriores de libertação
de ficheiros em read-only e Windows/Python >=3.11.8 na versão 3.1.3:
[changelog openpyxl](https://openpyxl.readthedocs.io/en/stable/changes.html).

## Evidência sintética

Em `tests/test_excel_streaming.py`, os casos de 40/200 colunas abriam o XML de
LINK 49/209 vezes antes da alteração. Agora fazem no máximo **duas aberturas**:
dimensões no carregamento e leitura dos valores. Os streams estão fechados antes
de regressar; o ficheiro pode ser renomeado e apagado. Outros cenários confirmam
colunas deslocadas, dois telefones, ficheiro esparso, bytes inalterados na leitura
e fórmulas preservadas após escrita/reabertura. Os testes anteriores de Excel e
arquivo/web também passaram.

Comparação local com o serviço do commit `81ff43b8`, Windows 11/Python 3.13.1,
openpyxl 3.1.5, três leituras por tamanho e ordem alternada. Workbooks gerados de
raiz, sem rede/dados reais; mediana em segundos:

| Colunas | Antes | Depois |
|---|---:|---:|
| 50 | 0,1538 | 0,0073 |
| 200 | 1,1504 | 0,0145 |
| 800 | 12,7805 | 0,0287 |

Em todos os casos os dicionários lidos coincidiram, o workbook não mudou e a
renomeação/remoção após leitura foi possível. Evidência local:
`qa-output/consolidacao-20261007/benchmark_excel.py` e `excel-benchmark.json` na
árvore de trabalho exterior (incluem hashes e todas as medições). Os tempos não
são limites garantidos nem uma medição do servidor/Graph/PDF. A validação Linux
continua pendente; a melhoria não resolve por si só erros de sincronização.
