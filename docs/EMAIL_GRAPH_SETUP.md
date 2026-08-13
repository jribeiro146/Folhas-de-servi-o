# Envio das folhas por e-mail e aviso no Teams

## Fluxo de e-mail implementado

Ao finalizar uma folha com o campo **E-mail do cliente** preenchido, tanto com
armazenamento local como com `FS_STORAGE_BACKEND=graph`, a aplicação:

1. valida o endereço do campo **E-mail do cliente**;
2. move a folha de `Aplicação/Activas` para `Aplicação/Arquivadas`;
3. produz e publica o HTML final;
4. em Graph/Plesk, pede ao Microsoft Graph para converter esse HTML em PDF; apenas no modo local usa Chrome/Edge;
5. valida o PDF, publica-o no arquivo SharePoint e envia exatamente o mesmo ficheiro pelo Microsoft Graph;
6. usa `service@sensorpoint.pt` como remetente e coloca apenas o técnico autenticado em CC;
7. guarda a mensagem nos Itens Enviados de `service@sensorpoint.pt`.

O campo **E-mail do cliente** é opcional. Quando fica vazio, a folha é arquivada
normalmente, mas a aplicação não consulta as credenciais de e-mail, não cria nenhum
trabalho de envio e não publica no Teams uma notificação de folha enviada. Mesmo com
`FS_MAIL_TEST_RECIPIENT` configurado, um campo vazio continua a significar que não deve
ser enviado qualquer e-mail. Um endereço preenchido mas inválido continua a impedir a
finalização.

O pedido Graph `sendMail` usa `saveToSentItems: true`. Uma resposta `202 Accepted` confirma que o Graph aceitou o pedido de envio; não constitui confirmação de entrega final no destinatário.

A interface acompanha o trabalho persistente e só apresenta sucesso depois dessa aceitação.
Falhas temporárias são repetidas até `FS_MAIL_JOB_MAX_ATTEMPTS` (três por defeito).
Depois disso, ou perante uma falha permanente de autenticação/permissão, o trabalho fica
parado. A repetição é sempre explícita e afeta apenas o trabalho selecionado. Durante a
primeira atualização para este comportamento, trabalhos antigos de e-mail também ficam
parados para revisão, evitando envios acumulados inesperados.

O diagnóstico `GET /api/mail/test` valida o client secret e a permissão de aplicação
`Mail.Send` através da obtenção de um token. Este diagnóstico nunca cria uma mensagem nem
chama `sendMail`.

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
FS_MAIL_JOB_MAX_ATTEMPTS=3
FS_MAIL_JOB_STALE_SECONDS=900
```

No modo local, `FS_PDF_TEMP_DIR` é usado tanto para o perfil headless como para o PDF
temporário e Chrome/Edge tem de estar disponível. Em `FS_STORAGE_BACKEND=graph`, o
ficheiro HTML final já publicado é a origem da conversão Graph; o Excel não é usado
para criar o PDF e o Plesk não precisa de Chrome/Chromium.

O SharePoint, a conversão PDF, o login Microsoft e o envio de e-mail usam sempre a
mesma identidade `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID` e `GRAPH_CLIENT_SECRET`. As antigas
variáveis `GRAPH_MAIL_*` e `MICROSOFT_AUTH_*` de credenciais são ignoradas e devem ser
removidas do Plesk para não causarem confusão. Um secret temporário de testes locais
nunca deve ser copiado para produção. Depois de rodar o secret principal, atualizar
apenas `GRAPH_CLIENT_SECRET` e reiniciar a aplicação e o processador da fila.

## Referências Microsoft

- Microsoft Graph, conversão de HTML para PDF: https://learn.microsoft.com/graph/api/driveitem-get-content-format?view=graph-rest-1.0
- Microsoft Graph `sendMail`: https://learn.microsoft.com/graph/api/user-sendmail?view=graph-rest-1.0
- Adaptive Cards: https://adaptivecards.io/explorer/AdaptiveCard.html
