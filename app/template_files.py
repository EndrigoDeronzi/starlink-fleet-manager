from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=64)
def template_fragment(name):
    """Somente nomes constantes do código; nenhum caminho vem da requisição."""
    return (Path(__file__).resolve().parent / "templates" / name).read_text()
