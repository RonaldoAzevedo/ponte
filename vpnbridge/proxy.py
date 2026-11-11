"""mitmproxy embutido rodando em thread própria com loop asyncio dedicado.

A GUI não conversa com o mitmproxy diretamente: tudo chega por uma
``queue.Queue`` de :class:`ProxyEvent`, consumida pela thread da interface.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import queue
import ssl
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import certifi
from mitmproxy import http, log, tls
from mitmproxy.connection import Client
from mitmproxy.options import Options
from mitmproxy.tools.dump import DumpMaster

UPSTREAM_CA_FILE = Path.home() / ".mitmproxy" / "vpnbridge-upstream-ca.pem"
_SERVER_AUTH = "1.3.6.1.5.5.7.3.1"


class ProxyMode(enum.Enum):
    """Modo de operação do proxy."""

    PASSTHROUGH = "passthrough"
    """Não decripta TLS: sem certificado no celular, não quebra pinning."""

    INSPECT = "inspecao"
    """Decripta TLS: exige o certificado do mitmproxy instalado e confiável no celular."""

    @property
    def label(self) -> str:
        return "Passthrough (sem decriptar)" if self is ProxyMode.PASSTHROUGH else "Inspeção (decripta TLS)"


class EventKind(enum.Enum):
    STARTED = "started"
    STOPPED = "stopped"
    FAILED = "failed"
    CLIENT = "client"
    LOG = "log"
    CERT_REJECTED = "cert_rejected"
    """Modo Inspeção: o celular recusou o certificado do mitmproxy (message = host)."""


@dataclass(frozen=True)
class LogEntry:
    """Uma linha do log em tempo real (nunca contém corpo de requisição)."""

    time: datetime
    client_ip: str
    host: str
    status: str

    def format(self) -> str:
        return f"{self.time:%H:%M:%S}  {self.client_ip:<15}  {self.status:<10}  {self.host}"


@dataclass(frozen=True)
class ProxyEvent:
    kind: EventKind
    message: str = ""
    entry: LogEntry | None = None


def _client_ip(client: Client) -> str:
    host = client.peername[0] if client.peername else "?"
    # Conexões IPv4 podem chegar como IPv6 mapeado (::ffff:192.168.0.10).
    return host.removeprefix("::ffff:")


def _host_of(flow: http.HTTPFlow) -> str:
    req = flow.request
    return f"{req.pretty_host}:{req.port}" if req.method == "CONNECT" else req.pretty_host


class BridgeAddon:
    """Addon do mitmproxy que publica conexões e um log resumido na fila."""

    def __init__(self, events: queue.Queue[ProxyEvent], mode: ProxyMode) -> None:
        self._events = events
        self._mode = mode
        self._seen_clients: set[str] = set()

    def _log(self, client: Client, host: str, status: str) -> None:
        entry = LogEntry(datetime.now(), _client_ip(client), host, status)
        self._events.put(ProxyEvent(EventKind.LOG, entry=entry))

    def running(self) -> None:
        self._events.put(ProxyEvent(EventKind.STARTED))

    def client_connected(self, client: Client) -> None:
        ip = _client_ip(client)
        if ip not in self._seen_clients:
            self._seen_clients.add(ip)
            self._events.put(ProxyEvent(EventKind.CLIENT, message=ip))

    def http_connected(self, flow: http.HTTPFlow) -> None:
        # No passthrough só existe o túnel CONNECT; na inspeção as requisições
        # internas aparecem em `response`, então o túnel não é logado.
        if self._mode is ProxyMode.PASSTHROUGH:
            self._log(flow.client_conn, _host_of(flow), "TÚNEL OK")

    def http_connect_error(self, flow: http.HTTPFlow) -> None:
        if flow.response is not None:
            status = f"{flow.response.status_code} FALHA"
        else:
            status = "FALHA"
        self._log(flow.client_conn, _host_of(flow), status)

    def response(self, flow: http.HTTPFlow) -> None:
        if flow.response is not None:
            self._log(flow.client_conn, _host_of(flow), str(flow.response.status_code))

    def error(self, flow: http.HTTPFlow) -> None:
        if flow.request.method != "CONNECT":
            self._log(flow.client_conn, _host_of(flow), "ERRO")

    def tls_failed_client(self, data: tls.TlsData) -> None:
        """Modo Inspeção: o celular não aceitou o certificado do mitmproxy.

        Sem isto a falha é silenciosa: o celular aparece "conectado" e nenhum
        HTTPS funciona. Causas: certificado não instalado/confiável ou pinning.
        """
        client = data.conn  # no hook do lado do cliente, sempre um Client
        server = data.context.server.address
        host = client.sni or (server[0] if server else "?")
        self._log(client, host, "RECUSADO")  # type: ignore[arg-type]
        self._events.put(ProxyEvent(EventKind.CERT_REJECTED, message=host))


class _ErrorCollector(log.MitmLogHandler):
    """Guarda mensagens de erro do mitmproxy para explicar falhas de inicialização."""

    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _translate_error(raw: str, port: int) -> str:
    """Mensagem para o usuário: o que aconteceu e o que fazer (o erro cru vai no fim)."""
    lowered = raw.lower()
    if "address already in use" in lowered or "10048" in lowered or "only one usage" in lowered:
        return (f"A porta {port} está em uso por outro programa, como o Burp. "
                "Feche esse programa ou escolha outra porta e clique em Iniciar.")
    if "10013" in lowered or "permission" in lowered or "access" in lowered:
        return (f"O Windows não deixou abrir a porta {port} (reservada ou bloqueada por política). "
                "Escolha outra porta e clique em Iniciar.")
    return (f"Não foi possível iniciar o proxy. Feche e abra o Ponte de novo; "
            f"se continuar, avise quem te enviou o app. Detalhe técnico: {raw}")


@dataclass
class ProxyConfig:
    port: int
    mode: ProxyMode = ProxyMode.PASSTHROUGH
    listen_host: str = "0.0.0.0"
    extra_options: dict[str, object] = field(default_factory=dict)


def build_upstream_ca_bundle(path: Path) -> Path | None:
    """Grava em ``path`` as CAs do ``certifi`` + as do repositório do Windows.

    Redes corporativas com inspeção TLS reassinam os sites com uma CA própria,
    que o Windows conhece mas o ``certifi`` (usado pelo mitmproxy) não. Sem
    isso, no modo inspeção todo HTTPS falha com "unable to get local issuer
    certificate". Retorna ``None`` fora do Windows ou em caso de erro.
    """
    if sys.platform != "win32":
        return None
    try:
        pems = [Path(certifi.where()).read_text(encoding="ascii")]
        for store in ("ROOT", "CA"):
            for cert, encoding, trust in ssl.enum_certificates(store):  # type: ignore[attr-defined]
                if encoding == "x509_asn" and (trust is True or _SERVER_AUTH in trust):
                    pems.append(ssl.DER_cert_to_PEM_cert(cert))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(pems), encoding="ascii")
    except (OSError, ssl.SSLError, ValueError):
        return None
    return path


def build_options(config: ProxyConfig) -> dict[str, object]:
    """Opções do mitmproxy para a configuração dada."""
    opts: dict[str, object] = {
        "listen_host": config.listen_host,
        "listen_port": config.port,
        "mode": ["regular"],
        "ignore_hosts": [".*"] if config.mode is ProxyMode.PASSTHROUGH else [],
        # Conecta no destino antes de responder ao CONNECT: falhas da VPN viram
        # "FALHA" no log em vez de um túnel aberto que morre em seguida.
        "connection_strategy": "eager",
    }
    if config.mode is ProxyMode.INSPECT:
        bundle = build_upstream_ca_bundle(UPSTREAM_CA_FILE)
        if bundle is not None:
            opts["ssl_verify_upstream_trusted_ca"] = str(bundle)
    opts.update(config.extra_options)
    return opts


class ProxyController:
    """Inicia e para o mitmproxy em uma thread de fundo.

    Todos os métodos públicos são chamados pela thread da GUI; os eventos são
    lidos de :attr:`events`.
    """

    def __init__(self) -> None:
        self.events: queue.Queue[ProxyEvent] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._master: DumpMaster | None = None
        self._lock = threading.Lock()
        self._stop_requested = threading.Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, config: ProxyConfig) -> None:
        """Sobe o proxy em background. O resultado chega como STARTED ou FAILED."""
        if self.running:
            raise RuntimeError("O proxy já está em execução.")
        self._stop_requested.clear()
        self._thread = threading.Thread(
            target=self._thread_main, args=(config,), name="mitmproxy", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Pede o encerramento do proxy e espera a thread terminar."""
        with self._lock:
            self._stop_requested.set()
            master = self._master
        if master is not None:
            master.shutdown()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def _thread_main(self, config: ProxyConfig) -> None:
        collector = _ErrorCollector()
        collector.install()
        failed = False
        try:
            asyncio.run(self._serve(config))
        except SystemExit:
            # O addon errorcheck do mitmproxy chama sys.exit(1) se a subida falhar.
            failed = True
        except Exception as exc:  # noqa: BLE001 - qualquer falha precisa chegar à GUI
            failed = True
            collector.messages.append(str(exc))
        finally:
            collector.uninstall()
            with self._lock:
                self._master = None

        if failed:
            raw = collector.messages[0] if collector.messages else "erro desconhecido"
            self.events.put(ProxyEvent(EventKind.FAILED, message=_translate_error(raw, config.port)))
        else:
            self.events.put(ProxyEvent(EventKind.STOPPED))

    async def _serve(self, config: ProxyConfig) -> None:
        opts = Options()
        master = DumpMaster(opts, with_termlog=False, with_dumper=False)
        master.addons.add(BridgeAddon(self.events, config.mode))
        opts.update(**build_options(config))
        with self._lock:
            self._master = master
            if self._stop_requested.is_set():
                return
        await master.run()
