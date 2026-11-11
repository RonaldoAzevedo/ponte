"""Tela de abertura do .exe no tema do app (claro ou escuro).

A splash do PyInstaller é desenhada pelo bootloader, em Tcl/Tk, antes de o Python
começar: o app ainda não existe para escolher a imagem. Por isso o script Tcl da
splash é trocado no build por um que:

1. lê ``%APPDATA%\\Ponte\\config.json`` (gravado pelo app);
2. usa ``theme`` ("Claro"/"Escuro") ou, no modo "Sistema", ``appearance``, que é o
   tema que o app estava mostrando da última vez (o app atualiza esse valor quando o
   Windows troca de tema);
3. sem nada salvo (primeiro uso), mostra a versão clara, o padrão do Windows.

A imagem escura vai para o .exe pelo ``--splash`` normal; a clara vai embutida no
script, em base64. O Tcl só lê um arquivo: não roda programas (como o ``reg.exe``),
o que antivírus corporativo costuma tratar como suspeito.
"""

from __future__ import annotations

import base64
import re

ORIGINAL_PUT = "splash_image put $_image_data"
"""Linha do modelo do PyInstaller que carrega a imagem da splash."""

MODE_PROC = r"""
proc ponte_splash_mode {config} {
    set theme ""
    set appearance ""
    catch {
        set f [open $config r]
        fconfigure $f -encoding utf-8
        set data [read $f]
        close $f
        regexp {"theme"\s*:\s*"([^"]*)"} $data -> theme
        regexp {"appearance"\s*:\s*"(light|dark)"} $data -> appearance
    }
    if {$theme eq "Claro"} {return light}
    if {$theme eq "Escuro"} {return dark}
    if {$appearance ne ""} {return $appearance}
    return light
}
"""
"""Procedimento Tcl: caminho do config.json → "light" ou "dark". Nunca dá erro."""

_CHOOSE = r"""
set _ponte_mode light
catch {set _ponte_mode [ponte_splash_mode [file join $env(APPDATA) @APP@ config.json]]}
if {$_ponte_mode eq "light"} {
    splash_image put {@LIGHT@} -format png
} else {
    splash_image put $_image_data
}
"""


def themed_image_script(original: str, light_png: bytes, app_name: str) -> str:
    """Versão do ``image_script`` do PyInstaller que escolhe a imagem pelo tema.

    Args:
        original: ``PyInstaller.building.splash_templates.image_script``.
        light_png: PNG da versão clara (mesmo tamanho da escura).
        app_name: pasta do app em ``%APPDATA%``.

    Raises:
        RuntimeError: o modelo do PyInstaller mudou; melhor falhar o build do que
            gerar um .exe sem a splash certa.
    """
    if original.count(ORIGINAL_PUT) != 1:
        raise RuntimeError(
            "O modelo da splash do PyInstaller mudou; revise tools/splash_theme.py.")
    choose = (_CHOOSE.replace("@APP@", app_name)
              .replace("@LIGHT@", base64.b64encode(light_png).decode("ascii")))
    return MODE_PROC + original.replace(ORIGINAL_PUT, choose.strip())


def minify(script: str) -> str:
    """Mesma compactação que o PyInstaller aplica ao script da splash (``minify_script``)."""
    lines = (line.strip() for line in script.splitlines())
    return re.sub(" +", " ", "\n".join(line for line in lines if line and not line.startswith("#")))
