# Passagem ao implementador — Plesk / Docker

## Identificação e âmbito

- Repositório de destino: `jribeiro146/Folhas-de-servi-o`.
- Branch de entrega: `codex/migracao-melhorias-20260928`.
- Revisão local da implementação revista: `40ad0fe` (histórico da cópia de migração; não é um commit da branch principal remota).
- Esta entrega publica o pacote em `migracao/`, sem substituir a aplicação que já existe na raiz do repositório.
- Código a analisar: `migracao/App_Folhas_Checklists/aplicacao/`. O contexto do novo Dockerfile é esta pasta, não a raiz do repositório.
- Alterações: inventário SharePoint e concorrência, cabeçalho móvel, pendências/assinaturas SADI, impressão dos 12 materiais, paginação, PWA e preparação de Docker/worker.

## Comentário pronto a passar ao implementador

> Está disponível na branch `codex/migracao-melhorias-20260928` um pacote revisto em `migracao/App_Folhas_Checklists/`. A implementação foi validada localmente com 396 testes Python, 47 JavaScript, PDFs reais e ensaios de interface móvel. A revisão independente encontrou uma condição de concorrência, entretanto corrigida e revalidada; não ficaram achados P1/P2 abertos no âmbito revisto.
>
> Antes de instalar no Plesk, comparar este pacote com o código e a configuração efetivamente em execução. Esta é uma base de migração; o merge do PR não atualiza o código da aplicação na raiz nem instala o pacote no servidor. A SADI deste pacote continua limitada à demonstração: não substituir uma versão que já oferece SADI operacional sem reconciliar e validar essa diferença. Não ativar `FS_TEST_SYNTHETIC` ou retirar proteções para tornar a SADI operacional.
>
> Confirmar Docker/Compose, projeto, volumes, código montado, logs e UID. Construir uma imagem candidata a partir de `migracao/App_Folhas_Checklists/aplicacao/` e executar `tools/smoke_pdf.py` como utilizador da imagem, sem rede, volumes ou `.env` operacional. Preparar backup consistente e recuperação de código, imagem, Compose e configuração antes da instalação autorizada.
>
> Preservar `.env`, credenciais, filas e dados existentes. Rever apenas as diferenças necessárias: Chromium/HOME gravável, worker separado e `FS_GRAPH_QUEUE_IN_WEB=false`. Confirmar destinos SharePoint e o volume real; não copiar automaticamente exemplos nem apagar cache, rascunhos ou volumes para corrigir a listagem. Após a instalação, verificar revisão carregada, login, lista/refrescos, rascunhos, PDF, PWA e saúde da fila. Envios reais ou repetição de trabalhos pendentes exigem validação/autorização específica; os testes desta entrega usaram transportes simulados.
>
> Ler `documentacao/09_MELHORIAS_E_VALIDACAO.md`, `10_ALOJAMENTO_DOCKER.md`, `11_REVISAO_INDEPENDENTE.md` e `12_QA_INTERFACE.md` dentro do pacote. Devolver um relatório com revisão/tag/digest efetivos, configuração sem segredos, verificações executadas, pendências e caminho dos backups. Docker Linux, dispositivos físicos e integrações reais ainda não estão certificados por esta entrega.

## Pontos a reconfirmar no Plesk

As referências seguintes vêm de um relatório de 26/09/2026 e não foram verificadas no servidor durante esta entrega:

| Referência histórica | Verificação antes de instalar |
|---|---|
| hs4 / Plesk com Docker | Confirmar runtime real; não aplicar Passenger por analogia. |
| `/var/www/vhosts/service.sensorpoint.pt/app`, projeto Compose `app` | Preservar a identidade efetiva do projeto; outro nome pode criar volume vazio. |
| Volume `app_app-data` em `/app/data` | Confirmar montagem e backup consistente, incluindo SQLite/WAL. |
| `./src:/app/src` | Se existir, o código do host sobrepõe-se à imagem; reconciliar ambos. |
| Logs privados do site | Preservar a montagem e o `FS_LOG_DIR` operacional. |
| `127.0.0.1:8000` atrás do proxy | Preservar proxy, HTTPS e exposição de rede efetivos. |
| `02-Aplicação/Activas` e `02-Aplicação/Arquivadas` | Confirmar destinos sem sobrescrever o ambiente com exemplos. |

A imagem nova usa UID/GID 10001 e HOME `/home/appuser`. Rever acesso ao volume existente antes do arranque. O sandbox do Chromium permanece ativo por defeito; uma eventual opção `FS_PDF_BROWSER_NO_SANDBOX=true` deve ser avaliada no destino e não ativada automaticamente.

## Teste da imagem candidata

Em checkout isolado da branch de entrega, com Docker disponível:

```sh
cd migracao/App_Folhas_Checklists/aplicacao
docker build -t folhas-servico:migracao-20260928 .
docker run --rm --network none folhas-servico:migracao-20260928 python -B tools/smoke_pdf.py
```

Este teste não usa o Compose operacional nem monta dados de produção. Guardar o resultado e o digest da imagem. Para o procedimento de instalação/recuperação, seguir o documento 10 e a configuração verificada do servidor.

## Entrega pelo Git

- Publicar apenas a branch de entrega; não fazer force-push, merge automático, alteração de proteção ou deploy.
- Excluir `Configuracao_Privada/`, `.env` operacionais, dados, filas, logs, caches, capturas e guiões locais de QA.
- Preservar a fixture Excel sintética e `.env.example` sem segredos, que pertencem ao pacote validado.
- Manter identificação distinta entre commit local revisto, commit publicado no PR e revisão efetivamente instalada.
- Recuperação de uma instalação autorizada: repor código/imagem/Compose/configuração anteriores como conjunto. Não usar `docker compose down -v` nem restaurar dados automaticamente; pode existir trabalho posterior ao backup.

Nenhum deploy, reinício operacional, alteração de dados SharePoint ou comunicação real foi efetuado para preparar esta entrega.
