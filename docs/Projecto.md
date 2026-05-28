# Princípios e Guardrails do Projecto

## Objectivo

Este projecto implementa um sistema de Folhas de Serviço baseado em ficheiros Excel arquivados no SharePoint.

Cada Folha de Serviço corresponde a um ficheiro Excel criado manualmente pelo utilizador.

Cada ficheiro Excel deve conter obrigatoriamente:
- uma sheet denominada `Link`;
- uma sheet denominada `Template`.

A aplicação deve permitir listar essas folhas, abrir o formulário correspondente, ler e escrever os dados no `Link`, gerar o PDF final a partir do `Template` e arquivar o ficheiro Excel após envio.

---

## Arquitectura obrigatória

A arquitectura oficial do projecto é:

**Aplicação -> Link -> Template -> PDF -> Arquivo**

Isto significa:

1. a aplicação lê os dados da sheet `Link`;
2. a aplicação escreve os dados apenas na sheet `Link`;
3. a sheet `Template` recebe os dados exclusivamente por fórmulas ou referências internas do Excel;
4. o PDF final é gerado apenas a partir da sheet `Template`;
5. após **Guardar e enviar**, o ficheiro Excel é movido para arquivo;
6. o Excel nunca é apagado automaticamente.

---

## Princípios fundamentais

1. O utilizador cria manualmente cada ficheiro Excel.
2. A aplicação nunca cria automaticamente o Excel da Folha de Serviço.
3. O `Link` é a única interface de leitura e escrita dentro do Excel.
4. O `Template` é apenas uma folha de apresentação.
5. O `Template` nunca pode ser escrito directamente pela aplicação.
6. O PDF final é sempre gerado a partir do `Template`.
7. O SharePoint é o repositório oficial dos ficheiros Excel e dos PDFs.
8. O arquivo faz parte do fluxo oficial e não é opcional.
9. Sempre que houver conflito entre rapidez e consistência, escolher consistência.
10. Sempre que houver conflito entre solução “rápida” e arquitectura correcta, escolher arquitectura correcta.

---

## Prioridades do projecto

Ordem de prioridade:
1. consistência;
2. previsibilidade;
3. segurança operacional;
4. simplicidade;
5. facilidade de manutenção;
6. eficiência.

---

## O que é proibido

É expressamente proibido:
- escrever directamente na sheet `Template`;
- apontar para células visuais do `Template` para gravar dados;
- alterar a estrutura do Excel sem aprovação explícita;
- mudar os nomes das sheets `Link` e `Template`;
- criar múltiplas fontes de verdade para o mesmo campo;
- duplicar lógica de leitura e escrita do Excel;
- contornar o `Link` para resolver casos pontuais;
- apagar automaticamente o Excel após envio;
- usar ficheiros de produção para testes destrutivos;
- fazer refactors largos fora do âmbito da tarefa;
- criar caminhos alternativos de geração de PDF que ignorem o `Template`.

---

## Regras do ficheiro Excel

Cada ficheiro Excel da Folha de Serviço:
- é criado manualmente pelo utilizador;
- deve conter obrigatoriamente as sheets `Link` e `Template`;
- deve cumprir a estrutura aprovada pelo projecto;
- deve permitir que o `Template` beba do `Link` através de fórmulas ou referências internas.

Se faltar qualquer uma das sheets obrigatórias:
- a aplicação deve bloquear a edição normal;
- a aplicação deve mostrar erro claro;
- a aplicação não deve tentar “adivinhar” ou contornar a estrutura.

---

## Regras da sheet Link

- A sheet `Link` é a única camada oficial de dados da aplicação dentro do Excel.
- O formulário deve ser pré-preenchido exclusivamente com dados lidos do `Link`.
- Todas as alterações feitas pelo utilizador devem ser escritas exclusivamente no `Link`.
- O `Link` deve ser tratado como contrato estável.
- O `Link` deve ter estrutura documentada e previsível.
- O código não deve assumir estrutura variável do `Link`.
- Sempre que possível, o acesso aos campos do `Link` deve estar centralizado num mapa de campos.

---

## Regras da sheet Template

- A sheet `Template` é apenas uma folha de apresentação.
- O `Template` nunca pode ser escrito directamente pela aplicação.
- O `Template` deve receber os dados por fórmulas ou referências internas do Excel.
- O código nunca deve depender do layout visual do `Template`.
- O `Template` pode mudar visualmente sem afectar a aplicação, desde que continue a beber do `Link`.

---

## Regras do PDF

- O PDF final deve ser gerado apenas da sheet `Template`.
- O PDF nunca deve ser gerado a partir do `Link`.
- O PDF nunca deve ser montado por preenchimento directo do `Template`.
- Antes de gerar o PDF, o sistema deve confirmar que o `Link` foi actualizado e gravado.
- O naming do PDF deve ser consistente e previsível.

---

## Regras de SharePoint e pastas

O sistema deve distinguir claramente:
- ficheiros activos;
- ficheiros arquivados;
- ficheiros cancelados;
- PDFs finais.

Estrutura recomendada:
- `Excel/Activas`
- `Excel/Arquivadas`
- `Excel/Canceladas`
- `PDF/Final`

Após **Guardar e enviar**, a aplicação deve:
1. actualizar o `Link`;
2. garantir que o `Template` reflecte os dados;
3 os dados;
3. gerar o PDF;
4. guardar o PDF na pasta correcta;
5. mover o Excel da pasta activa para a pasta de arquivo.

É proibido:
- apagar automaticamente o Excel;
- deixar o Excel activo após envio sem regra explícita;
- misturar PDFs e Excel na mesma pasta sem necessidade.

---

## Regra final do projecto

Qualquer solução que proponha:
- escrita directa no `Template`;
- bypass do `Link`;
- geração de PDF por caminho alternativo não aprovado;
- eliminação automática do Excel após envio;

deve ser considerada violação da arquitectura do projecto.