"""Garante que todos os módulos importam (o build roda os testes antes do PyInstaller)."""

import importlib

import pytest


@pytest.mark.parametrize("module", ["main", "vpnbridge.network", "vpnbridge.firewall",
                                    "vpnbridge.proxy", "vpnbridge.gui"])
def test_module_imports(module: str) -> None:
    importlib.import_module(module)
