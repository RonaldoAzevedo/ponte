"""Testes da detecção de interfaces e da escolha de porta (psutil e socket mockados)."""

from __future__ import annotations

import socket
from types import SimpleNamespace
from unittest import mock

import pytest

from vpnbridge import network


def addr(ip: str, family: int = socket.AF_INET, netmask: str = "255.255.255.0") -> SimpleNamespace:
    return SimpleNamespace(family=family, address=ip, netmask=netmask)


def stat(isup: bool = True) -> SimpleNamespace:
    return SimpleNamespace(isup=isup)


@pytest.fixture
def fake_psutil(monkeypatch: pytest.MonkeyPatch):
    """Instala interfaces falsas: ``fake_psutil({nome: ([ips], isup)})``."""

    def install(ifaces: dict[str, tuple[list[str], bool]]) -> None:
        monkeypatch.setattr(
            network.psutil, "net_if_addrs",
            lambda: {name: [addr(ip) for ip in ips] for name, (ips, _) in ifaces.items()},
        )
        monkeypatch.setattr(
            network.psutil, "net_if_stats",
            lambda: {name: stat(up) for name, (_, up) in ifaces.items()},
        )

    return install


# ---------------------------------------------------------------- IP privado

@pytest.mark.parametrize("ip", ["192.168.0.10", "10.0.0.1", "10.50.15.32", "172.16.0.1", "172.31.255.254"])
def test_private_lan_ips_accepted(ip: str) -> None:
    assert network.is_private_lan_ip(ip)


@pytest.mark.parametrize(
    "ip",
    ["172.15.0.1", "172.32.0.1", "8.8.8.8", "127.0.0.1", "169.254.1.1", "100.64.0.1", "fe80::1", "lixo"],
)
def test_non_lan_ips_rejected(ip: str) -> None:
    assert not network.is_private_lan_ip(ip)


# ---------------------------------------------------------- filtro de VPN

@pytest.mark.parametrize(
    "name, description",
    [
        ("tun0", ""),
        ("tap-windows", ""),
        ("utun3", ""),
        ("PANGP Virtual Ethernet Adapter", ""),
        ("Cisco AnyConnect", ""),
        ("Ethernet 2", "Cisco AnyConnect Virtual Miniport Adapter for Windows x64"),
        ("Ethernet 10", "Fortinet SSL VPN Virtual Ethernet Adapter"),
        ("Ethernet 3", "PANGP Virtual Ethernet Adapter"),
        ("GlobalProtect", ""),
        ("WireGuard Tunnel", ""),
        ("OpenVPN Data Channel Offload", ""),
        ("ppp0", ""),
    ],
)
def test_vpn_interfaces_detected(name: str, description: str) -> None:
    assert network.is_vpn_interface(name, description)


@pytest.mark.parametrize(
    "name, description",
    [("Wi-Fi", "Intel(R) Wi-Fi 6E AX211"), ("Ethernet", "Realtek PCIe GbE Family Controller"), ("en0", "")],
)
def test_lan_interfaces_not_vpn(name: str, description: str) -> None:
    assert not network.is_vpn_interface(name, description)


def test_candidates_exclude_vpn_by_description(fake_psutil) -> None:
    """Caso real: o adaptador Fortinet se chama só "Ethernet 10" e tem IP 172.20.x."""
    fake_psutil({
        "Ethernet 10": (["172.20.10.4"], True),
        "Wi-Fi 4": (["10.50.15.32"], True),
    })
    descriptions = {
        "Ethernet 10": "Fortinet SSL VPN Virtual Ethernet Adapter",
        "Wi-Fi 4": "Intel(R) Wi-Fi 6E AX211 160MHz #2",
    }
    result = network.list_lan_candidates(descriptions)
    assert [(i.name, i.ip) for i in result] == [("Wi-Fi 4", "10.50.15.32")]


def test_candidates_exclude_vpn_by_name(fake_psutil) -> None:
    fake_psutil({
        "tun0": (["10.8.0.2"], True),
        "PANGP Virtual Ethernet Adapter": (["10.20.30.40"], True),
        "en0": (["192.168.1.20"], True),
    })
    assert [i.ip for i in network.list_lan_candidates({})] == ["192.168.1.20"]


def test_candidates_exclude_down_virtual_and_non_private(fake_psutil) -> None:
    fake_psutil({
        "Ethernet 8": (["192.168.0.50"], False),              # desconectada
        "vEthernet (Default Switch)": (["192.168.208.1"], True),  # Hyper-V
        "Loopback Pseudo-Interface 1": (["127.0.0.1"], True),
        "Conexão Local* 1": (["169.254.231.7"], True),        # link-local
        "Wi-Fi": (["192.168.0.10"], True),
    })
    assert [i.name for i in network.list_lan_candidates({})] == ["Wi-Fi"]


def test_candidates_ignore_ipv6(fake_psutil, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(network.psutil, "net_if_addrs",
                        lambda: {"Wi-Fi": [addr("fe80::1", socket.AF_INET6), addr("192.168.0.10")]})
    monkeypatch.setattr(network.psutil, "net_if_stats", lambda: {"Wi-Fi": stat()})
    assert [i.ip for i in network.list_lan_candidates({})] == ["192.168.0.10"]


def test_multiple_candidates_wifi_first(fake_psutil) -> None:
    fake_psutil({
        "Ethernet": (["10.0.0.5"], True),
        "Wi-Fi": (["192.168.0.10"], True),
    })
    result = network.list_lan_candidates({})
    assert [i.name for i in result] == ["Wi-Fi", "Ethernet"]
    assert [i.kind for i in result] == ["Wi-Fi", "Ethernet"]


def test_no_candidates(fake_psutil) -> None:
    fake_psutil({"tun0": (["10.8.0.2"], True)})
    assert network.list_lan_candidates({}) == []


def test_candidates_fetch_descriptions_when_not_given(fake_psutil, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_psutil({"Ethernet 3": (["10.1.1.1"], True), "Wi-Fi": (["192.168.0.10"], True)})
    monkeypatch.setattr(network, "get_adapter_descriptions",
                        lambda: {"Ethernet 3": "PANGP Virtual Ethernet Adapter"})
    assert [i.name for i in network.list_lan_candidates()] == ["Wi-Fi"]


def test_candidates_keep_netmask(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(network.psutil, "net_if_addrs",
                        lambda: {"Wi-Fi": [addr("10.50.15.32", netmask="255.255.252.0")]})
    monkeypatch.setattr(network.psutil, "net_if_stats", lambda: {"Wi-Fi": stat()})
    [iface] = network.list_lan_candidates({})
    assert iface.netmask == "255.255.252.0"


def test_label_shows_name_then_ip() -> None:
    assert network.Interface("Wi-Fi 4", "192.168.15.9").label == "Wi-Fi 4 · 192.168.15.9"


# ------------------------------------------------- dica do IP do celular

@pytest.mark.parametrize(
    "ip, netmask, expected",
    [
        ("192.168.15.9", "255.255.255.0", "começar com 192.168.15."),
        ("10.50.15.32", "255.255.255.0", "começar com 10.50.15."),
        ("172.20.10.2", "255.255.0.0", "começar com 172.20."),
        ("10.1.2.3", "255.0.0.0", "começar com 10."),
        ("10.50.13.32", "255.255.252.0", "estar na rede 10.50.12.0/22"),
        ("192.168.15.9", "", "começar com 192.168.15."),       # máscara desconhecida: /24
        ("192.168.15.9", "lixo", "começar com 192.168.15."),
    ],
)
def test_phone_ip_hint(ip: str, netmask: str, expected: str) -> None:
    assert network.phone_ip_hint(network.Interface("Wi-Fi", ip, netmask=netmask)) == expected


# ------------------------------------------------------ VPN ativa ou não

def test_active_vpn_found(fake_psutil) -> None:
    fake_psutil({"Ethernet 10": (["172.20.10.4"], True), "Wi-Fi": (["192.168.0.10"], True)})
    vpns = network.active_vpn_interfaces({"Ethernet 10": "Fortinet SSL VPN Virtual Ethernet Adapter"})
    assert [(i.name, i.ip) for i in vpns] == [("Ethernet 10", "172.20.10.4")]


def test_vpn_down_or_link_local_counts_as_disconnected(fake_psutil) -> None:
    fake_psutil({
        "Ethernet 9": (["169.254.11.88"], True),  # adaptador existe, mas sem IP real
        "tun0": (["10.8.0.2"], False),            # interface desligada
        "Wi-Fi": (["192.168.0.10"], True),
    })
    descriptions = {"Ethernet 9": "Fortinet Virtual Ethernet Adapter (NDIS 6.30)"}
    assert network.active_vpn_interfaces(descriptions) == []


def test_snapshot_queries_windows_once(fake_psutil, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_psutil({"Ethernet 10": (["172.20.10.4"], True), "Wi-Fi": (["192.168.0.10"], True)})
    calls = []
    monkeypatch.setattr(network, "get_adapter_descriptions",
                        lambda: calls.append(1) or {"Ethernet 10": "Fortinet SSL VPN Virtual Ethernet Adapter"})
    lans, vpns = network.snapshot()
    assert [i.name for i in lans] == ["Wi-Fi"]
    assert [i.name for i in vpns] == ["Ethernet 10"]
    assert len(calls) == 1


# ---------------------------------------------- descrições (PowerShell)

def test_adapter_descriptions_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(network.sys, "platform", "win32")
    payload = '[{"Name":"Wi-Fi 4","InterfaceDescription":"Intel"},{"Name":"6to4","InterfaceDescription":null}]'
    fake = mock.Mock(return_value=SimpleNamespace(stdout=payload.encode("utf-8")))
    monkeypatch.setattr(network.subprocess, "run", fake)
    assert network.get_adapter_descriptions() == {"Wi-Fi 4": "Intel", "6to4": ""}


def test_adapter_descriptions_single_object(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(network.sys, "platform", "win32")
    payload = b'{"Name":"Wi-Fi","InterfaceDescription":"Intel"}'
    monkeypatch.setattr(network.subprocess, "run", mock.Mock(return_value=SimpleNamespace(stdout=payload)))
    assert network.get_adapter_descriptions() == {"Wi-Fi": "Intel"}


def test_adapter_descriptions_failure_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(network.sys, "platform", "win32")
    monkeypatch.setattr(network.subprocess, "run", mock.Mock(side_effect=OSError("sem powershell")))
    assert network.get_adapter_descriptions() == {}


def test_adapter_descriptions_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(network.sys, "platform", "darwin")
    assert network.get_adapter_descriptions() == {}


# ------------------------------------------------------------------ porta

class FakeSocket:
    """Socket falso: ``bind`` falha para as portas em ``busy``."""

    busy: set[int] = set()
    bound: list[int] = []

    def __init__(self, *args: object) -> None:
        pass

    def __enter__(self) -> "FakeSocket":
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def setsockopt(self, *args: object) -> None:
        pass

    def bind(self, address: tuple[str, int]) -> None:
        FakeSocket.bound.append(address[1])
        if address[1] in FakeSocket.busy:
            raise OSError(10048, "Only one usage of each socket address")


@pytest.fixture
def fake_socket(monkeypatch: pytest.MonkeyPatch) -> type[FakeSocket]:
    FakeSocket.busy = set()
    FakeSocket.bound = []
    monkeypatch.setattr(network.socket, "socket", FakeSocket)
    return FakeSocket


def test_default_port_when_free(fake_socket: type[FakeSocket]) -> None:
    assert network.find_free_port() == 8083
    assert fake_socket.bound == [8083]


def test_next_port_when_default_busy(fake_socket: type[FakeSocket]) -> None:
    fake_socket.busy = {8083, 8084}
    assert network.find_free_port() == 8085
    assert fake_socket.bound == [8083, 8084, 8085]


def test_is_port_free(fake_socket: type[FakeSocket]) -> None:
    fake_socket.busy = {9000}
    assert not network.is_port_free(9000)
    assert network.is_port_free(9001)


def test_no_free_port_raises(fake_socket: type[FakeSocket]) -> None:
    fake_socket.busy = set(range(8080, 8090))
    with pytest.raises(RuntimeError, match="Nenhuma porta livre entre 8080 e 8089"):
        network.find_free_port(8080, attempts=10)


def test_port_search_stops_at_65535(fake_socket: type[FakeSocket]) -> None:
    fake_socket.busy = {65534, 65535}
    with pytest.raises(RuntimeError):
        network.find_free_port(65534, attempts=10)
    assert fake_socket.bound == [65534, 65535]


def test_real_socket_detects_port_bound_to_specific_ip() -> None:
    """Caso do Burp: escuta em <IP>:porta não pode passar como livre em 0.0.0.0."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        busy = sock.getsockname()[1]
        assert not network.is_port_free(busy)


def test_real_socket_detects_busy_port() -> None:
    """Sanidade sem mock: uma porta em uso é pulada."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("0.0.0.0", 0))
        sock.listen()
        busy = sock.getsockname()[1]
        assert not network.is_port_free(busy)
        assert network.find_free_port(busy) != busy
