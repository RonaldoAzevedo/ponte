"""Janela única em CustomTkinter, com visual próprio e tema claro/escuro.

Regras de thread: só a thread da interface mexe em widgets. Trabalho lento
(PowerShell, netsh) roda em threads de fundo e devolve o resultado por
``self._ui_calls``; eventos do proxy chegam por ``ProxyController.events``.
Ambas as filas são drenadas por :meth:`App._poll`.
"""

from __future__ import annotations

import queue
import sys
import threading
import time
import tkinter
import tkinter.font as tkfont
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import customtkinter as ctk

from . import APP_NAME, AUTHOR, TAGLINE, __version__, firewall, network, settings
from .proxy import EventKind, LogEntry, ProxyConfig, ProxyController, ProxyEvent, ProxyMode

POLL_MS = 100
NETWORK_CHECK_MS = 15_000
WAIT_HINT_MS = 30_000
TICK_MS = 5_000
IDLE_S = 60
COPIED_FEEDBACK_MS = 1_500
MAX_LOG_LINES = 2000

# Paleta (claro, escuro): superfícies neutras e uma única cor de destaque. O CTk
# escolhe o lado conforme o tema. Todo texto passa de 4,5:1 (WCAG AA) sobre
# BG, SURFACE e FIELD nos dois temas (conferido por tests/test_theme.py).
BG = ("#f3f4f6", "#111317")
SURFACE = ("#ffffff", "#181b20")
FIELD = ("#eceef2", "#20242b")
FIELD_HOVER = ("#e2e5ea", "#272c34")
BORDER = ("#d9dde3", "#2a2f37")
SEG_SELECTED = ("#ffffff", "#3a404a")
SEG_SELECTED_HOVER = ("#f7f8fa", "#434a55")
TEXT = ("#16181d", "#e7e9ec")
TEXT_2 = ("#4b5260", "#9aa1ac")
TEXT_3 = ("#5f6673", "#868d98")
ACCENT = ("#12733a", "#34c47c")
ACCENT_HOVER = ("#0f6230", "#2dab6c")
ON_ACCENT = ("#ffffff", "#0c1a12")
WARN = ("#8a5200", "#e5a54b")
DANGER = ("#b3261e", "#f06a6a")
FOCUS = ACCENT  # anel de foco do teclado: passa de 3:1 sobre BG e SURFACE nos dois temas

THEMES = {"Sistema": "system", "Claro": "light", "Escuro": "dark"}
PLATFORMS = ("iPhone", "Android")
MODES = {"Passthrough": ProxyMode.PASSTHROUGH, "Inspeção": ProxyMode.INSPECT}

# Nome do campo do servidor na tela de proxy de cada sistema.
SERVER_FIELD = {"iPhone": "Servidor", "Android": "Nome do host"}

STEPS = {
    "iPhone": [
        "Ajustes → Wi-Fi",
        "Toque no (i) da rede atual",
        "Configurar Proxy → Manual",
        "Digite o Servidor e a Porta (à esquerda)",
        "Autenticação desligada → Salvar",
    ],
    "Android": [
        "Configurações → Wi-Fi",
        "Toque na engrenagem da rede atual",
        "Opções avançadas → Proxy → Manual",
        "Digite o Nome do host e a Porta (à esquerda)",
        "Salvar",
    ],
}

PROXY_OFF_PATH = {
    "iPhone": "Ajustes → Wi-Fi → (i) → Configurar Proxy → Desligado",
    "Android": "Configurações → Wi-Fi → engrenagem da rede → Proxy → Nenhum",
}

MODE_HELP = {
    ProxyMode.PASSTHROUGH: "Só encaminha o tráfego. Nada para instalar no celular.",
    ProxyMode.INSPECT: "Mostra as requisições HTTPS. Exige certificado (veja ao lado).",
}

INSPECT_HELP = {
    "iPhone": (
        "Abra http://mitm.it no Safari → iOS → Permitir. Instale em Ajustes → Perfil "
        "Transferido e ative em Geral → Sobre → Ajustes de Confiança de Certificados. "
        "Apps com certificate pinning recusam."
    ),
    "Android": (
        "Abra http://mitm.it no Chrome → Android e instale em Segurança → Criptografia e "
        "credenciais → Certificado CA. Só o Chrome e o build de debug do seu app "
        "confiam nele."
    ),
}


def _mode_color(color: tuple[str, str], mode: str | None = None) -> str:
    """Lado da cor para o tema atual (para APIs do Tk que não aceitam pares)."""
    mode = (mode or ctk.get_appearance_mode()).lower()
    return color[0] if mode == "light" else color[1]


def _resource(relative: str) -> Path:
    """Caminho de um arquivo empacotado (funciona no .exe do PyInstaller)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "vpnbridge" / relative


def _pick_font(candidates: tuple[str, ...]) -> str:
    families = set(tkfont.families())
    return next((f for f in candidates if f in families), candidates[-1])


class Dot(ctk.CTkFrame):
    """Indicador de status: um círculo pequeno, sem depender de emoji."""

    def __init__(self, master: ctk.CTkBaseClass, color: tuple[str, str] = TEXT_3,
                 size: int = 8) -> None:
        super().__init__(master, width=size, height=size, corner_radius=size // 2,
                         fg_color=color, border_width=0)

    def set(self, color: tuple[str, str]) -> None:
        self.configure(fg_color=color)


class StatusRow(ctk.CTkFrame):
    """Linha "● Nome    valor" usada para VPN, firewall e celular."""

    def __init__(self, master: ctk.CTkBaseClass, title: str, fonts: dict[str, ctk.CTkFont]) -> None:
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(2, weight=1)
        self.dot = Dot(self)
        self.dot.grid(row=0, column=0, padx=(0, 10))
        ctk.CTkLabel(self, text=title, font=fonts["body"], text_color=TEXT_2, width=70,
                     height=22, anchor="w").grid(row=0, column=1, sticky="w")
        self.value = ctk.CTkLabel(self, text="—", font=fonts["body"], text_color=TEXT,
                                  height=22, anchor="w", justify="left")
        self.value.grid(row=0, column=2, sticky="w")

    def set(self, text: str, color: tuple[str, str]) -> None:
        self.dot.set(color)
        self.value.configure(text=text)


class FocusRing(ctk.CTkFrame):
    """Moldura que acende quando o controle de dentro recebe o foco do teclado.

    Os botões, seletores e listas do CTk não aceitam Tab nem mostram foco;
    :meth:`App._keyboard` liga isso e usa esta moldura como indicador visível.
    ``idle`` é a cor do fundo onde a moldura está (fica invisível sem foco).
    """

    def __init__(self, master: ctk.CTkBaseClass, idle: tuple[str, str], radius: int = 9) -> None:
        super().__init__(master, fg_color="transparent", border_width=2, border_color=idle,
                         corner_radius=radius)
        self._idle = idle

    def show(self, focused: bool) -> None:
        self.configure(border_color=FOCUS if focused else self._idle)


class ConfirmDialog(ctk.CTkToplevel):
    """Diálogo modal no tema do app, com botões que dizem a ação (nada de OK/Cancelar).

    Enter confirma e Esc cancela. Sem ``cancel``, vira um aviso de um botão só.
    ``tone`` ("error", "warn" ou "info") colore o título. Depois de ``wait_window``,
    :attr:`confirmed` diz se o usuário escolheu a ação.
    """

    def __init__(self, master: App, title: str, message: str, confirm: str,
                 cancel: str | None = None, tone: str = "info") -> None:
        super().__init__(master, fg_color=SURFACE)
        self.confirmed = False
        self.title(APP_NAME)
        self.resizable(False, False)
        self.transient(master)
        icon = _resource("assets/icon.ico")
        if icon.exists():
            self.iconbitmap(str(icon))

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=(20, 18))
        title_color = {"error": DANGER, "warn": WARN}.get(tone, TEXT)
        ctk.CTkLabel(body, text=title, font=master.fonts["dialog_title"], text_color=title_color,
                     anchor="w", justify="left", wraplength=400).pack(fill="x")
        ctk.CTkLabel(body, text=message, font=master.fonts["body"], text_color=TEXT_2,
                     justify="left", anchor="w", wraplength=400).pack(fill="x", pady=(8, 20))
        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.pack(fill="x")
        ctk.CTkButton(buttons, text=confirm, command=self._confirm, height=32,
                      font=master.fonts["button"], fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      text_color=ON_ACCENT, corner_radius=7).pack(side="right")
        if cancel is not None:
            ctk.CTkButton(buttons, text=cancel, command=self.destroy, height=32,
                          font=master.fonts["button"], fg_color=FIELD, hover_color=FIELD_HOVER,
                          text_color=TEXT, border_width=1, border_color=BORDER,
                          corner_radius=7).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _e: self.destroy())
        self.bind("<Return>", lambda _e: self._confirm())
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{x}+{y}")
        self.after(20, self._activate)

    def _activate(self) -> None:
        # O CTk esconde a janela para pintar a barra de título e só a reexibe num
        # timer, que atrasa enquanto a janela principal desenha. Mostrar explicitamente.
        self.deiconify()
        self.lift()
        self.focus_force()
        try:
            self.grab_set()
        except tkinter.TclError:  # janela ainda não visível; segue sem modal
            pass

    def _revert_withdraw_after_windows_set_titlebar_color(self) -> None:
        # Timer do CTk que pode disparar depois de o diálogo fechar ("bad window path name").
        if self.winfo_exists():
            super()._revert_withdraw_after_windows_set_titlebar_color()

    def _confirm(self) -> None:
        self.confirmed = True
        self.destroy()


class App(ctk.CTk):
    """Janela principal do Ponte."""

    def __init__(self, port: int | None = None) -> None:
        """
        Args:
            port: porta fixa escolhida pelo usuário (``--porta``). Se ``None``,
                usa a porta padrão ou a próxima livre.
        """
        saved = settings.load()
        theme = saved.get("theme")
        self.theme = theme if theme in THEMES else "Sistema"
        self._saved_appearance = saved.get("appearance")
        ctk.set_appearance_mode(THEMES[self.theme])
        super().__init__(fg_color=BG)
        self._remember_appearance(ctk.get_appearance_mode())
        self.title(APP_NAME)
        self._fit_to_screen()
        icon = _resource("assets/icon.ico")
        if icon.exists():
            self.iconbitmap(str(icon))

        ui = _pick_font(("Segoe UI Variable Text", "Segoe UI"))
        mono = _pick_font(("Cascadia Mono", "Consolas"))
        self.fonts = {
            "title": ctk.CTkFont(family=ui, size=18, weight="bold"),
            "dialog_title": ctk.CTkFont(family=ui, size=16, weight="bold"),
            "body": ctk.CTkFont(family=ui, size=13),
            "small": ctk.CTkFont(family=ui, size=12),
            "caption": ctk.CTkFont(family=ui, size=11, weight="bold"),
            "value": ctk.CTkFont(family=mono, size=28),
            "mono": ctk.CTkFont(family=mono, size=12),
            "button": ctk.CTkFont(family=ui, size=13, weight="bold"),
        }

        self.controller = ProxyController()
        self._ui_calls: queue.Queue[Callable[[], None]] = queue.Queue()
        self.mode = ProxyMode.PASSTHROUGH
        self.platform = PLATFORMS[0]
        self.port = port or network.DEFAULT_PORT
        self._fixed_port = port is not None
        self.candidates: list[network.Interface] = []
        self.selected: network.Interface | None = None
        self._stopping_for_restart = False
        self._closing = False
        self._auto_start_pending = True
        self._proxy_active = False
        self._run_id = 0
        self._run_clients: set[str] = set()
        self._clients_seen: set[str] = set()
        self._last_client: str | None = None
        self._last_activity = 0.0
        self._cert_rejected_run = False
        self._fw_confirmed_port: int | None = None
        self._step_labels: list[ctk.CTkLabel] = []
        self._tip_labels: list[ctk.CTkLabel] = []
        self._wraps_pending = False
        self._wait_hint_visible = False

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(POLL_MS, self._poll)
        self.after(TICK_MS, self._tick)
        self.after(50, self._initial_setup)

    def _fit_to_screen(self) -> None:
        """Tamanho inicial que cabe na tela (notebooks 1366x768 incluídos)."""
        scale = self._get_window_scaling()
        available = int(self.winfo_screenheight() / scale) - 90  # barra de título + tarefas
        height = max(600, min(760, available))
        self.geometry(f"1040x{height}")
        # 720 garante dicas + log visíveis no pior caso; telas baixas usam o que houver.
        self.minsize(940, min(720, height))

    # ------------------------------------------------------------------ layout

    def _card(self, master: ctk.CTkBaseClass) -> ctk.CTkFrame:
        return ctk.CTkFrame(master, fg_color=SURFACE, corner_radius=10,
                            border_width=1, border_color=BORDER)

    def _caption(self, master: ctk.CTkBaseClass, text: str) -> ctk.CTkLabel:
        return ctk.CTkLabel(master, text=text.upper(), font=self.fonts["caption"],
                            text_color=TEXT_3, anchor="w", height=16)

    def _keyboard(self, widget: ctk.CTkBaseClass, ring: FocusRing,
                  keys: dict[str, Callable[[], object]]) -> None:
        """Deixa ``widget`` alcançável por Tab, com anel de foco e teclas de ação."""
        target = getattr(widget, "_entry", None) or widget._canvas  # Entry nativo ou canvas do CTk
        target.configure(takefocus=1)
        target.bind("<FocusIn>", lambda _e: ring.show(True), add="+")
        target.bind("<FocusOut>", lambda _e: ring.show(False), add="+")
        for key, action in keys.items():
            target.bind(key, lambda _e, act=action: (act(), "break")[1])

    def _button(self, master: ctk.CTkBaseClass, idle: tuple[str, str],
                **kwargs: object) -> tuple[FocusRing, ctk.CTkButton]:
        """Botão com foco de teclado (Enter/Espaço). Posicione a moldura retornada."""
        ring = FocusRing(master, idle, radius=int(kwargs.get("corner_radius", 7)) + 2)
        button = ctk.CTkButton(ring, **kwargs)
        button.pack(padx=2, pady=2)
        self._keyboard(button, ring, {k: button.invoke for k in ("<Return>", "<KP_Enter>", "<space>")})
        return ring, button

    def _ghost_button(self, master: ctk.CTkBaseClass, idle: tuple[str, str], text: str,
                      command: Callable[[], None], width: int = 80) -> tuple[FocusRing, ctk.CTkButton]:
        return self._button(master, idle, text=text, command=command, width=width, height=30,
                            font=self.fonts["small"], fg_color=FIELD, hover_color=FIELD_HOVER,
                            text_color=TEXT, border_width=1, border_color=BORDER, corner_radius=7)

    def _segmented(self, master: ctk.CTkBaseClass, idle: tuple[str, str], values: list[str],
                   command: Callable[[str], None], height: int = 30,
                   radius: int = 7) -> tuple[FocusRing, ctk.CTkSegmentedButton]:
        """Seletor com foco de teclado: setas ←/→ trocam a opção."""
        ring = FocusRing(master, idle, radius=radius + 2)
        seg = ctk.CTkSegmentedButton(
            ring, values=values, command=command, font=self.fonts["small"], height=height,
            fg_color=FIELD, selected_color=SEG_SELECTED, selected_hover_color=SEG_SELECTED_HOVER,
            unselected_color=FIELD, unselected_hover_color=FIELD_HOVER,
            text_color=TEXT, corner_radius=radius,
        )
        seg.pack(padx=2, pady=2)
        self._keyboard(seg, ring, {"<Left>": lambda: self._step_choice(seg, -1, command),
                                   "<Right>": lambda: self._step_choice(seg, 1, command)})
        return ring, seg

    @staticmethod
    def _step_choice(widget: ctk.CTkSegmentedButton | ctk.CTkOptionMenu, delta: int,
                     command: Callable[[str], None]) -> None:
        if widget.cget("state") == "disabled":
            return
        values = list(widget.cget("values"))
        if not values:
            return
        current = widget.get()
        index = values.index(current) if current in values else 0
        new = values[max(0, min(len(values) - 1, index + delta))]
        if new != current:
            widget.set(new)
            command(new)

    def _help_box(self, master: ctk.CTkBaseClass, title: str) -> ctk.CTkFrame:
        """Caixa de ajuda contextual (certificado, dicas de conexão); conteúdo na linha 1."""
        box = ctk.CTkFrame(master, fg_color=FIELD, corner_radius=8)
        box.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(box, text=title, font=self.fonts["button"], text_color=WARN,
                     anchor="w").grid(row=0, column=0, sticky="w", padx=14, pady=(8, 0))
        return box

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        pad = 20

        # Barra superior: nome, status e o único botão principal.
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=pad, pady=(12, 8))
        top.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(top, text=APP_NAME, font=self.fonts["title"], text_color=TEXT).grid(
            row=0, column=0, sticky="w")
        ctk.CTkLabel(top, text=TAGLINE, font=self.fonts["small"],
                     text_color=TEXT_3).grid(row=0, column=1, sticky="w", padx=(8, 0))
        status = ctk.CTkFrame(top, fg_color="transparent")
        status.grid(row=0, column=2, padx=(0, 12))
        self.status_dot = Dot(status)
        self.status_dot.pack(side="left", padx=(0, 8))
        self.status_label = ctk.CTkLabel(status, text="Parado", font=self.fonts["body"],
                                         text_color=TEXT_2)
        self.status_label.pack(side="left")
        run_ring, self.run_button = self._button(
            top, BG, text="Iniciar", width=104, height=32, font=self.fonts["button"],
            corner_radius=7, command=self._toggle_running)
        run_ring.grid(row=0, column=3)

        # Linha principal: o que digitar + ajustes + status (esquerda), passo a passo (direita).
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=1, column=0, sticky="ew", padx=pad)
        main.grid_columnconfigure(0, weight=3, uniform="cols")
        main.grid_columnconfigure(1, weight=2, uniform="cols")
        self._build_address_card(main).grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self._build_setup_card(main).grid(row=0, column=1, sticky="nsew")

        self.log_card = self._build_log_card(self)
        self.log_card.grid(row=2, column=0, sticky="nsew", padx=pad, pady=(12, 6))

        # Rodapé discreto: tema à esquerda, versão e autoria à direita.
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, sticky="ew", padx=pad, pady=(0, 8))
        footer.grid_columnconfigure(2, weight=1)
        ctk.CTkLabel(footer, text="Tema", font=self.fonts["small"], text_color=TEXT_3).grid(
            row=0, column=0, sticky="w", padx=(4, 6))
        theme_ring, self.theme_switch = self._segmented(
            footer, BG, list(THEMES), self._on_theme_selected, height=24, radius=6)
        self.theme_switch.set(self.theme)
        theme_ring.grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(footer, text=f"v{__version__}  ·  criado por {AUTHOR}",
                     font=self.fonts["small"], text_color=TEXT_3).grid(
            row=0, column=2, sticky="e", padx=(0, 4))

        self.setup_inner.bind("<Configure>", lambda _e: self._schedule_wraps())
        self.log_card.bind("<Configure>", lambda _e: self._schedule_wraps())
        self._update_mode_widgets()
        self._update_running_widgets()

    def _build_address_card(self, master: ctk.CTkBaseClass) -> ctk.CTkFrame:
        card = self._card(master)
        card.grid_columnconfigure(0, weight=1)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.grid(row=0, column=0, sticky="nsew", padx=20, pady=16)
        inner.grid_columnconfigure(1, weight=1)

        # O que digitar no celular: Servidor e Porta separados, como nos campos do aparelho.
        self._caption(inner, "Digite no celular").grid(row=0, column=0, columnspan=2, sticky="w")
        values = ctk.CTkFrame(inner, fg_color="transparent")
        values.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 6))
        self.server_caption = ctk.CTkLabel(values, text="Servidor", font=self.fonts["small"],
                                           text_color=TEXT_2, anchor="w", height=18)
        self.server_caption.grid(row=0, column=0, sticky="w")
        self.server_value = ctk.CTkLabel(values, text="—", font=self.fonts["value"],
                                         text_color=TEXT, anchor="w")
        self.server_value.grid(row=1, column=0, sticky="w")
        ctk.CTkLabel(values, text="Porta", font=self.fonts["small"], text_color=TEXT_2,
                     anchor="w", height=18).grid(row=0, column=1, sticky="w", padx=(36, 0))
        self.port_value = ctk.CTkLabel(values, text="—", font=self.fonts["value"],
                                       text_color=TEXT, anchor="w")
        self.port_value.grid(row=1, column=1, sticky="w", padx=(36, 0))
        copy_ring, self.copy_button = self._button(
            values, SURFACE, text="Copiar", width=64, height=26, font=self.fonts["small"],
            fg_color="transparent", hover_color=FIELD_HOVER, text_color=TEXT_2,
            corner_radius=6, command=self._copy)
        copy_ring.grid(row=1, column=2, sticky="s", padx=(16, 0), pady=(0, 4))

        def field_label(row: int, text: str) -> None:
            ctk.CTkLabel(inner, text=text, font=self.fonts["body"], text_color=TEXT_2,
                         anchor="w", width=76).grid(row=row, column=0, sticky="nw", pady=4)

        self._caption(inner, "Ajustes").grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 2))
        field_label(3, "Rede")
        net_row = ctk.CTkFrame(inner, fg_color="transparent")
        net_row.grid(row=3, column=1, sticky="w", pady=2)
        menu_ring = FocusRing(net_row, SURFACE)
        self.iface_menu = ctk.CTkOptionMenu(
            menu_ring, values=["detectando..."], width=300, height=30,
            font=self.fonts["small"], dropdown_font=self.fonts["small"],
            fg_color=FIELD, button_color=FIELD, button_hover_color=FIELD_HOVER,
            # Com uma rede só a lista fica desabilitada, mas o valor precisa continuar legível.
            text_color=TEXT, text_color_disabled=TEXT_2,
            dropdown_fg_color=FIELD, dropdown_hover_color=FIELD_HOVER,
            dropdown_text_color=TEXT, corner_radius=7, command=self._on_iface_selected,
        )
        self.iface_menu.pack(padx=2, pady=2)
        open_menu = lambda: (self.iface_menu.cget("state") != "disabled"  # noqa: E731
                             and self.iface_menu._open_dropdown_menu())
        self._keyboard(self.iface_menu, menu_ring, {
            "<Up>": lambda: self._step_choice(self.iface_menu, -1, self._on_iface_selected),
            "<Down>": lambda: self._step_choice(self.iface_menu, 1, self._on_iface_selected),
            "<Return>": open_menu, "<space>": open_menu,
        })
        menu_ring.pack(side="left")
        refresh_ring, _ = self._ghost_button(net_row, SURFACE, "Atualizar", self._refresh_interfaces)
        refresh_ring.pack(side="left", padx=(4, 0))

        field_label(4, "Porta")
        port_ring = FocusRing(inner, SURFACE)
        self.port_entry = ctk.CTkEntry(port_ring, width=80, height=30, font=self.fonts["mono"],
                                       fg_color=FIELD, border_color=BORDER, border_width=1,
                                       text_color=TEXT, corner_radius=7, justify="center")
        self.port_entry.pack(padx=2, pady=2)
        self.port_entry.insert(0, str(self.port))
        self._keyboard(self.port_entry, port_ring, {"<Return>": self._start})
        port_ring.grid(row=4, column=1, sticky="w", pady=2)

        field_label(5, "Modo")
        mode_box = ctk.CTkFrame(inner, fg_color="transparent")
        mode_box.grid(row=5, column=1, sticky="w", pady=2)
        mode_ring, self.mode_switch = self._segmented(mode_box, SURFACE, list(MODES),
                                                      self._on_mode_selected)
        self.mode_switch.set("Passthrough")
        mode_ring.pack(anchor="w")
        self.mode_help = ctk.CTkLabel(mode_box, text="", font=self.fonts["small"],
                                      text_color=TEXT_2, anchor="w", justify="left",
                                      wraplength=400, height=18)
        self.mode_help.pack(anchor="w", padx=2, pady=(2, 0))

        self._caption(inner, "Status").grid(row=6, column=0, columnspan=2, sticky="w", pady=(12, 2))
        self.vpn_row = StatusRow(inner, "VPN", self.fonts)
        self.vpn_row.grid(row=7, column=0, columnspan=2, sticky="ew", pady=2)
        fw = ctk.CTkFrame(inner, fg_color="transparent")
        fw.grid(row=8, column=0, columnspan=2, sticky="ew", pady=2)
        fw.grid_columnconfigure(0, weight=1)
        self.fw_row = StatusRow(fw, "Firewall", self.fonts)
        self.fw_row.grid(row=0, column=0, sticky="ew")
        self.fw_ring, self.fw_button = self._button(
            fw, SURFACE, text="Liberar porta", width=100, height=26, font=self.fonts["small"],
            fg_color="transparent", hover_color=FIELD_HOVER, text_color=WARN,
            border_width=1, border_color=WARN, corner_radius=6,
            command=self._create_firewall_rule)
        self.client_row = StatusRow(inner, "Celular", self.fonts)
        self.client_row.grid(row=9, column=0, columnspan=2, sticky="ew", pady=2)
        self.client_row.set("proxy parado", TEXT_3)
        return card

    def _build_setup_card(self, master: ctk.CTkBaseClass) -> ctk.CTkFrame:
        card = self._card(master)
        card.grid_columnconfigure(0, weight=1)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.grid(row=0, column=0, sticky="nsew", padx=20, pady=16)
        inner.grid_columnconfigure(0, weight=1)
        self.setup_inner = inner

        head = ctk.CTkFrame(inner, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew")
        head.grid_columnconfigure(0, weight=1)
        self._caption(head, "Configurar no celular").grid(row=0, column=0, sticky="w")
        platform_ring, self.platform_switch = self._segmented(
            head, SURFACE, list(PLATFORMS), self._on_platform_selected)
        self.platform_switch.set(self.platform)
        platform_ring.grid(row=0, column=1, sticky="e")

        self.steps_frame = ctk.CTkFrame(inner, fg_color="transparent")
        self.steps_frame.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        self.steps_frame.grid_columnconfigure(1, weight=1)

        self.inspect_box = self._help_box(inner, "Certificado para o modo Inspeção")
        self.inspect_label = ctk.CTkLabel(self.inspect_box, text="", font=self.fonts["small"],
                                          text_color=TEXT_2, justify="left", anchor="w",
                                          wraplength=316)
        self.inspect_label.grid(row=1, column=0, sticky="w", padx=14, pady=(0, 12))
        return card

    def _build_log_card(self, master: ctk.CTkBaseClass) -> ctk.CTkFrame:
        card = self._card(master)
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(3, weight=1)

        head = ctk.CTkFrame(card, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=20, pady=(10, 2))
        head.grid_columnconfigure(0, weight=1)
        self._caption(head, "Atividade").grid(row=0, column=0, sticky="w")
        clear_ring, _ = self._button(
            head, SURFACE, text="Limpar", width=60, height=24, font=self.fonts["small"],
            fg_color="transparent", hover_color=FIELD_HOVER, text_color=TEXT_2,
            corner_radius=6, command=self._clear_log)
        clear_ring.grid(row=0, column=1, sticky="e")

        # Estado vazio útil: enquanto nada conecta, o log está vazio e as dicas cabem aqui.
        self.wait_box = self._help_box(card, "Nenhum celular conectou ainda")
        self.wait_tips = ctk.CTkFrame(self.wait_box, fg_color="transparent")
        self.wait_tips.grid(row=1, column=0, sticky="ew", padx=14, pady=(2, 10))
        self.wait_tips.grid_columnconfigure((1, 3), weight=1, uniform="tips")

        columns = f"{'HORA':<8}  {'CLIENTE':<15}  {'STATUS':<10}  HOST"
        self.log_columns = ctk.CTkLabel(card, text=columns, font=self.fonts["mono"],
                                        text_color=TEXT_3, anchor="w", height=20)
        self.log_columns.grid(row=2, column=0, sticky="ew", padx=26)

        # height pequeno: é o mínimo pedido; a linha com weight=1 estica até o espaço livre.
        self.log_box = ctk.CTkTextbox(card, font=self.fonts["mono"], wrap="word", height=40,
                                      fg_color=SURFACE, text_color=TEXT, border_width=0,
                                      scrollbar_button_color=FIELD,
                                      scrollbar_button_hover_color=FIELD_HOVER)
        self.log_box.grid(row=3, column=0, sticky="nsew", padx=20, pady=(2, 10))
        # Somente leitura e fora do Tab: o Text do Tk prenderia o foco (Tab insere tabulação).
        self.log_box._textbox.configure(takefocus=0)
        self._apply_log_colors()
        self.log_box.configure(state="disabled")
        return card

    # ------------------------------------------------------------------ tema

    def _apply_log_colors(self, mode: str | None = None) -> None:
        """Cores das tags do log; o Tk não aceita pares (claro, escuro) em tags."""
        for tag, color in (("muted", TEXT_3), ("info", TEXT_2), ("ok", ACCENT),
                           ("warn", WARN), ("error", DANGER)):
            self.log_box.tag_config(tag, foreground=_mode_color(color, mode))

    def _set_appearance_mode(self, mode_string: str) -> None:
        # Chamado pelo CTk a cada troca de tema, inclusive quando o Windows muda sozinho.
        super()._set_appearance_mode(mode_string)
        if hasattr(self, "log_box"):
            self._apply_log_colors(mode_string)
        if hasattr(self, "_saved_appearance"):
            self._remember_appearance(mode_string)

    def _remember_appearance(self, mode: str) -> None:
        """Grava o tema em uso ("light"/"dark") para a tela de abertura do próximo início.

        A splash do .exe aparece antes do Python e não consegue resolver "Sistema"
        sozinha (veja tools/splash_theme.py). Só grava quando muda.
        """
        mode = mode.lower()
        if mode in ("light", "dark") and mode != self._saved_appearance:
            self._saved_appearance = mode
            settings.save(appearance=mode)

    def _on_theme_selected(self, label: str) -> None:
        self.theme = label
        ctk.set_appearance_mode(THEMES[label])
        settings.save(theme=label)

    # ------------------------------------------------- quebra de linha responsiva

    def _schedule_wraps(self) -> None:
        if not self._wraps_pending:
            self._wraps_pending = True
            self.after_idle(self._apply_wraps)

    def _apply_wraps(self) -> None:
        """Ajusta a quebra de linha dos textos à largura atual (nada de texto cortado)."""
        self._wraps_pending = False
        scale = ctk.ScalingTracker.get_widget_scaling(self)
        right = int(self.setup_inner.winfo_width() / scale)
        if right > 150:
            for label in self._step_labels:
                label.configure(wraplength=right - 40)
            self.inspect_label.configure(wraplength=right - 32)
        log = int(self.log_card.winfo_width() / scale)
        if log > 150:
            for label in self._tip_labels:
                label.configure(wraplength=(log - 90) // 2 - 28)

    # ------------------------------------------------------------ inicialização

    def _initial_setup(self) -> None:
        if not self._fixed_port:
            try:
                self.port = network.find_free_port(network.DEFAULT_PORT)
            except RuntimeError as exc:
                self._notify("Nenhuma porta livre", str(exc), "error")
            if self.port != network.DEFAULT_PORT:
                owner = network.port_owner(network.DEFAULT_PORT) or "outro programa"
                self._log_message(
                    f"Porta {network.DEFAULT_PORT} em uso por {owner}; usando {self.port}. "
                    f"Se o celular não conectar, feche o {owner} e use a {network.DEFAULT_PORT}.",
                    "warn",
                )
        self._update_address()
        self._check_network(initial=True)

    def _in_background(self, work: Callable[[], object], done: Callable[[object], None]) -> None:
        """Roda ``work`` numa thread e entrega o resultado a ``done`` na thread da GUI."""

        def runner() -> None:
            try:
                result: object = work()
            except Exception as exc:  # noqa: BLE001 - devolvido à GUI como resultado
                result = exc
            self._ui_calls.put(lambda: done(result))

        threading.Thread(target=runner, daemon=True).start()

    # --------------------------------------------------- redes e VPN (periódico)

    def _check_network(self, initial: bool = False, manual: bool = False) -> None:
        """Consulta redes locais e VPN; sem ``manual``, repete a cada 15 s."""

        def done(result: object) -> None:
            self._apply_snapshot(result, initial=initial, manual=manual)
            if not manual and not self._closing:
                self.after(NETWORK_CHECK_MS, self._check_network)

        self._in_background(network.snapshot, done)

    def _refresh_interfaces(self) -> None:
        self.iface_menu.configure(values=["detectando..."])
        self.iface_menu.set("detectando...")
        self._check_network(manual=True)

    def _apply_snapshot(self, result: object, initial: bool = False, manual: bool = False) -> None:
        if isinstance(result, Exception):
            self.vpn_row.set("não foi possível verificar", TEXT_3)
            if initial or manual:
                self._log_message(f"Não foi possível listar as redes: {result}", "error")
            return
        lans, vpns = result  # type: ignore[misc]
        self._show_vpn(vpns)
        if initial or manual:
            self._set_candidates(lans, notify_empty=True)
        else:
            self._sync_candidates(lans)
        if self._auto_start_pending and self.selected is not None and not self.controller.running:
            self._auto_start_pending = False
            self._start()

    def _show_vpn(self, vpns: list[network.Interface]) -> None:
        if vpns:
            names = ", ".join(sorted({i.name for i in vpns}))
            self.vpn_row.set(f"conectada · {names}", ACCENT)
        else:
            self.vpn_row.set("desconectada · o celular navega, mas sem a rede da empresa", DANGER)

    def _sync_candidates(self, lans: list[network.Interface]) -> None:
        """Verificação periódica: acompanha o PC trocando de rede ou de IP."""
        if [c.label for c in lans] == [c.label for c in self.candidates]:
            return
        previous = self.selected.ip if self.selected else None
        self._set_candidates(lans, notify_empty=False)
        current = self.selected.ip if self.selected else None
        if current == previous:
            return
        if current is None:
            self._log_message("O PC saiu da rede local. Conecte-o ao mesmo Wi-Fi do celular.", "warn")
        elif previous is not None:
            self._log_message(f"O IP do PC mudou para {current}. Atualize o "
                              f"{SERVER_FIELD[self.platform]} no celular.", "warn")

    def _set_candidates(self, candidates: list[network.Interface], notify_empty: bool) -> None:
        self.candidates = candidates
        if not candidates:
            self.selected = None
            self.iface_menu.configure(values=["nenhuma rede encontrada"], state="disabled")
            self.iface_menu.set("nenhuma rede encontrada")
            if notify_empty:
                self._notify("Nenhuma rede encontrada",
                             "O PC não está em nenhuma rede Wi-Fi ou cabeada. Conecte-o ao mesmo "
                             "Wi-Fi do celular e clique em Atualizar.", "warn")
        else:
            previous = self.selected.ip if self.selected else None
            self.selected = next((c for c in candidates if c.ip == previous), candidates[0])
            labels = [c.label for c in candidates]
            self.iface_menu.configure(values=labels,
                                      state="normal" if len(candidates) > 1 else "disabled")
            self.iface_menu.set(self.selected.label)
            if len(candidates) > 1 and notify_empty:
                self._log_message(f"{len(candidates)} redes encontradas. Confira se a selecionada "
                                  "em Rede é o mesmo Wi-Fi do celular.", "warn")
        self._update_address()

    def _on_iface_selected(self, label: str) -> None:
        self.selected = next((c for c in self.candidates if c.label == label), self.selected)
        self._update_address()

    # ---------------------------------------------------------------- exibição

    def _address(self) -> str | None:
        return f"{self.selected.ip}:{self.port}" if self.selected else None

    def _update_address(self) -> None:
        self._set_port_entry(self.port)
        self.server_value.configure(text=self.selected.ip if self.selected else "—")
        self.port_value.configure(text=str(self.port))
        if self._wait_hint_visible:
            self._fill_wait_hint()

    def _set_port_entry(self, port: int) -> None:
        state = self.port_entry.cget("state")
        self.port_entry.configure(state="normal")
        self.port_entry.delete(0, "end")
        self.port_entry.insert(0, str(port))
        self.port_entry.configure(state=state)

    def _on_platform_selected(self, platform: str) -> None:
        self.platform = platform
        self._update_mode_widgets()

    def _render_steps(self) -> None:
        for child in self.steps_frame.winfo_children():
            child.destroy()
        self._step_labels = []
        for i, step in enumerate(STEPS[self.platform]):
            ctk.CTkLabel(self.steps_frame, text=str(i + 1), width=22, height=22,
                         corner_radius=11, fg_color=FIELD, text_color=TEXT_2,
                         font=self.fonts["caption"]).grid(row=i, column=0, sticky="nw", pady=5)
            label = ctk.CTkLabel(self.steps_frame, text=step, font=self.fonts["body"],
                                 text_color=TEXT, anchor="w", justify="left", wraplength=300)
            label.grid(row=i, column=1, sticky="w", padx=(12, 0), pady=5)
            self._step_labels.append(label)

    def _update_mode_widgets(self) -> None:
        self._render_steps()
        self.server_caption.configure(text=SERVER_FIELD[self.platform])
        self.mode_help.configure(text=MODE_HELP[self.mode])
        self.inspect_label.configure(text=INSPECT_HELP[self.platform])
        if self.mode is ProxyMode.INSPECT:
            self.inspect_box.grid(row=2, column=0, sticky="ew", pady=(14, 0))
        else:
            self.inspect_box.grid_forget()
        if self._wait_hint_visible:
            self._fill_wait_hint()
        self._schedule_wraps()

    def _update_running_widgets(self) -> None:
        if self.controller.running:
            self.run_button.configure(text="Parar", fg_color=FIELD, hover_color=FIELD_HOVER,
                                      text_color=TEXT, border_width=1, border_color=BORDER)
        else:
            self.run_button.configure(text="Iniciar", fg_color=ACCENT, hover_color=ACCENT_HOVER,
                                      text_color=ON_ACCENT, border_width=0)
        self.port_entry.configure(state="disabled" if self.controller.running else "normal",
                                  text_color=TEXT_2 if self.controller.running else TEXT)

    def _set_status(self, text: str, color: tuple[str, str]) -> None:
        self.status_dot.set(color)
        self.status_label.configure(text=text, text_color=TEXT if color == ACCENT else TEXT_2)

    def _refresh_client_row(self) -> None:
        """Status "Celular" a partir do estado atual (chamado em eventos e a cada 5 s)."""
        if not self._proxy_active:
            if self._clients_seen:
                self.client_row.set("proxy parado · o celular fica sem internet", WARN)
            else:
                self.client_row.set("proxy parado", TEXT_3)
        elif not self._run_clients:
            if self._wait_hint_visible:
                self.client_row.set("nada chegou ainda · veja as dicas abaixo", WARN)
            else:
                self.client_row.set("aguardando conexão", TEXT_3)
        elif self._cert_rejected_run:
            self.client_row.set(f"{self._last_client} · recusou o certificado · veja ao lado", WARN)
        else:
            idle = time.monotonic() - self._last_activity
            if idle >= IDLE_S:
                self.client_row.set(f"sem tráfego há {int(idle // 60)} min · {self._last_client}",
                                    TEXT_3)
            else:
                self.client_row.set(f"conectado · {self._last_client}", ACCENT)

    def _tick(self) -> None:
        self._refresh_client_row()
        if not self._closing:
            self.after(TICK_MS, self._tick)

    # -------------------------------------------- dica quando o celular não conecta

    def _fill_wait_hint(self) -> None:
        if self.selected is None:
            return
        field = SERVER_FIELD[self.platform]
        # Curtas de propósito: cabem numa linha mesmo na largura mínima da janela.
        tips = [
            "Abra um site no celular: só há conexão com tráfego.",
            f"No mesmo Wi-Fi, o IP do celular deve {network.phone_ip_hint(self.selected)}",
            f"Proxy Manual: {field} {self.selected.ip}, Porta {self.port}.",
            "Rede de visitantes pode isolar os aparelhos.",
        ]
        if self.port != network.DEFAULT_PORT:
            tips.append(f"A porta {self.port} pode estar bloqueada; tente a {network.DEFAULT_PORT}.")
        for child in self.wait_tips.winfo_children():
            child.destroy()
        self._tip_labels = []
        for i, tip in enumerate(tips):
            # Duas colunas; marcador numa coluna própria para alinhar as linhas quebradas.
            row, col = divmod(i, 2)
            ctk.CTkLabel(self.wait_tips, text="•", font=self.fonts["small"], text_color=TEXT_3,
                         height=18).grid(row=row, column=col * 2, sticky="nw", pady=2,
                                         padx=(0 if col == 0 else 16, 0))
            label = ctk.CTkLabel(self.wait_tips, text=tip, font=self.fonts["small"],
                                 text_color=TEXT_2, justify="left", anchor="w", wraplength=400,
                                 height=18)
            label.grid(row=row, column=col * 2 + 1, sticky="nw", padx=(8, 0), pady=2)
            self._tip_labels.append(label)
        self._schedule_wraps()

    def _maybe_show_wait_hint(self, run_id: int) -> None:
        if run_id != self._run_id or not self._proxy_active or self._run_clients:
            return
        self._fill_wait_hint()
        self._wait_hint_visible = True
        # Sem tráfego ainda, o cabeçalho de colunas só ocupa espaço que as dicas precisam.
        self.log_columns.grid_forget()
        self.wait_box.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 4))
        self._refresh_client_row()

    def _hide_wait_hint(self) -> None:
        self._wait_hint_visible = False
        self.wait_box.grid_forget()
        self.log_columns.grid(row=2, column=0, sticky="ew", padx=26)

    # -------------------------------------------------------------------- log

    def _write_log(self, parts: list[tuple[str, str | None]]) -> None:
        self.log_box.configure(state="normal")
        for text, tag in parts:
            self.log_box.insert("end", text, tag or ())
        self.log_box.insert("end", "\n")
        lines = int(self.log_box.index("end-1c").split(".")[0])
        if lines > MAX_LOG_LINES:
            self.log_box.delete("1.0", f"{lines - MAX_LOG_LINES}.0")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _log_message(self, message: str, tag: str = "info") -> None:
        self._write_log([(f"{datetime.now():%H:%M:%S}  ", "muted"), (message, tag)])

    def _log_entry(self, entry: LogEntry) -> None:
        status = entry.status
        if "FALHA" in status or "ERRO" in status or "RECUSADO" in status:
            tag = "error"
        elif status.isdigit() and int(status) >= 400:
            tag = "warn"
        else:
            tag = "ok"
        self._write_log([
            (f"{entry.time:%H:%M:%S}  ", "muted"),
            (f"{entry.client_ip:<15}  ", "info"),
            (f"{status:<10}  ", tag),
            (entry.host, None),
        ])

    # ------------------------------------------------------------------ ações

    def _notify(self, title: str, message: str, tone: str = "info") -> None:
        """Aviso de um botão no tema do app (no lugar do messagebox do Windows)."""
        if not self._closing:
            ConfirmDialog(self, title=title, message=message, confirm="Entendi", tone=tone)

    def _toggle_running(self) -> None:
        if self.controller.running:
            self._stop()
        else:
            self._start()

    def _start(self) -> None:
        if self.controller.running:
            return
        if self.selected is None:
            self._notify("Escolha uma rede", "Selecione a rede do celular na lista Rede e "
                         "clique em Iniciar.", "warn")
            return
        text = self.port_entry.get().strip()
        if not text.isdigit() or not 1 <= int(text) <= 65535:
            self._notify("Porta inválida", f"\"{text}\" não é uma porta. Digite um número de "
                         "1 a 65535 (o padrão é 8083) e clique em Iniciar.", "error")
            return
        if int(text) != self.port:
            self.port = int(text)
            self._fixed_port = True
            self._update_address()
        if not network.is_port_free(self.port):
            if self._fixed_port:
                owner = network.port_owner(self.port)
                who = f" pelo {owner}" if owner else " por outro programa"
                self._notify(f"A porta {self.port} está em uso",
                             f"Ela está sendo usada{who}. Feche esse programa (no Burp: Proxy "
                             "settings → desmarque o listener) ou escolha outra porta e clique "
                             "em Iniciar.", "error")
                return
            try:
                self.port = network.find_free_port(self.port)
            except RuntimeError as exc:
                self._notify("Nenhuma porta livre", str(exc), "error")
                return
            self._log_message(f"Porta anterior ocupada; usando {self.port}.", "warn")
            self._update_address()
        self._auto_start_pending = False
        self.controller.start(ProxyConfig(port=self.port, mode=self.mode))
        self._set_status("Iniciando", WARN)
        self.client_row.set("iniciando...", TEXT_3)
        self._update_running_widgets()
        self._check_firewall()

    def _stop(self) -> None:
        self.controller.stop()
        self._update_running_widgets()

    def _on_mode_selected(self, label: str) -> None:
        mode = MODES[label]
        if mode is self.mode:
            return
        self.mode = mode
        self._update_mode_widgets()
        self._log_message(f"Modo alterado para {self.mode.label}.")
        if self.controller.running:
            self._stopping_for_restart = True
            self.controller.stop()
            self._start()

    def _copy(self) -> None:
        address = self._address()
        if address is None:
            return
        self.clipboard_clear()
        self.clipboard_append(address)
        self.copy_button.configure(text="Copiado", text_color=ACCENT)
        self.after(COPIED_FEEDBACK_MS,
                   lambda: self.copy_button.configure(text="Copiar", text_color=TEXT_2))

    def _clear_log(self) -> None:
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    # --------------------------------------------------------------- firewall

    def _check_firewall(self) -> None:
        self.fw_ring.grid_forget()
        if self._fw_confirmed_port == self.port:
            self.fw_row.set("liberado · o celular conectou", ACCENT)
            return
        if not firewall.is_supported():
            self.fw_row.set("não se aplica neste sistema", TEXT_3)
            return
        port = self.port
        self.fw_row.set("verificando...", TEXT_3)

        def done(result: object) -> None:
            # O celular conectar é a prova definitiva; não sobrescrever com a checagem.
            if port != self.port or self._fw_confirmed_port == port:
                return
            if result is True:
                self.fw_row.set(f"porta {port} liberada", ACCENT)
            elif result is False:
                self.fw_row.set(f"porta {port} sem regra de entrada", WARN)
                self.fw_ring.grid(row=0, column=1, sticky="e")
            else:
                # Sem permissão para ler as regras da empresa: confirmamos na prática.
                self.fw_row.set("confirmado quando o celular conectar", TEXT_3)

        self._in_background(lambda: firewall.has_inbound_rule(port), done)

    def _confirm_firewall_by_client(self) -> None:
        self._fw_confirmed_port = self.port
        self.fw_ring.grid_forget()
        self.fw_row.set("liberado · o celular conectou", ACCENT)

    def _create_firewall_rule(self) -> None:
        port = self.port
        dialog = ConfirmDialog(
            self,
            title="Liberar a porta no firewall?",
            message=(f"Cria uma regra no Firewall do Windows liberando a porta {port} "
                     "só para a rede local.\n\n"
                     "É preciso permissão de administrador: o Windows vai pedir confirmação."),
            confirm="Liberar porta",
            cancel="Agora não",
        )
        self.wait_window(dialog)
        if not dialog.confirmed:
            return
        self.fw_button.configure(state="disabled")

        def done(result: object) -> None:
            self.fw_button.configure(state="normal")
            if isinstance(result, Exception):
                ok, msg = False, str(result)
            else:
                ok, msg = result  # type: ignore[misc]
            self._log_message(msg, "info" if ok else "error")
            if ok:
                self._notify("Porta liberada", msg)
            else:
                self._notify("Não foi possível liberar a porta", msg, "error")
            self._check_firewall()

        self._in_background(lambda: firewall.create_inbound_rule(port), done)

    # --------------------------------------------------------------- eventos

    def _poll(self) -> None:
        while True:
            try:
                call = self._ui_calls.get_nowait()
            except queue.Empty:
                break
            call()
        for _ in range(500):
            try:
                event = self.controller.events.get_nowait()
            except queue.Empty:
                break
            self._handle_event(event)
        if not self._closing:
            self.after(POLL_MS, self._poll)

    def _is_phone(self, ip: str) -> bool:
        return ip not in {"127.0.0.1", self.selected.ip if self.selected else ""}

    def _handle_event(self, event: ProxyEvent) -> None:
        if event.kind is EventKind.STARTED:
            self._proxy_active = True
            self._run_id += 1
            self._run_clients.clear()
            self._cert_rejected_run = False
            self._hide_wait_hint()  # nova rodada: os 30 s recomeçam
            self._set_status("Ativo", ACCENT)
            self._log_message(f"Proxy ativo na porta {self.port}, modo {self.mode.label}.")
            run_id = self._run_id
            self.after(WAIT_HINT_MS, lambda: self._maybe_show_wait_hint(run_id))
        elif event.kind is EventKind.STOPPED:
            self._proxy_active = False
            if self._stopping_for_restart:
                self._stopping_for_restart = False
            else:
                self._set_status("Parado", TEXT_3)
                self._hide_wait_hint()
                self._log_message("Proxy parado.")
        elif event.kind is EventKind.FAILED:
            self._proxy_active = False
            self._set_status("Falha", DANGER)
            self._hide_wait_hint()
            self._log_message(event.message, "error")
            self._notify("O proxy não iniciou", event.message, "error")
        elif event.kind is EventKind.CLIENT:
            if self._is_phone(event.message):
                self._run_clients.add(event.message)
                self._clients_seen.add(event.message)
                self._last_client = event.message
                self._last_activity = time.monotonic()
                self._hide_wait_hint()
                self._confirm_firewall_by_client()
                self._log_message(f"Celular conectado: {event.message}", "ok")
        elif event.kind is EventKind.LOG and event.entry is not None:
            self._log_entry(event.entry)
            if self._is_phone(event.entry.client_ip):
                was_idle = time.monotonic() - self._last_activity >= IDLE_S
                self._last_client = event.entry.client_ip
                self._last_activity = time.monotonic()
                if not was_idle:
                    self._update_running_widgets()
                    return
        elif event.kind is EventKind.CERT_REJECTED:
            if not self._cert_rejected_run:
                self._cert_rejected_run = True
                self._log_message(
                    f"O celular recusou o certificado do mitmproxy ({event.message}). Instale e "
                    "confie no certificado (instruções ao lado) ou volte para Passthrough. "
                    "Apps com certificate pinning sempre recusam.", "warn")
        self._refresh_client_row()
        self._update_running_widgets()

    def _on_close(self) -> None:
        if self._clients_seen:
            dialog = ConfirmDialog(
                self,
                title="Desligue o proxy no celular",
                message=("Com o Ponte fechado, o celular fica sem internet enquanto o proxy "
                         "estiver configurado nele.\n\n"
                         f"No {self.platform}: {PROXY_OFF_PATH[self.platform]}."),
                confirm="Fechar o Ponte",
                cancel="Voltar",
            )
            self.wait_window(dialog)
            if not dialog.confirmed:
                return
        self._shutdown()

    def _shutdown(self) -> None:
        self._closing = True
        try:
            self.controller.stop()
        finally:
            self.destroy()


def _close_splash() -> None:
    """Fecha a tela "Abrindo o Ponte..." do .exe (só existe no build com --splash)."""
    try:
        import pyi_splash  # type: ignore[import-not-found]
    except ImportError:
        return
    pyi_splash.close()


def run(port: int | None = None) -> None:
    """Abre a janela e bloqueia até ela ser fechada."""
    app = App(port)
    # O primeiro desenho da janela leva ~1-2 s; fechar a splash só depois dele evita
    # um intervalo com a tela vazia. ``after_idle`` entra na fila depois do desenho.
    app.after(100, lambda: app.after_idle(_close_splash))
    app.mainloop()
