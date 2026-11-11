"""Roda o PyInstaller com a tela de abertura no tema do app.

Uso (pelo build.ps1): ``python tools/build_exe.py <argumentos do PyInstaller>``.
O ``--splash`` deve apontar para a imagem escura; a clara é lida de
``vpnbridge/assets/splash-light.png``. Veja tools/splash_theme.py.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from splash_theme import themed_image_script  # noqa: E402

from vpnbridge import APP_NAME  # noqa: E402

LIGHT_SPLASH = ROOT / "vpnbridge" / "assets" / "splash-light.png"


def main() -> None:
    import PyInstaller.__main__
    from PyInstaller.building import splash_templates

    splash_templates.image_script = themed_image_script(
        splash_templates.image_script, LIGHT_SPLASH.read_bytes(), APP_NAME)
    PyInstaller.__main__.run(sys.argv[1:])


if __name__ == "__main__":
    main()
