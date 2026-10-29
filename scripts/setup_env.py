"""Gera segredos locais; nunca os imprime e não sobrescreve configuração existente."""

import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    path = ROOT / ".env"
    content = (ROOT / ".env.example").read_text()
    for key in (
        "POSTGRES_PASSWORD",
        "APP_DB_PASSWORD",
        "APP_SECRET",
        "INITIAL_ADMIN_PASSWORD",
        "GRAFANA_DB_PASSWORD",
        "GRAFANA_ADMIN_PASSWORD",
    ):
        content = content.replace(key + "=CHANGE_ME", key + "=" + secrets.token_hex(24))
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SystemExit(".env já existe. Nenhum arquivo foi sobrescrito.")
    with os.fdopen(fd, "w") as f:
        f.write(content)
    (ROOT / "secrets").mkdir(exist_ok=True)
    (ROOT / "secrets").chmod(0o700)
    config = ROOT / "config/starlink-accounts.json"
    if not config.exists():
        fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("[]\n")
    print(
        ".env criado com senhas aleatórias. Consulte o arquivo local para os acessos."
    )


if __name__ == "__main__":
    main()
