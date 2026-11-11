"""Verificação e criação de regra de entrada no Firewall do Windows."""

from __future__ import annotations

import ctypes
import subprocess
import sys

RULE_PREFIX = "Ponte (proxy do celular)"

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def rule_name(port: int) -> str:
    """Nome da regra criada por este programa para ``port``."""
    return f"{RULE_PREFIX} TCP {port}"


def is_supported() -> bool:
    """O firewall só é gerenciado no Windows."""
    return sys.platform == "win32"


def is_admin() -> bool:
    """True se o processo atual roda como administrador."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False


def _run(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(args, capture_output=True, timeout=timeout, creationflags=_NO_WINDOW)


def has_inbound_rule(port: int) -> bool | None:
    """Diz se existe regra de entrada ativa que libera TCP em ``port``.

    Primeiro procura a regra criada por este programa (rápido); depois qualquer
    regra habilitada de entrada/permitir com essa porta local, incluindo as da
    política de grupo. Regras por programa (ex.: "BurpSuite.exe") não entram.

    Returns:
        True/False, ou ``None`` quando o Windows nega a leitura das regras
        (comum em contas sem administrador em PCs gerenciados).
    """
    if not is_supported():
        return True
    try:
        own = _run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={rule_name(port)}"])
        if own.returncode == 0:
            return True
        script = (
            "try {"
            " $f = Get-NetFirewallPortFilter -PolicyStore ActiveStore -Protocol TCP -ErrorAction Stop"
            f" | Where-Object {{ $_.LocalPort -contains '{port}' }};"
            " $r = $f | Get-NetFirewallRule -ErrorAction Stop | Where-Object {"
            " $_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' -and $_.Action -eq 'Allow' };"
            " if ($r) { exit 0 } else { exit 1 }"
            "} catch { exit 2 }"
        )
        other = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if other.returncode == 0:
        return True
    if other.returncode == 1:
        return False
    return None


def create_inbound_rule(port: int) -> tuple[bool, str]:
    """Cria a regra de entrada via ``netsh advfirewall`` (pede elevação/UAC).

    A regra aceita só a sub-rede local (``remoteip=localsubnet``), em todos os perfis.

    Returns:
        (sucesso, mensagem em português).
    """
    if not is_supported():
        return False, "Gerenciamento de firewall disponível apenas no Windows."
    netsh_args = [
        "advfirewall", "firewall", "add", "rule",
        f"name={rule_name(port)}",
        "dir=in", "action=allow", "protocol=TCP",
        f"localport={port}", "remoteip=localsubnet", "profile=any",
    ]
    try:
        if is_admin():
            result = _run(["netsh", *netsh_args])
        else:
            # Start-Process junta os argumentos sem aspas; o nome tem espaços.
            joined = " ".join(
                f'name="{rule_name(port)}"' if a.startswith("name=") else a for a in netsh_args
            )
            script = (
                f"try {{ $p = Start-Process netsh -ArgumentList '{joined}' -Verb RunAs"
                " -WindowStyle Hidden -Wait -PassThru; exit $p.ExitCode } catch { exit 1223 }"
            )
            result = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"Não foi possível executar o netsh: {exc}"

    if result.returncode == 1223:
        return False, "Criação da regra cancelada (permissão de administrador negada)."
    if result.returncode != 0:
        return False, (
            f"O netsh falhou (código {result.returncode}). Se o PC é gerenciado pela empresa, "
            "a política pode bloquear regras locais — peça ao suporte para liberar a porta."
        )
    if has_inbound_rule(port) is False:
        return False, (
            "A regra foi criada, mas não aparece como ativa. A política de grupo da empresa "
            "pode estar ignorando regras locais de firewall."
        )
    return True, f"Regra \"{rule_name(port)}\" criada: porta TCP {port} liberada para a rede local."
