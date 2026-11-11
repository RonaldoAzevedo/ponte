"""Testes do proxy: mensagens de erro (o que aconteceu + o que fazer) e certificado recusado."""

import queue
from types import SimpleNamespace

import pytest

from vpnbridge.proxy import BridgeAddon, EventKind, ProxyMode, _translate_error


@pytest.mark.parametrize(
    "raw",
    ["[Errno 10048] Only one usage of each socket address", "OSError: address already in use"],
)
def test_port_in_use(raw: str) -> None:
    message = _translate_error(raw, 8083)
    assert "porta 8083 está em uso" in message
    assert "clique em Iniciar" in message


def test_permission_denied() -> None:
    message = _translate_error("[WinError 10013] An attempt was made to access a socket", 8083)
    assert "não deixou abrir a porta 8083" in message
    assert "Escolha outra porta" in message


def test_unknown_error_says_what_to_do_and_keeps_detail() -> None:
    message = _translate_error("boom inesperado", 8083)
    assert message.startswith("Não foi possível iniciar o proxy.")
    assert "Feche e abra o Ponte de novo" in message
    assert message.endswith("Detalhe técnico: boom inesperado")


# ------------------------------------------- certificado recusado (Inspeção)

def _tls_data(sni: str | None, server: tuple[str, int] | None) -> SimpleNamespace:
    client = SimpleNamespace(peername=("::ffff:192.168.0.40", 51000), sni=sni)
    return SimpleNamespace(conn=client, context=SimpleNamespace(server=SimpleNamespace(address=server)))


def test_rejected_certificate_is_logged_and_reported() -> None:
    events: queue.Queue = queue.Queue()
    BridgeAddon(events, ProxyMode.INSPECT).tls_failed_client(_tls_data("api.empresa.com.br", None))
    log, report = events.get_nowait(), events.get_nowait()
    assert log.kind is EventKind.LOG
    assert (log.entry.client_ip, log.entry.host, log.entry.status) == (
        "192.168.0.40", "api.empresa.com.br", "RECUSADO")
    assert (report.kind, report.message) == (EventKind.CERT_REJECTED, "api.empresa.com.br")


def test_rejected_certificate_without_sni_uses_server_address() -> None:
    events: queue.Queue = queue.Queue()
    BridgeAddon(events, ProxyMode.INSPECT).tls_failed_client(_tls_data(None, ("10.0.0.5", 443)))
    assert events.get_nowait().entry.host == "10.0.0.5"
