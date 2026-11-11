"""Tela de abertura no tema do app: o script Tcl que o .exe roda antes do Python."""

import json
import sys
import tkinter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from splash_theme import MODE_PROC, minify, themed_image_script  # noqa: E402

ASSETS = ROOT / "vpnbridge" / "assets"
DARK_BG = (17, 19, 23)
LIGHT_BG = (243, 244, 246)


@pytest.fixture(scope="module")
def tcl() -> tkinter.Tcl:
    # Um interpretador só: criar vários seguidos às vezes falha ao ler o init.tcl.
    interp = tkinter.Tcl()
    interp.eval(minify(MODE_PROC))
    return interp


def _mode(tcl: tkinter.Tcl, config: Path) -> str:
    return tcl.call("ponte_splash_mode", str(config))


def _write(path: Path, data: object) -> Path:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("saved", "expected"),
    [
        ({"theme": "Claro", "appearance": "dark"}, "light"),
        ({"theme": "Escuro", "appearance": "light"}, "dark"),
        ({"theme": "Sistema", "appearance": "dark"}, "dark"),
        ({"theme": "Sistema", "appearance": "light"}, "light"),
        ({"appearance": "dark"}, "dark"),
        ({"theme": "Sistema"}, "light"),
        ({}, "light"),
    ],
)
def test_mode_follows_saved_theme(tcl, tmp_path: Path, saved: dict, expected: str) -> None:
    assert _mode(tcl, _write(tmp_path / "config.json", saved)) == expected


def test_mode_without_config_or_with_garbage_is_light(tcl, tmp_path: Path) -> None:
    assert _mode(tcl, tmp_path / "nao-existe.json") == "light"
    (tmp_path / "ruim.json").write_bytes(b"\xff\xfe{ nada")
    assert _mode(tcl, tmp_path / "ruim.json") == "light"


def test_rejects_changed_pyinstaller_template() -> None:
    with pytest.raises(RuntimeError):
        themed_image_script("image create photo splash_image", b"", "Ponte")


# --------------------------------------- script completo, como o .exe roda

@pytest.fixture(scope="module")
def tk_root():
    try:
        root = tkinter.Tk()
    except tkinter.TclError as exc:  # sem tela (CI)
        pytest.skip(f"Tk indisponível: {exc}")
    root.withdraw()
    yield root
    root.destroy()


@pytest.mark.parametrize(
    ("saved", "expected_bg"),
    [({"theme": "Escuro"}, DARK_BG), ({"theme": "Claro"}, LIGHT_BG), (None, LIGHT_BG)],
)
def test_script_loads_the_image_of_the_theme(tk_root, tmp_path: Path, saved, expected_bg) -> None:
    splash_templates = pytest.importorskip("PyInstaller.building.splash_templates")
    if saved is not None:
        (tmp_path / "Ponte").mkdir()
        _write(tmp_path / "Ponte" / "config.json", saved)
    script = minify(themed_image_script(
        splash_templates.image_script, (ASSETS / "splash-light.png").read_bytes(), "Ponte"))

    tk_root.eval("catch {image delete splash_image}")
    tk_root.call("set", "env(APPDATA)", str(tmp_path))
    # O bootloader entrega a imagem do --splash (a escura) nesta variável.
    tk_root.call("set", "_image_data", (ASSETS / "splash-dark.png").read_bytes())
    tk_root.eval(script)

    assert (tk_root.call("image", "width", "splash_image"),
            tk_root.call("image", "height", "splash_image")) == (460, 240)
    pixel = tuple(int(v) for v in tk_root.splitlist(tk_root.call("splash_image", "get", 8, 8)))
    assert pixel[:3] == expected_bg
    assert tk_root.eval("info exists _image_data") == "0"


def test_splash_images_have_the_same_size() -> None:
    from PIL import Image

    sizes = {Image.open(ASSETS / f"splash-{mode}.png").size for mode in ("dark", "light")}
    assert sizes == {(460, 240)}
