# Starlink Fleet Manager

Sistema demonstrativo para consolidar o inventário e as despesas de múltiplas contas Starlink, com cadastro manual por CSV, controle de usuários por empresa e dashboards do Grafana sobre PostgreSQL.

**O repositório contém somente código e dados fictícios.** A instalação demonstrativa não acessa a API Starlink. Os segredos são gerados na sua máquina e ficam fora do Git.

## Recursos

- Visão consolidada por empresa, e-mail, titularidade, conta e obra/regional.
- Coletor opcional para a API oficial, com paginação validada e preservação da última coleta quando há erro.
- Cadastro CSV com prévia, validação, confirmação transacional e prevenção de duplicidades.
- Titularidade CPF/CNPJ conforme a regra de cada empresa.
- Obras e e-mails associados individualmente às antenas.
- Perfis Leitura, Leitura/Gravação e Admin, com uma ou mais empresas por usuário.
- Desativação de usuários, alteração de senha, CSRF e revogação de sessões após mudança de acesso.
- Temas claro/escuro e filtros avançados no dashboard.
- Grafana com 19 painéis e conexão PostgreSQL somente de leitura.
- Compose, exemplos de configuração e CI para validação do projeto.

## Iniciar a demonstração

Requisitos: Docker Engine com Docker Compose v2 e Python 3.10+ no host para gerar a configuração local. Os containers usam Python 3.12, PostgreSQL 17 e Grafana 13.2.4.

```bash
git clone https://github.com/EndrigoDeronzi/starlink-fleet-manager.git
cd starlink-fleet-manager
python3 scripts/setup_env.py
docker compose up -d --build web grafana
```

| Serviço | Endereço local | Usuário | Senha |
|---|---|---|---|
| Aplicação | http://127.0.0.1:8080 | `admin` | `INITIAL_ADMIN_PASSWORD` no `.env` |
| Grafana | http://127.0.0.1:3000 | `admin` | `GRAFANA_ADMIN_PASSWORD` no `.env` |

As portas ficam vinculadas ao localhost. Para usar uma VM remota, utilize um túnel SSH ou configure seu proxy de acesso. O PostgreSQL não publica uma porta no host.

O serviço `init` cria o esquema, o administrador e papéis distintos para aplicação e Grafana. A inicialização é reaplicável e não redefine senhas de usuários existentes.

A demonstração contém duas empresas fictícias, sete antenas em snapshots simulados de API, três cadastros manuais e seis faturas fictícias. Nenhuma credencial de API é necessária. A quantidade aumenta se você testar importações ou cadastros.

```bash
python3 scripts/smoke.py  # depois que os containers estiverem disponíveis
```

Para parar preservando os dados:

```bash
docker compose down
```

## Arquitetura

A aplicação Flask recebe dados da API ou do CSV e grava no PostgreSQL. O Grafana consulta as visões do esquema `reporting` com um papel dedicado que não recebe acesso à tabela de usuários nem permissão de escrita.

| Componente | Responsabilidade |
|---|---|
| `app/` | Interface, autenticação, perfis, empresas e importação CSV |
| `scripts/sync_starlink.py` | Coletor opcional da API |
| `db/schema.sql` | Esquema e validações de empresa |
| `db/reporting.sql` | Visões para inventário, financeiro, integrações e conectividade |
| `grafana/dashboards/fleet.json` | Dashboard importável e versionado |
| `grafana/provisioning/` | Provisionamento automático da fonte PostgreSQL e dashboard |
| `tests/` | Integração com PostgreSQL efêmero via PGlite |

## Usuários e empresas

O administrador inicial tem acesso às duas empresas. Abra **Usuários e permissões** para criar usuários e marcar uma ou mais empresas.

| Perfil | Consulta | Cadastro / edição / importação | Gerenciar usuários |
|---|---|---|---|
| Leitura | Empresas autorizadas | Não | Não |
| Leitura/Gravação | Empresas autorizadas | Sim | Não |
| Admin | Empresas autorizadas | Sim | Dentro do seu escopo |

A consulta consolidada considera somente as empresas autorizadas para o usuário. O Admin principal é protegido contra desativação pela interface. O perfil atribuído aplica-se a todas as empresas marcadas para o usuário.

A **Empresa Alfa** aceita CPF e CNPJ. A **Empresa Beta** aceita somente CNPJ. São exemplos configurados no bootstrap, não cadastros de organizações reais.

## Importar CSV

Em **Importar antenas · CSV**, selecione a empresa, envie o arquivo, confira a prévia e confirme a importação. O exemplo está em `config/manual-antennas.example.csv`.

```csv
kit;nome_antena;email_original;tipo_conta;obra_regional;plano;mensalidade
DEMO-CSV-EXTRA-001;Antena Exemplo;manual.extra@example.invalid;CPF;Obra Exemplo;Plano informado;219,90
```

São aceitos UTF-8, Windows/Excel e UTF-16 com BOM. O importador identifica ponto e vírgula, vírgula ou tabulação. Os campos obrigatórios são `kit`, `email_original` e `tipo_conta`.

- Limites: 800 KB e 2.000 linhas por arquivo.
- O KIT não pode sobrescrever uma antena da API ou ser transferido implicitamente entre empresas.
- Prévia vinculada ao usuário e à empresa, com expiração de 20 minutos.
- Atualização por CSV substitui os valores informados; obra vazia preserva seu vínculo.
- Mensalidade é uma previsão manual em BRL, não uma fatura emitida ou paga.

## Projeto Grafana

O Compose provisiona a fonte `fleet-postgres` e o dashboard **Starlink Fleet · Gestão e conectividade** automaticamente. Também é possível importar `grafana/dashboards/fleet.json` no seu Grafana, usando uma fonte PostgreSQL com esse UID e as mesmas visões de consulta.

### Painéis incluídos

- Inventário total e divisão CNPJ/CPF.
- Situação contratual, antenas sem obra e integrações desatualizadas.
- Antenas por obra e distribuição por titularidade.
- Valor de faturas no período, saldo atual por moeda e previsão mensal manual em BRL.
- Inventário consolidado, faturas e última coleta das integrações API.
- Online/offline, latência e perda de pacotes preparados para uma coleta futura.

Os filtros são empresa, titularidade, e-mail, obra, conta e moeda. Inventário e saldos representam o estado atual; o intervalo de datas aplica-se às faturas e à telemetria. Filtros de e-mail, obra e titularidade selecionam contas financeiras e exibem a fatura completa, sem rateio por antena.

**Serviço ativo não significa antena online.** Nenhum coletor de conectividade é incluído nesta versão. A tabela `reporting.telemetry_samples` começa vazia; o dashboard mostra “Sem coleta” em vez de inventar disponibilidade. Amostras com mais de cinco minutos também ficam sem estado atual. Mensalidades manuais e faturas da API permanecem separadas.

O login do Grafana é independente do login da aplicação. A fonte demonstrativa é para o administrador e enxerga as duas empresas. **As variáveis do dashboard não são controle de acesso.** Para disponibilizar Grafana a usuários restritos por empresa, é necessário configurar fontes/visões isoladas e a autenticação correspondente; o RBAC da aplicação não é propagado automaticamente ao Grafana.

O `sslmode: disable` do provisionamento vale para a rede local do Compose. Para banco externo, configure TLS e a validação do certificado conforme a documentação oficial.

Para regerar o JSON:

```bash
python3 scripts/build_dashboard.py
```

## API oficial: configuração opcional

O coletor é desabilitado enquanto `DEMO_MODE=true`. Não há automação de navegador nem armazenamento de senhas do portal Starlink.

Para uma instalação separada com dados reais:

1. Use uma base nova, independente da demonstração, e defina `DEMO_MODE=false` **antes do primeiro bootstrap**. Uma base marcada como demo não é convertida automaticamente.
2. Configure `config/starlink-accounts.json` a partir de `config/starlink-accounts.example.json`.
3. Grave os pares `client_id` / `client_secret` nos arquivos locais da pasta `secrets/`, sem versioná-los. Use permissão 600 nos arquivos e 700 na pasta.
4. Associe cada integração ao `company_id` do cadastro correspondente. O mesmo papel de cliente precisa ter acesso às rotas oficiais consultadas.
5. Execute uma coleta manual e confira os logs antes de agendar.

```bash
docker compose --profile api run --rm sync
```

O serviço opcional lê credenciais por montagem somente de leitura e usa o papel limitado da aplicação para gravar no banco. Ele consulta autenticação, terminais, linhas, conta, saldo e faturas. Cada conta é gravada em sua própria transação; falhas preservam o snapshot anterior. E-mails alterados manualmente de antenas já conhecidas não são restaurados pela coleta.

O projeto não afirma que todas as contas pessoais possuem acesso à API. Quando a API não está disponível, utilize o cadastro manual/CSV. Dados manuais não geram faturas automaticamente.

Para agendar, use o comando acima em seu agendador externo. A demonstração não cria timers no host. Ajuste a frequência aos limites e às permissões da API da sua conta.

## Validação

Os testes locais verificam consultas SQL, perfis, isolamento de empresas, bloqueio de escrita, importação CSV, paginação e ausência de telemetria. O CI também executa o Compose e confere login, provisionamento e uma consulta do Grafana ao PostgreSQL.

```bash
pip install -r requirements.txt
npm install --no-save --prefix /tmp/fleet-test @electric-sql/pglite@0.5.13
PGLITE_MODULE=/tmp/fleet-test/node_modules/@electric-sql/pglite/dist/index.js \
  python tests/test_project.py
python scripts/check_publication.py
```

O teste PGlite usa um banco efêmero em memória. Não acessa um banco operacional nem a API Starlink. A validação ponta a ponta com Docker está definida no CI; não confunda aprovação dos testes locais com execução do CI.

## Escopo e próximos passos

Esta é uma base de portfólio e laboratório. Não inclui coletor de telemetria, recuperação de senha por e-mail, integração de identidade entre aplicação e Grafana ou faturamento manual detalhado. O cadastro manual antigo é preservado em uma tela própria; para a visão consolidada da aplicação, utilize CSV. As visões do Grafana também reconhecem esses registros antigos.

Não há licença de redistribuição definida para o código deste projeto. As dependências mantêm suas próprias licenças.

## Referências

- [PostgreSQL no Grafana](https://grafana.com/docs/grafana/latest/datasources/postgres/configure/)
- [Provisionamento do Grafana](https://grafana.com/docs/grafana/latest/administration/provisioning/)
- [Docker Compose](https://docs.docker.com/compose/)
