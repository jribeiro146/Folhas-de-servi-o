# Backup e restauro offline — M07

Esta ferramenta preserva o estado conjunto de Folhas de Serviço e SADI. Não
contacta Microsoft Graph, email ou Teams e não importa a aplicação/configuração.
Uma recuperação **não desfaz efeitos externos**. O restauro começa bloqueado e
só uma decisão explícita e registada do responsável permite reabrir a aplicação.

## Preparação do profissional

1. Usar o artefacto com manifesto válido correspondente ao contrato ativo. Inventariar
   `FS_APP_DATA_DIR` e **todas** as raízes externas de cache/Excel/anexos. Raízes
   incluídas dentro de outra já estão cobertas; não as repetir. Logs internos
   também são incluídos. Configuração privada e chave ficam em canal separado.
2. Parar web, worker, agendamentos, versões antigas e editores de ficheiros/bases.
   As flags confirmam essa paragem; não a executam. Os bloqueios adicionais recusam
   escritores modernos e SQLite ocupados, mas não controlam escritores externos.
3. Reservar espaço para a cópia e para os dados recuperados. Usar disco local,
   diretórios privados fora do artefacto e das raízes de dados. Não usar OneDrive,
   SMB/NFS, symlinks, junctions ou raiz do disco. Não executar sobre dados reais
   durante o ensaio. A ferramenta não apaga nem desloca diretórios existentes.
4. Preservar `FS_SECRET_KEY` pelo canal privado do instalador, juntamente com a
   configuração necessária. Para o CLI, um ficheiro UTF-8 privado contém **apenas
   o valor da chave**, sem `FS_SECRET_KEY=`, aspas ou BOM. Espaços periféricos são
   removidos como no arranque da app. Nunca indicar o próprio `.env`, nem copiar
   a chave para o backup, código, Git, logs, linha de comando ou revisão externa.
   A ferramenta recusa um ficheiro da chave dentro das raízes/backup/workspace.

Exemplos Linux a adaptar pelo instalador (caminhos ilustrativos):

```sh
python -B tools/backup_runtime.py backup \
  --data-dir /srv/folhas/data --include-root /srv/folhas/Excel \
  --backup /srv/backups/folhas-20261009 \
  --key-file /srv/private/folhas-key.txt --writers-stopped --roots-complete

python -B tools/backup_runtime.py verify \
  --backup /srv/backups/folhas-20261009 --key-file /srv/private/folhas-key.txt
```

Resultado esperado: JSON `state=verified`, identificador, data e número de
ficheiros, exit 0. Falhas dão exit 2 e `backup_recovery_refused`. Não há mensagens
com o valor da chave. Cada tentativa de backup exige um diretório novo; uma
tentativa incompleta é preservada para diagnóstico, não sobrescrita.

## O que é copiado e verificado

SQLite é copiado pela API de backup, incluindo páginas **confirmadas no WAL**.
O contrato é serializado pela ligação que detém a exclusão; abrir e fechar um
descritor cru dessa base poderia libertar bloqueios POSIX. As cópias ficam em
modo DELETE, independentes dos WAL originais. A ferramenta não apaga journals.
Os ficheiros normais são conferidos antes/depois e o manifesto guarda SHA-256,
caminhos, modos, proprietário e timestamps. Diretórios vazios são preservados.

Inclui estados/reservas/autosaves/auditoria, fila/recibos e MIME, bundles Excel,
fotografias, assinaturas, metadados Graph e dados/PDF privados SADI nas raízes
inventariadas. Não reconstitui nem volta a assinar documentos. Um HMAC específico
da cópia verifica a chave apresentada no restauro, sem armazenar a própria chave.
O manifesto completo (caminhos, hashes, UID/GID/modos e timestamps) também é
autenticado por HMAC-SHA256 com essa chave. Adulterá-lo e recalcular apenas os
checksums não permite recuperar a cópia. Isto não prova que o operador forneceu
a chave correta na criação inicial: deve
ser o valor ativo da aplicação, conservado pelo profissional. Quem possui a chave
pode autenticar outro manifesto; manter chave e cópia em canais privados separados.

Só são aceites os formatos atuais e contrato ativo. A cópia privada de originais
do migrador M06 é outro tipo de evidência; não é automaticamente um backup M07.

## Recuperar, sem repetir efeitos posteriores

1. Manter todos os processos parados. Conservar o estado acidentado noutro local
   privado, por procedimento do instalador. Confirmar backup/chave e raízes vazias.
   O restauro usa **os mesmos caminhos absolutos**, preservando identidades locais
   e referências persistidas na fila. Migração para outros caminhos/host, alteração
   de UID/GID ou recuperação parcial não são suportadas por este comando.
2. Executar com um workspace novo, privado e fora das raízes/cópia/artefacto:

```sh
python -B tools/backup_runtime.py restore \
  --backup /srv/backups/folhas-20261009 --workspace /srv/recovery/folhas-20261009 \
  --key-file /srv/private/folhas-key.txt --writers-stopped
```

3. Esperar `state=held`, `reconciliation_required=true`. **Web e worker devem
   recusar operações**. O contrato contém `restore_hold=1`; as versões anteriores
   que já respeitam o contrato também recusam esse formato, incluindo `activate`.
   Código legado sem contrato tem de continuar parado. Não editar diretamente o
   contrato, não usar `init`/`activate`/M06 para contornar este bloqueio.
4. Se o restauro for interrompido, repetir **o mesmo comando/workspace/artefacto**.
   Só se completam ficheiros ausentes de uma recuperação ainda em curso. Conteúdo
   existente diferente é preservado e recusado. Depois de `held`, alterações,
   ficheiros extra ou em falta também recusam a libertação. Não apagar o workspace:
   contém o plano e os candidatos que permitem a retoma. Se a interrupção foi
   durante `release`, repetir `release`. Repetir `restore` nessa fase devolve
   `state=releasing`, `next_command=release`, sem alterar dados nem auditoria.

   O plano é autenticado por HMAC com domínio distinto do manifesto, antes de
   usar caminhos, estado, modos ou hashes. Os candidatos de contrato/fila são
   conferidos contra os hashes autenticados antes de publicar ou libertar.
   A decisão tem também HMAC próprio, incluindo quando sobreviveu a uma falha
   anterior à gravação do plano. Alterar JSON ou recalcular hashes no workspace
   não autoriza remover o bloqueio nem reativar trabalhos. Não editar esses
   ficheiros manualmente; conservar a evidência para diagnóstico se forem recusados.
5. O responsável compara os efeitos externos entre a data da cópia e a paragem:
   emails aceites, Teams, movimentos/cancelamentos Graph e documentos finalizados.
   Abrange **documentos e trabalhos criados depois da cópia e ausentes nela**.
   Guardar o levantamento privado e resolver divergências antes da libertação.
   Se não for possível demonstrar o estado, manter bloqueado e pedir diagnóstico;
   não tentar reenviar para descobrir o que aconteceu.
6. Só após essa confirmação, registar responsável/motivo/referência privada:

```sh
python -B tools/backup_runtime.py release \
  --workspace /srv/recovery/folhas-20261009 --key-file /srv/private/folhas-key.txt \
  --writers-stopped --external-effects-reconciled \
  --actor instalador --reason 'Reconciliação concluída; referência ao relatório privado do incidente'
```

O resultado `state=released` permite reabrir a aplicação. É uma **declaração do
operador**, não uma verificação automática da Microsoft. `reconciliation.json`
é gravado antes da ativação; o plano guarda o seu hash e retoma uma interrupção
sem substituir a decisão, mesmo se falhou a gravação do plano depois da auditoria.
Os argumentos actor/motivo de uma retoma não substituem a primeira declaração.
Guardar workspace/relatório junto da evidência privada.
Toda a fila restaurada que não estava `complete` permanece falhada, sem repetição,
com indicação de reconciliação, estado e resultado anteriores preservados. A
libertação global não liberta esses trabalhos. A resolução individual auditada
é o âmbito separado M08; não editar SQL nem apagar trabalhos para reenviar.

## Acessos, testes e limites

POSIX conserva UID/GID/modo normal e mtime dos ficheiros e diretórios. Bits especiais
setuid/setgid/sticky são recusados no backup e na aplicação de atributos; não são
necessários neste armazenamento e requerem avaliação específica do instalador.
Executar com os
privilégios necessários; uma falha de `chown` interrompe sem publicar o ficheiro.
Em Windows herda-se a ACL da pasta privada de destino; ACLs específicas, atributos
estendidos e políticas de retenção/encriptação são responsabilidade do instalador.
Conteúdo privado fica no backup: garantir acesso restrito e proteção em repouso.

Os testes `tests/test_runtime_backup.py` usam só dados fictícios e rede bloqueada:
WAL confirmado, MIME, bundles, assinatura SADI ainda válida, chave/checksum
errados, destino ocupado, concorrência, interrupções do restauro e da libertação,
recusa de dados mudados/incompletos e fila sem replay. `gc.collect` na fixture
simula a paragem dos processos antigos e fecha ligações SQLite da fila que ainda
aguardam recolha; não é um mecanismo usado pelo comando operacional.

O ensaio real de UID/GID em Linux/Docker aguarda o ambiente ou o instalador (M14).
As interrupções testadas são de processo; durabilidade face a perda física de
energia/disco e restauro off-site exigem o ensaio/procedimento da infraestrutura.
Não foi executado backup, restauro, envio ou desbloqueio em produção.
