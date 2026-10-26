# V02 — teste do acompanhamento de email

O checker `tools/check_mail_monitor_ui.py` passou a reproduzir a verificação de
sessão já usada pelo monitor. Obtém o HTML e `/api/graph/monitor-context` na mesma
sessão Flask fictícia, serve esse contexto ao JavaScript real e aguarda a
inicialização do acompanhamento antes de finalizar a folha.

O ensaio verifica o âmbito enviado no POST de finalização e no GET de consulta,
e devolve no POST o cabeçalho que o servidor real usa para identificar a sessão
do envio. Mantém quatro cenários: falha conhecida, tentativa agendada, prazo de
consulta demasiado distante e trabalho não encontrado. Cada cenário exige uma
consulta de contexto, um único POST de finalização e um único GET do trabalho;
fechar o aviso não repete o envio. Detalhes privados do fornecedor não podem
aparecer no aviso.

Não há alteração ao comportamento da aplicação, permissões, login ou serviço de
email. O checker usa HTML e módulos reais, mas substitui o coordenador de edição
e as respostas dos envios. Não comprova entrega pela Microsoft. O ensaio integrado
separado cobre os percursos reais de edição e persistência.

Executar isoladamente com Python e Playwright/Chromium disponíveis:

```text
python -B tools/check_mail_monitor_ui.py --output CAMINHO_NOVO_DE_EVIDENCIA
```

O próprio checker configura armazenamento temporário fictício antes de importar
a aplicação, desativa email/Teams/worker e bloqueia a rede externa. As quatro
capturas e o resultado JSON são a evidência visual e funcional. Não carregar
configuração operacional para este teste.
