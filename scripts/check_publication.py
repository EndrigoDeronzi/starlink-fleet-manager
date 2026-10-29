"""Bloqueia arquivos operacionais e padrões comuns de segredos no conjunto versionado."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BLOCKED_PARTS = {
    "backups",
    "uploads",
    "imports",
    "__pycache__",
    "node_modules",
    ".venv",
}
SECRET = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bspx_[A-Za-z0-9_-]{20,}|\bgh[pousr]_[A-Za-z0-9]{25,}|\bgithub_pat_[A-Za-z0-9_]{25,}"
)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")


def violations(files):
    issues = []
    for rel in files:
        p = Path(rel)
        if (p.name.startswith(".env") and p.name != ".env.example") or any(
            part in BLOCKED_PARTS for part in p.parts
        ):
            issues.append((rel, "arquivo operacional"))
        if p.parts and p.parts[0] == "secrets" and p.name != ".gitkeep":
            issues.append((rel, "arquivo de credenciais"))
        if (
            p.parts
            and p.parts[0] == "config"
            and p.suffix == ".json"
            and not p.name.endswith(".example.json")
        ):
            issues.append((rel, "configuração operacional"))
        file = ROOT / p
        if not file.is_file():
            continue
        try:
            text = file.read_text()
        except UnicodeDecodeError:
            issues.append((rel, "arquivo binário: revisão manual necessária"))
            continue
        if SECRET.search(text):
            issues.append((rel, "possível segredo"))
        for domain in EMAIL.findall(text):
            if domain.lower() not in {
                "example.invalid",
                "example.com",
                "example.org",
                "users.noreply.github.com",
            }:
                issues.append((rel, "e-mail fora dos domínios demonstrativos"))
                break
    return issues


def main():
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True, text=True
    )
    files = [p for p in result.stdout.split("\0") if p]
    if not files:
        raise SystemExit(
            "Nenhum arquivo versionado. Adicione somente os arquivos públicos e execute novamente."
        )
    problems = violations(files)
    for name, reason in problems:
        print(name + ": " + reason)
    if problems:
        raise SystemExit("Publicação bloqueada. Revise os arquivos indicados.")
    print(
        "Verificação de publicação OK:",
        len(files),
        "arquivos; nenhum padrão bloqueado encontrado.",
    )


if __name__ == "__main__":
    main()
