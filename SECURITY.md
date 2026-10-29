# Configuração e publicação

Esta demonstração não contém credenciais reais. Os diretórios de credenciais, exportações, backups e a configuração ativa de integrações são ignorados pelo Git. Execute `python scripts/check_publication.py` sobre o conjunto versionado antes de publicar.

O verificador é uma barreira adicional baseada em padrões, não uma garantia de detecção de todos os segredos. Não adicione dumps, screenshots operacionais ou arquivos da instalação de uma organização.

A aplicação aplica permissões por perfil e empresa no servidor. O Grafana usa autenticação independente e um papel de banco somente de leitura sobre o esquema de relatório. O datasource demonstrativo administrativo enxerga todas as empresas fictícias; filtros de dashboard não constituem isolamento entre clientes.

Para acesso remoto, configure seu proxy com HTTPS e `SESSION_COOKIE_SECURE=true`. Não habilite confiança irrestrita em cabeçalhos de proxy. O Compose publica a interface apenas no localhost por padrão.

Se uma credencial for publicada por engano, revogue-a na origem; removê-la de um commit posterior não a retira do histórico.
