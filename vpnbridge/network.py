"""Detecção de interfaces de rede e escolha de porta.

O celular precisa do IP do PC na rede local (Wi-Fi/Ethernet), nunca o IP do
túnel da VPN. No Windows o nome amigável do adaptador de VPN costuma ser
genérico ("Ethernet 10"), por isso a filtragem também olha a descrição do
adaptador ("Fortinet SSL VPN Virtual Ethernet Adapter").
"""

from __future__ import annotations

import ipaddress
import json
import socket
import subprocess
import sys
from dataclasses import dataclass

import psutil

DEFAULT_PORT = 8083
"""Porta padrão. Fica fora da 8080, que costuma estar ocupada por outros proxies (Burp, Fiddler)."""

VPN_KEYWORDS: tuple[str, ...] = (
    "tun",
    "tap",
    "utun",
    "pangp",
    "cisco",
    "anyconnect",
    "fortinet",
    "forticlient",
    "globalprotect",
    "wireguard",
    "openvpn",
    "ppp",
    "vpn",
)
"""Trechos (minúsculos) que identificam adaptadores de VPN no nome ou na descrição."""

VIRTUAL_KEYWORDS: tuple[str, ...] = (
    "loopback",
    "vethernet",
    "hyper-v",
    "virtualbox",
    "vmware",
    "wsl",
    "docker",
    "wi-fi direct",
    "bluetooth",
)
"""Adaptadores virtuais/locais que o celular não consegue alcançar."""

PRIVATE_NETWORKS: tuple[ipaddress.IPv4Network, ...] = (
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
)


@dataclass(frozen=True)
class Interface:
    """Uma interface IPv4 candidata a receber conexões do celular."""

    name: str
    ip: str
    description: str = ""
    netmask: str = ""

    @property
    def kind(self) -> str:
        """Tipo provável da interface, só para exibição e ordenação."""
        text = f"{self.name} {self.description}".lower()
        if "wi-fi" in text or "wifi" in text or "wireless" in text or "wlan" in text:
            return "Wi-Fi"
        if "ethernet" in text or "gbe" in text:
            return "Ethernet"
        return "Outra"

    @property
    def label(self) -> str:
        """Texto curto para listas de seleção: "Wi-Fi 4 · 192.168.15.9"."""
        return f"{self.name} · {self.ip}"


def phone_ip_hint(iface: Interface) -> str:
    """Como reconhecer, pelo IP, um celular na mesma rede que ``iface``.

    Completa a frase "o IP do celular deve ...". Sem máscara conhecida, assume /24,
    o caso de quase todo Wi-Fi doméstico e hotspot.
    """
    try:
        net = ipaddress.IPv4Interface(f"{iface.ip}/{iface.netmask or '255.255.255.0'}").network
    except ValueError:
        net = ipaddress.IPv4Interface(f"{iface.ip}/24").network
    if net.prefixlen in (8, 16, 24):
        octets = str(net.network_address).split(".")[: net.prefixlen // 8]
        return f"começar com {'.'.join(octets)}."
    return f"estar na rede {net.with_prefixlen}"


def is_private_lan_ip(ip: str) -> bool:
    """Retorna True se ``ip`` é IPv4 em 10/8, 172.16/12 ou 192.168/16."""
    try:
        addr = ipaddress.IPv4Address(ip)
    except ValueError:
        return False
    return any(addr in net for net in PRIVATE_NETWORKS)


def _matches(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(k in lowered for k in keywords)


def is_vpn_interface(name: str, description: str = "") -> bool:
    """Retorna True se o nome ou a descrição do adaptador indicam uma VPN."""
    return _matches(name, VPN_KEYWORDS) or _matches(description, VPN_KEYWORDS)


def is_virtual_interface(name: str, description: str = "") -> bool:
    """Retorna True para adaptadores virtuais locais (Hyper-V, WSL, loopback...)."""
    return _matches(name, VIRTUAL_KEYWORDS) or _matches(description, VIRTUAL_KEYWORDS)


def get_adapter_descriptions() -> dict[str, str]:
    """Mapeia nome do adaptador → descrição do driver (apenas Windows).

    Usa ``Get-NetAdapter``. Em qualquer falha devolve dicionário vazio e a
    filtragem segue só pelo nome.
    """
    if sys.platform != "win32":
        return {}
    script = (
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
        "Get-NetAdapter -IncludeHidden | Select-Object Name,InterfaceDescription"
        " | ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        data = json.loads(result.stdout.decode("utf-8", errors="replace") or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    if isinstance(data, dict):
        data = [data]
    return {
        item.get("Name", ""): item.get("InterfaceDescription") or ""
        for item in data
        if isinstance(item, dict)
    }


def _ipv4_addresses(addrs: list) -> list[tuple[str, str]]:
    """Pares (IP, máscara) IPv4 de um adaptador do psutil."""
    return [(a.address, getattr(a, "netmask", None) or "")
            for a in addrs if a.family == socket.AF_INET]


def _sort_key(iface: Interface) -> tuple[int, str]:
    order = {"Wi-Fi": 0, "Ethernet": 1}
    return order.get(iface.kind, 2), iface.name


def list_lan_candidates(descriptions: dict[str, str] | None = None) -> list[Interface]:
    """Lista interfaces ativas com IP privado de LAN, sem VPN nem virtuais.

    Args:
        descriptions: nome → descrição do adaptador. Se ``None``, é obtido via
            :func:`get_adapter_descriptions`.

    Returns:
        Candidatas ordenadas: Wi-Fi primeiro, depois Ethernet, depois o resto.
    """
    if descriptions is None:
        descriptions = get_adapter_descriptions()
    stats = psutil.net_if_stats()
    candidates: list[Interface] = []
    for name, addrs in psutil.net_if_addrs().items():
        stat = stats.get(name)
        if stat is not None and not stat.isup:
            continue
        description = descriptions.get(name, "")
        if is_vpn_interface(name, description) or is_virtual_interface(name, description):
            continue
        for ip, netmask in _ipv4_addresses(addrs):
            if is_private_lan_ip(ip):
                candidates.append(Interface(name, ip, description, netmask))
    return sorted(candidates, key=_sort_key)


def active_vpn_interfaces(descriptions: dict[str, str] | None = None) -> list[Interface]:
    """Lista interfaces de VPN ativas e com IPv4 (exceto link-local 169.254.x.x).

    Lista vazia sugere que a VPN está desconectada.
    """
    if descriptions is None:
        descriptions = get_adapter_descriptions()
    stats = psutil.net_if_stats()
    found: list[Interface] = []
    for name, addrs in psutil.net_if_addrs().items():
        stat = stats.get(name)
        if stat is not None and not stat.isup:
            continue
        description = descriptions.get(name, "")
        if not is_vpn_interface(name, description):
            continue
        for ip, netmask in _ipv4_addresses(addrs):
            if not ipaddress.IPv4Address(ip).is_link_local:
                found.append(Interface(name, ip, description, netmask))
    return found


def snapshot() -> tuple[list[Interface], list[Interface]]:
    """Redes locais candidatas e VPNs ativas, com uma única consulta ao Windows.

    Usado na verificação periódica: detecta tanto a VPN caindo quanto o IP do PC mudando.
    """
    descriptions = get_adapter_descriptions()
    return list_lan_candidates(descriptions), active_vpn_interfaces(descriptions)


def is_port_free(port: int, host: str = "0.0.0.0") -> bool:
    """Tenta abrir ``host:port`` em TCP; True se a porta está livre.

    No Windows usa ``SO_EXCLUSIVEADDRUSE``: sem isso o bind em 0.0.0.0 funciona
    mesmo com outro programa (ex.: Burp) escutando em ``<IP da LAN>:porta`` — e
    esse outro programa recebe as conexões do celular.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
            if exclusive is not None:
                sock.setsockopt(socket.SOL_SOCKET, exclusive, 1)
            sock.bind((host, port))
        except OSError:
            return False
    return True


def port_owner(port: int) -> str | None:
    """Nome do processo que escuta em TCP ``port``, se for possível descobrir."""
    try:
        for conn in psutil.net_connections(kind="tcp"):
            if conn.status == psutil.CONN_LISTEN and conn.laddr and conn.laddr.port == port and conn.pid:
                return psutil.Process(conn.pid).name()
    except (psutil.Error, OSError):
        pass
    return None


def find_free_port(start: int = DEFAULT_PORT, attempts: int = 100, host: str = "0.0.0.0") -> int:
    """Retorna ``start`` se estiver livre, senão a próxima porta livre.

    Raises:
        RuntimeError: nenhuma porta livre em ``attempts`` tentativas.
    """
    for port in range(start, min(start + attempts, 65536)):
        if is_port_free(port, host):
            return port
    raise RuntimeError(
        f"Nenhuma porta livre entre {start} e {start + attempts - 1}. "
        "Feche outros proxies (Burp, Fiddler, Charles) e tente de novo."
    )
