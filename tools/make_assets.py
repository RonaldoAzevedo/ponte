"""Gera os arquivos visuais do app em vpnbridge/assets/.

- icon.ico: ícone da janela e do .exe.
- splash-dark.png / splash-light.png: tela "Abrindo o Ponte..." que o .exe mostra
  enquanto descompacta, uma para cada tema (a escolha é feita por tools/splash_theme.py).

Rodar só quando o desenho mudar: ``python tools/make_assets.py``.
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vpnbridge import APP_NAME, AUTHOR, TAGLINE  # noqa: E402

# Ícone: igual nos dois temas, como o ícone do app na barra de tarefas.
SURFACE = (24, 27, 32, 255)
ACCENT = (52, 196, 124, 255)
TEXT = (231, 233, 236, 255)

# Cores da tela de abertura por tema, as mesmas de vpnbridge/gui.py (BG, BORDER, TEXT...).
SPLASH_PALETTES: dict[str, dict[str, str]] = {
    "dark": {"bg": "#111317", "border": "#2a2f37", "text": "#e7e9ec",
             "text_2": "#9aa1ac", "text_3": "#868d98"},
    "light": {"bg": "#f3f4f6", "border": "#d9dde3", "text": "#16181d",
              "text_2": "#4b5260", "text_3": "#5f6673"},
}

SIZE = 512
ASSETS = Path(__file__).resolve().parents[1] / "vpnbridge" / "assets"
FONTS = Path("C:/Windows/Fonts")


def draw_icon() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((16, 16, SIZE - 16, SIZE - 16), radius=112, fill=SURFACE)

    # Celular (contorno) à esquerda.
    d.rounded_rectangle((80, 132, 198, 380), radius=24, outline=TEXT, width=22)
    d.line((120, 340, 158, 340), fill=TEXT, width=18)

    # Ponte em arco ligando o celular ao nó da VPN.
    d.arc((232, 176, 402, 346), start=180, end=360, fill=ACCENT, width=26)
    d.ellipse((385, 229, 449, 293), fill=ACCENT)
    return img


def draw_splash(mode: str) -> Image.Image:
    """Retângulo opaco: a splash do PyInstaller não tem transparência confiável.

    As duas versões têm o mesmo tamanho: a janela da splash é dimensionada pela imagem.
    """
    c = SPLASH_PALETTES[mode]
    w, h = 460, 240
    img = Image.new("RGBA", (w, h), c["bg"])
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, w - 1, h - 1), outline=c["border"], width=1)

    icon = draw_icon().resize((88, 88), Image.LANCZOS)
    img.alpha_composite(icon, (36, 56))

    title = ImageFont.truetype(str(FONTS / "seguisb.ttf"), 34)
    body = ImageFont.truetype(str(FONTS / "segoeui.ttf"), 16)
    small = ImageFont.truetype(str(FONTS / "segoeui.ttf"), 14)
    d.text((148, 58), APP_NAME, font=title, fill=c["text"])
    d.text((150, 108), TAGLINE, font=body, fill=c["text_2"])
    # Sem barra de progresso: a splash não anima, e uma barra parada parece travamento.
    d.text((36, 188), f"Abrindo o {APP_NAME}...", font=small, fill=c["text_3"])
    d.text((w - 36, 188), f"criado por {AUTHOR}", font=small, fill=c["text_3"], anchor="ra")
    return img.convert("RGB")


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    draw_icon().save(ASSETS / "icon.ico",
                     sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    for mode in SPLASH_PALETTES:
        draw_splash(mode).save(ASSETS / f"splash-{mode}.png")
    print(ASSETS)


if __name__ == "__main__":
    main()
