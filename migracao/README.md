# Migração

O pacote está em **[App_Folhas_Checklists/README.md](App_Folhas_Checklists/README.md)**.

Âmbito corrigido: inclui a aplicação completa — **frontend e backend dos formulários e checklists**. Apenas a parte administrativa, Registo Admin, faturação e fórmulas comerciais ficam excluídos.

Copiar `App_Folhas_Checklists` completa para a nova pasta. O código executável encontra-se em `aplicacao/`, com arranque de teste isolado e testes próprios. Não é necessário reconstruir o backend dos formulários.

As credenciais e ligações disponíveis foram transferidas separadamente para **[Configuracao_Privada](Configuracao_Privada/README.md)**, a pedido do utilizador. Essa pasta é privada, ignorada pelo Git e não deve ser publicada, incluída no frontend ou carregada nos testes.

Foram preservados `CORRECAO_FOLHAS_ATIVAS_SHAREPOINT.md` e `NOTAS_DEPLOY_DOCKER_E_MELHORIAS_2026-09-28.md`. São referências de diagnósticos/versões anteriores, não confirmação da versão que está no pacote ou no servidor.
