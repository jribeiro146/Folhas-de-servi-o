# Preparação explícita do runtime

Comandos locais para o profissional que instala a aplicação. Esta alteração não
executa nem autoriza instalação no servidor. O empacotamento final, migração de
dados antigos, backup/restauro e validação Linux continuam etapas separadas do plano.

Usar Python 3.13 (mínimo 3.12), o código de um artefacto preparado fora de Git por
`tools/build_release.py`, e uma pasta persistente **absoluta num disco local do
mesmo servidor**. Não usar OneDrive, NFS/SMB, raiz do disco ou pastas em `src`.
O CLI recusa caminho relativo, raiz, UNC (também após resolução) e dados em `src`; não deteta todos os
sistemas de ficheiros remotos/sincronizados. Confirmar isso na instalação.

## Instalação vazia

```sh
python -B tools/prepare_runtime.py init --data-dir /srv/folhas/data
python -B tools/prepare_runtime.py inspect --data-dir /srv/folhas/data
```

`init` exige manifesto válido e pasta nova/vazia, ou contrato já preparado para a
mesma versão. Não criar previamente logs/cache/subpastas na instalação nova.
Não aceita dados antigos como vazios nem os migra/apaga. As bases operacionais
ausentes são criadas pelos factories já protegidos quando a aplicação arranca.
Uma repetição não aumenta a época. O CLI não importa configuração, aplicação ou
worker e não usa credenciais/transportes.

## Mudança entre versões compatíveis

A mensagem durável e a retenção de resultados incertos Teams exigem fila de
formato 3 (editing continua 1). `activate` não converte filas de formato 1 ou 2:
recusa-as. Esta transição exige a ferramenta de
migração offline ainda prevista no plano; não mudar marcadores manualmente nem
apagar a fila para contornar a recusa. Não instalar este checkpoint em produção.

1. Parar todos os escritores: web, workers, agendadores e ferramentas antigas.
   A confirmação `--writers-stopped` declara este passo; não prova que aconteceu.
2. Consultar `inspect` e registar o `runtime.source_sha256` atual.
3. A partir do novo artefacto, executar:

```sh
python -B tools/prepare_runtime.py activate --data-dir /srv/folhas/data --expected-source SHA256_ATUAL --writers-stopped
```

A exclusão SQLite abrange validação dos formatos das bases de edição/fila e
troca do SHA. Uma operação admitida em curso recusa a ativação; não é interrompida.
Formatos incompatíveis ou SHA anterior diferente recusam a troca. Qualquer
validação falhada mantém SHA/época anteriores. O contrato só muda a época se o
SHA mudar. Após êxito, processos antigos recusam novas operações.

4. Iniciar web e worker com o mesmo artefacto e pasta persistente. Manter o código
   imutável; não substituir ficheiros por cima de processos ainda ativos.

O bloqueio não abrange versões antigas sem contrato. A primeira passagem desses
dados exige migração exclusiva e backup próprios, ainda por implementar. Uma
troca de código não é migração nem restauro e não desfaz efeitos externos.

## Resultados e diagnóstico

Sucesso devolve JSON em stdout e código 0; erro controlado devolve JSON em stderr
e código 2. Argumentos inválidos usam o diagnóstico normal de argparse. Depois
de perda de ligação/resultado, consultar `inspect` antes de repetir: o SHA esperado
impede uma nova troca baseada numa versão anterior que já deixou de estar ativa.

`inspect` mostra apenas identidade de código/contrato e compatibilidade declarada;
pode ler outra release ou formatos futuros se a estrutura do contrato for válida.
Não valida os stores, documentos, configuração, acesso Graph ou capacidade de
envio. **Código 0 de inspect não significa aplicação pronta.** Nunca editar a
base de controlo manualmente para contornar uma recusa.

Testes sintéticos executam o CLI num artefacto mínimo copiado, sem app/config,
com manifesto válido/inválido, pastas antigas, ativação concorrente, validação
dentro da exclusão, SHA esperado desatualizado e stores incompatíveis. Não são
uma validação do pacote final nem do servidor.

Resposta à primeira revisão: a resolução de uma ligação local para UNC era
aceite. Reproduzido com `Path.resolve` simulado, sem rede; agora verifica também
o destino resolvido. Teste adicional cobre stores marcados existentes, contrato
de outra release, SHA esperado malformado e store ocupado. `BEGIN IMMEDIATE`
permite leitura em modo DELETE; o ensaio de recusa usa `BEGIN EXCLUSIVE`.

Resposta à segunda revisão: falhas SQLite na criação inicial não eram convertidas
em erros do contrato. Agora BUSY/LOCKED devolvem `state_contract_busy`; os outros
erros SQLite devolvem `state_contract_invalid`, sem traceback no CLI. Ensaios
injetam falha de disco/ocupação e confirmam ausência de base parcial/temporário.
O teste de falha de I/O no preflight confirma rollback e libertação da exclusão.
