# Confirmação local de publicações e recuperação

A publicação própria mantém a revisão de edição e o autosave. O worker guarda
primeiro o resultado remoto e as provas dos metadados na fila. Antes de copiar
os metadados para o rascunho, guarda um journal na base de edição. Após uma
interrupção, repete apenas a confirmação local. Não exclui .graph.json do
fingerprint nem recalcula globalmente versões ou recibos antigos.

Uma diferença sem prova de publicação própria preserva o autosave e exige uma
escolha do próprio técnico. O painel permite continuar o rascunho recuperável
ou a versão atual da folha. A escolha é autenticada, usa sessão/revisão e um
token deste conflito. Novas alterações invalidam escolhas anteriores. A cópia
substituída fica preservada no estado para diagnóstico.

Antes da atualização, parar web/worker fora de expediente e efetuar o backup
consistente já previsto. Preservar bases de edição/fila, snapshots, rascunhos,
fotografias, recibos, dados SADI e configuração privada. Não repetir o reset
histórico de outubro.

O diagnóstico opcional sobre uma cópia consistente é:
`python -B tools/inspect_publication_recovery.py --database-copy CAMINHO_ABSOLUTO_DA_COPIA --bundles-copy PASTA_ABSOLUTA_DOS_RASCUNHOS_COPIADOS`.
Conta estados que requerem escolha sem expor nomes ou conteúdo nem alterar
estados. Não consegue reconstruir um autosave já perdido antes desta correção;
nesse caso verificar a recuperação do navegador e o backup.

Se a publicação remota estiver confirmada mas a pasta local tiver desaparecido,
o trabalho preserva o snapshot e a confirmação remota. A confirmação local fica
pendente de recuperação da pasta (refresh ou recuperação controlada da cópia).
Uma repetição nessa fase não envia novamente a folha ao SharePoint. Não apagar
o snapshot nem marcar manualmente o trabalho como concluído.

Os novos campos são aditivos no estado v2; os recibos e hashes existentes não
mudam. Só a nova versão reconhece publication_journal e source_conflict.
Não arrancar a versão anterior sobre dados já utilizados pela nova versão:
um rollback exige o backup consistente anterior, com web/worker parados e
reconciliação dos efeitos externos entretanto aceites. Restaurar ficheiros
locais não desfaz publicações, emails ou notificações já aceites.

Não há novas identidades, permissões Microsoft ou configurações obrigatórias.
Esta nota descreve o mecanismo; os resultados de testes e a aprovação da revisão
ficam nos registos identificados da entrega.

