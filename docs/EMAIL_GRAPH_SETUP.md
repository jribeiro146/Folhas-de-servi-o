# Envio das folhas por e-mail e aviso no Teams

## Fluxo de e-mail implementado

Ao selecionar `Guardar e enviar` com `FS_STORAGE_BACKEND=local`, a aplicação:

1. valida o endereço do campo **E-mail do cliente**;
2. move a folha de `Aplicação/Activas` para `Aplicação/Arquivadas`;
3. produz o HTML final e imprime-o para PDF com Chrome ou Edge local;
4. envia exclusivamente o PDF pelo Microsoft Graph;
5. usa `service@sensorpoint.pt` como remetente e coloca apenas o técnico autenticado em CC;
6. guarda a mensagem nos Itens Enviados de `service@sensorpoint.pt`.

O pedido Graph `sendMail` usa `saveToSentItems: true`. Uma resposta `202 Accepted` confirma que o Graph aceitou o pedido de envio; não constitui confirmação de entrega final no destinatário.

## Aviso Teams implementado

O aviso Teams é independente de SharePoint e usa diretamente um URL HTTPS criado por um Teams Workflow. A aplicação envia uma Adaptive Card apenas depois de o Microsoft Graph aceitar o e-mail.

A mensagem contém:

```text
Folha de serviço enviada
A folha nº 2026_4572 foi enviada ao cliente.
```

O canal, chat ou destinatário é definido no próprio Workflow. O URL recebido é um segredo e não deve ser guardado no repositório.

### Criar e configurar o Workflow

1. criar no Teams Workflows um fluxo capaz de receber um pedido webhook;
2. escolher no Workflow o canal ou chat onde a mensagem deve ser publicada;
3. copiar o URL HTTPS gerado;
4. guardar esse URL apenas na variável de ambiente `FS_TEAMS_WEBHOOK_URL`;
5. ativar `FS_TEAMS_NOTIFICATIONS_ENABLED=true` e reiniciar a aplicação.

Quando o aviso está ativo, a aplicação valida o webhook antes de finalizar a folha. Depois do e-mail ser aceite, cria um trabalho separado e persistente `notify_teams`. Uma falha do Teams não repete o e-mail; apenas o aviso pendente é repetido pela fila.

### Testar o Workflow

1. usar primeiro um destinatário controlado em `FS_MAIL_TEST_RECIPIENT`;
2. finalizar uma folha de teste;
3. confirmar o PDF e o e-mail;
4. confirmar a Adaptive Card no destino escolhido no Workflow;
5. consultar `/api/graph/status` se o aviso permanecer pendente ou falhar.

## Configuração da aplicação

```env
FS_TEAMS_NOTIFICATIONS_ENABLED=true
FS_TEAMS_WEBHOOK_URL=<url-https-do-workflow>
```

O envio de e-mail mantém:

```env
FS_MAIL_ENABLED=true
FS_MAIL_SENDER=service@sensorpoint.pt
FS_MAIL_TEST_RECIPIENT=
```

## Referências Microsoft

- Microsoft Graph `sendMail`: https://learn.microsoft.com/graph/api/user-sendmail?view=graph-rest-1.0
- Adaptive Cards: https://adaptivecards.io/explorer/AdaptiveCard.html
