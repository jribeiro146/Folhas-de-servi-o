# Âmbito e inventário corrigidos

Data: 28/09/2026. O utilizador esclareceu que «backend» no pedido inicial significava a **parte administrativa**, não o servidor dos formulários. A entrega passou a incluir a aplicação completa.

## Incluído

- Todos os módulos Python de `src/`: Flask, configuração, modelo FS/SADI, mapa LINK, logging, serviços e worker.
- Todos os templates e recursos ativos da interface, mantendo também `field-app.js` como referência antiga não carregada pelo template atual.
- Gravação/rascunhos, Excel/LINK, estado de edição, bloqueios, recuperação, assinaturas, fotografias, arquivo, HTML/PDF, Microsoft Graph, login, email e Teams.
- A imagem corporativa do rodapé do email, exigida pelo serviço original. Não é uma assinatura manuscrita de cliente.
- Dependências, arranque local isolado, WSGI e ficheiros Docker como referência.
- Testes da aplicação com mocks; teste exclusivamente administrativo excluído. Fixture Excel sintética construída de raiz.
- Catálogos JSON, exemplos fictícios, laboratório de regras e documentação já preparada.

## Excluído

- Registo administrativo, faturação, grelhas, macros e fórmulas comerciais da administração.
- Ferramentas `*admin*`, ensaios conjuntos com administração, profiling do registo e o seu teste.
- Excels operacionais, assinaturas/fotografias de clientes, bases de dados, filas, logs, backups e caches existentes.
- `.env` operacional e segredos do pacote de código. A transferência privada autorizada continua em `Migração/Configuracao_Privada`, fora deste pacote.

## Estrutura

| Pasta | Finalidade |
|---|---|
| `aplicacao/` | Projeto executável autónomo: frontend e backend dos formulários |
| `dados/` | Catálogos/exportações declarativas e cenários fictícios |
| `laboratorio/` | Recurso auxiliar para experimentar validadores JS |
| `documentacao/` | Análise funcional, melhorias, instruções e validação |
| `ferramentas/` | Verificação de integridade e testes JavaScript |

O diretório anterior `frontend/` foi renomeado para `aplicacao/`. Não existem duas cópias concorrentes do mesmo frontend no pacote.

## Base de origem e adaptações

HEAD observado: `548e13785571e189140fb28885a804a48bd688a3`, com alterações locais preexistentes. A cópia não representa uma release limpa nem comprova o código instalado no servidor.

Adaptações limitadas ao pacote:

1. Remoção de placeholders identificáveis do login.
2. Catálogo de técnicos substituído pelos nomes de demonstração também usados nos JSON.
3. Caminhos por defeito da configuração deixam de apontar para a pasta original e para a base de dados operacional; usam uma área própria por instalação fora do OneDrive.
4. Exemplos de testes anonimizados e fixture Excel original substituída por workbook sintético equivalente para os cenários.
5. Dependência Windows `pywin32` condicionada à plataforma; dependência de desenvolvimento `pytest` acrescentada.
6. Ferramentas de verificação, regras de exclusão Git/Docker e documentação de arranque incluídas.

O algoritmo de serviços e as limitações funcionais foram preservados; esta tarefa não implementa o backlog de melhorias. Os originais não foram alterados.

`proveniencia.json` regista hashes originais, destinos e adaptações. `manifest-sha256.json` confere a entrega final. Referências `src/...:linha` nos documentos 02–04 referem-se à origem analisada; o código correspondente está agora em `aplicacao/src/`, com as adaptações aqui descritas. Documentação histórica citada não é uma dependência de execução.
