"""Contraste da paleta nos dois temas (WCAG AA: 4,5:1 para texto)."""

import pytest

from vpnbridge import gui

TEXTS = {"TEXT": gui.TEXT, "TEXT_2": gui.TEXT_2, "TEXT_3": gui.TEXT_3,
         "ACCENT": gui.ACCENT, "WARN": gui.WARN, "DANGER": gui.DANGER}
BACKGROUNDS = {"BG": gui.BG, "SURFACE": gui.SURFACE, "FIELD": gui.FIELD}


def luminance(color: str) -> float:
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


@pytest.mark.parametrize("side, theme", [(0, "claro"), (1, "escuro")])
@pytest.mark.parametrize("text", list(TEXTS))
@pytest.mark.parametrize("background", list(BACKGROUNDS))
def test_text_contrast(side: int, theme: str, text: str, background: str) -> None:
    ratio = contrast(TEXTS[text][side], BACKGROUNDS[background][side])
    assert ratio >= 4.5, f"{text} sobre {background} no tema {theme}: {ratio:.2f}:1"


@pytest.mark.parametrize("side", [0, 1])
def test_primary_button_label_contrast(side: int) -> None:
    for fill in (gui.ACCENT[side], gui.ACCENT_HOVER[side]):
        assert contrast(gui.ON_ACCENT[side], fill) >= 4.5


@pytest.mark.parametrize("side", [0, 1])
def test_segment_text_contrast(side: int) -> None:
    for fill in (gui.SEG_SELECTED[side], gui.FIELD[side], gui.FIELD_HOVER[side]):
        assert contrast(gui.TEXT[side], fill) >= 4.5


def test_mode_color_picks_side() -> None:
    assert gui._mode_color(("#ffffff", "#000000"), "Light") == "#ffffff"
    assert gui._mode_color(("#ffffff", "#000000"), "Dark") == "#000000"
