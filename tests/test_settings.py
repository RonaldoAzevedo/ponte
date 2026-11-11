"""Preferências salvas em %APPDATA%\\Ponte\\config.json."""

from pathlib import Path

import pytest

from vpnbridge import settings


@pytest.fixture(autouse=True)
def appdata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


def test_missing_file_is_empty() -> None:
    assert settings.load() == {}


def test_save_then_load(appdata: Path) -> None:
    settings.save(theme="Claro")
    assert settings.load() == {"theme": "Claro"}
    assert settings.config_path() == appdata / "Ponte" / "config.json"


def test_save_keeps_other_values() -> None:
    settings.save(theme="Claro")
    settings.save(outra="x")
    assert settings.load() == {"theme": "Claro", "outra": "x"}


@pytest.mark.parametrize("content", ["{quebrado", "[1, 2]", ""])
def test_corrupted_file_is_ignored(content: str) -> None:
    path = settings.config_path()
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    assert settings.load() == {}


def test_save_failure_does_not_raise(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    blocker = tmp_path / "arquivo"
    blocker.write_text("x")
    monkeypatch.setenv("APPDATA", str(blocker))  # APPDATA aponta para um arquivo: mkdir falha
    settings.save(theme="Escuro")
