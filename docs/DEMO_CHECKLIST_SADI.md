# Demo de checklists SADI

## Iniciar

Executar `python -B tools/run_test_version.py --port 5017`. O lançador cria uma pasta temporária nova em cada execução, exclui o `.env` operacional, bloqueia ligações de saída e mantém email, Teams e workers desativados. A demo está disponível em `http://127.0.0.1:5017`.

Abrir um rascunho (ou criar um a partir de uma folha fictícia), marcar **Manutenção** e **SADI** e abrir **Checklists de manutenção**. Os dados duram durante essa execução e podem ser guardados/reabertos no navegador; reiniciar o lançador inicia outro conjunto de dados fictícios.

O lançador injeta um único **Técnico de teste** para a demo, permitindo retomar o mesmo rascunho no Chrome e no navegador da aplicação. Esta identidade simulada é recusada fora do ambiente sintético local sem autenticação e sem integrações reais. O controlo de revisões mantém-se ativo para impedir que duas sessões sobrescrevam alterações uma da outra. As sessões de instâncias locais diferentes usam cookies independentes.

## Fluxo

- Indicar o número de locais e identificar cada edifício. O cliente e o número de serviço são comuns e vêm da folha de serviço.
- Em cada local, indicar a existência e a quantidade de centrais convencionais, endereçáveis e repetidores. Os tipos podem coexistir.
- A ordem das secções é fixa: **1. Sistema; 2. Centrais; 3. Repetidores; 4. Periféricos e ensaios**. A secção 4 é permanente e preenchida uma única vez por local, mesmo sem centrais ou repetidores ativos. Inclui oito verificações de estado geral, sete ensaios, observações e cobertura dos ensaios.
- Preencher identificação e verificações de cada equipamento. Cada repetidor inclui **Marca, Modelo e Local do repetidor**; a localização é obrigatória na finalização.
- Respostas NC exigem justificação individual, mas não impedem a conclusão quando justificadas. NA é uma resposta explícita e não é pré-selecionada.
- Periodicidades mensal, trimestral e semestral exigem percentagem ou áreas testadas uma vez por local, na secção 4.
- Recolher duas assinaturas por local: técnico e cliente, diretamente nos cartões do formulário com primeiro e último nome, data e espaço para desenhar. O desenho é validado e guardado automaticamente quando os dados da assinatura estão preenchidos; o botão «Guardar assinatura» permite tentar novamente. Capturas ainda incompletas são preservadas no rascunho, mas não contam como assinaturas nem entram no PDF. A assinatura do cliente é sempre obrigatória na checklist, mesmo que o cliente esteja marcado como ausente na folha de serviço. É possível assinar com campos por preencher; **Verificar checklist** é informativo e as pendências bloqueiam apenas a finalização. O responsável SCIE é uma identificação opcional. Alterar conteúdo assinado exige novas assinaturas.
- Os botões **Pré-visualizar** e **Exportar PDF** no topo abrem uma janela com separadores para a folha de serviço e para todas as checklists locais. Começa no documento que estava selecionado no formulário, permitindo consultar os restantes sem voltar atrás. Usa as respostas atuais, incluindo alterações ainda por guardar, sem avisos de rascunho sobrepostos ao relatório. **Imprimir / Guardar este PDF** exporta o documento escolhido.
- A checklist partilha o logótipo, cabeçalho, estilos de tabelas, tipografia e caixas de assinatura da folha de serviço. As tabelas identificam cliente, local, equipamento e número da folha, também nas continuações; os blocos seguem em sequência, sem páginas de separação vazias.
- As orientações ao técnico (antes dos ensaios, simulação de alarme, cobertura e final da manutenção) aparecem apenas no formulário. O relatório e a sua pré-visualização mantêm os resultados, a cobertura registada e a legenda OK/NC/NA, sem essas instruções de preenchimento.
- Guardar rascunho permite continuar mais tarde. Finalizar exige a folha e todas as checklists completas; gera um PDF da folha e um por local, independentemente do número de centrais.
- A página final disponibiliza consulta e download dos PDFs. A simulação do email ao cliente inclui apenas a folha de serviço; na demo, as checklists ficam guardadas na pasta do serviço. A simulação não envia mensagens.

## Modelo e integridade

`src/maintenance_schema.py` contém o catálogo versionado `sadi-2`, com os textos técnicos transcritos de `SADI.xlsx`, folha `b) SADI`, e a estrutura atualizada por local. Os identificadores das perguntas correspondem às células do modelo. A definição alimenta os formulários e PDFs; a validação definitiva ocorre no servidor.

Rascunhos `sadi-1` são adaptados em memória ao abrir; a gravação usa a nova versão. Respostas iguais entre todas as centrais ativas são aproveitadas na secção comum; respostas ou coberturas diferentes ficam por preencher, sem escolher uma central arbitrariamente. Os registos originais, incluindo equipamentos inativos, ficam em `peripheral_history` e podem ser consultados no formulário. A mudança de versão exige novas assinaturas. Nada é sobrescrito no ficheiro ao consultar o rascunho.

Na demo, `maintenance_checklists` é guardado no JSON associado à folha. Em produção, é guardado separadamente em `FS_APP_DATA_DIR/sadi`, sem entrar no arquivo Graph. As assinaturas PNG ficam embebidas no registo da checklist como referências `data:` com identificação, data e token HMAC do conteúdo assinado. O token inclui local, configuração, perguntas, dados comuns impressos, interveniente e imagem. Não permite reaproveitar a assinatura noutro local ou após uma alteração. É uma proteção de integridade da aplicação, não uma assinatura digital qualificada.

Desativar um tipo preserva as respostas no rascunho, mas exclui os seus blocos da validação e do PDF. Reduzir quantidades exige selecionar e confirmar os elementos removidos. Os identificadores dos restantes elementos mantêm-se estáveis.

Na demo, os PDFs e o manifesto são produzidos no staging da finalização. Em produção, o conjunto completo é produzido na pasta privada da app antes da publicação do arquivo; não é enviado ao SharePoint. Uma falha preserva o rascunho e a repetição é tratada pelo mecanismo de idempotência existente.

## Demo e produção

O lançador descrito no início continua restrito ao ambiente sintético local, sem serviços Graph, email, Teams ou filas injetados. Em produção, a checklist só fica ativa com `FS_MAINTENANCE_ENABLED=true`, autenticação Microsoft e backend Graph. Só `acarvalho@sensorpoint.pt` e `jribeiro@sensorpoint.pt` a podem ver e preencher; os PDFs SADI são servidos exclusivamente pela app a estas contas. Os textos técnicos SADI estão em português. A exportação mantém o conteúdo do Excel, com paginação própria para suportar vários equipamentos; não reproduz a grelha Excel célula a célula. Ver [notas de versão](RELEASE_NOTES_SADI_DEMO_2026-09-24.md) antes de preparar o servidor.

## Validação

Testes Python em `tests/test_maintenance.py` e `tests/test_maintenance_access.py`, e JavaScript em `tests/js/maintenance-model.test.cjs`. Incluem assinatura ligada ao conteúdo, NC, cobertura, retomada do rascunho, três PDFs, falha de renderização, repetição sem duplicação, acesso privado em produção e bloqueio de contas não autorizadas. Os testes usam dados fictícios e transportes bloqueados; nenhum envio real é necessário.

Para repetir a geração real com Chromium: `python -B tools/verify_sadi_pdf_demo.py`. Este comando configura o isolamento antes de importar a aplicação, gera o cenário de dois edifícios, produz os três PDFs e imprime a localização temporária dos resultados. Requer as dependências de desenvolvimento e Edge/Chrome instalado. No Windows, executar num terminal normal se a restrição do ambiente de execução impedir o arranque do navegador.
# Fotografias e observações finais

Cada local tem fotografias (até 10, JPG/PNG/WebP, 10 MB por ficheiro de origem) e observações finais opcionais, depois dos periféricos e antes das assinaturas. As imagens são reduzidas no navegador até 1600 px e gravadas no JSON da checklist, com identificador estável e legenda opcional. O servidor verifica formato real, dimensões e tamanho antes de permitir a finalização; imagens inválidas ficam assinaladas no rascunho e nunca são renderizadas. Fotografias e observações finais aparecem no PDF desse local e participam na invalidação das suas assinaturas. As fotografias internas da folha de serviço mantêm o comportamento existente.

As caixas de observações e justificação começam numa linha larga e crescem com o texto.
