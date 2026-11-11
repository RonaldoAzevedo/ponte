"""Preferências do usuário (por enquanto, o tema), em %APPDATA%\\Ponte\\config.json."""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import APP_NAME


def config_path() -> Path:
    """Arquivo de preferências do usuário atual."""
    base = Path(os.environ.get("APPDATA") or Path.home())
    return base / APP_NAME / "config.json"


def load() -> dict[str, object]:
    """Preferências salvas; dicionário vazio se não houver ou se estiver corrompido."""
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(**values: object) -> None:
    """Grava ``values`` junto com o que já estava salvo.

    Falhar ao salvar não pode atrapalhar o uso do app: a preferência é só conveniência.
    """
    data = load()
    data.update(values)
    path = config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
