# Contrato das consultas do Grafana

| Objeto | Conteúdo | Escrita pelo Grafana |
|---|---|---|
| `reporting.inventory` | API, CSV e cadastro antigo em formato comum | Não |
| `reporting.invoices` | Faturas coletadas da API, sem rateio | Não |
| `reporting.balances` | Snapshot de saldo por conta e moeda | Não |
| `reporting.integrations` | Último sucesso e contagens por integração | Não |
| `reporting.connectivity` | Última amostra, válida por cinco minutos | Não |
| `reporting.telemetry_samples` | Contrato para futura ingestão de métricas | Não |

O papel `fleet_grafana` recebe SELECT apenas em `reporting`. Não recebe SELECT em `users`, acesso às credenciais de API ou permissão DDL. A aplicação utiliza outro papel, sem privilégios de criação de esquema.

O bootstrap só deve ser executado pelo serviço `init`, com o dono do banco. A senha do papel Grafana e as credenciais do próprio Grafana são diferentes e vêm do `.env` local.

## Telemetria futura

Um coletor futuro deverá identificar `company_id`, `source` e `terminal_id` do inventário e inserir `collected_at` em UTC. As medidas são `online` booleano, `latency_ms`, `loss_percent` de 0 a 100, `download_bps` e `upload_bps`. O inventário e o coletor precisam concordar na identidade da antena.

A tabela fica vazia na demonstração. Não há inferência de online/offline a partir de contratos, faturas ou sucesso da autenticação da API.

## Acesso por empresa

O datasource administrativo da demonstração possui leitura das duas empresas fictícias. Para cenários com usuários de empresas diferentes, projete visões e credenciais separadas ou outro mecanismo de isolamento no banco e organize fontes e permissões no Grafana. Selecionar uma empresa em uma variável não impede um usuário com acesso ao datasource de consultar outras linhas.
