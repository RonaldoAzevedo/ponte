"""Ponto de entrada do Ponte."""

from __future__ import annotations

import argparse
import os
import sys


def _ensure_std_streams() -> None:
    """No .exe sem console, stdout/stderr são None; o mitmproxy escreve neles."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


def main() -> None:
    _ensure_std_streams()
    parser = argparse.ArgumentParser(description="Ponte")
    parser.add_argument("--porta", type=int, help="porta fixa do proxy (padrão: 8083 ou a próxima livre)")
    args, _ = parser.parse_known_args()
    from vpnbridge.gui import run

    run(args.porta)


if __name__ == "__main__":
    main()
