"""Gera o config.py limpo que acompanha o aplicativo distribuido."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.config_defaults import default_config_text


def criar_config_distribuicao(destino: Path) -> Path:
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(default_config_text(), encoding='utf-8')
    return destino


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Uso: python tools/criar_config_distribuicao.py DESTINO_CONFIG_PY')
    criar_config_distribuicao(Path(sys.argv[1]))
