"""Utilidades stdlib-only de inventario de workers.

La central SOLO conoce a los workers por su dirección ``"ip:port"``.
Este módulo no depende de FastAPI/Pydantic para poder probarse aislado.
"""


import ipaddress
import socket
import time

_IP_CACHE: dict[str, tuple[float, str]] = {}
_IP_TTL_SECONDS = 300.0


def parse_worker_aliases(raw: str) -> dict[str, str]:
    """Normaliza ``WORKER_ALIASES="host:port=alias,host:port=alias"`` a dict."""
    aliases: dict[str, str] = {}
    for item in (raw or "").split(","):
        nodo, sep, alias = item.partition("=")
        nodo, alias = nodo.strip(), alias.strip()
        if sep and nodo and alias:
            aliases[nodo] = alias
    return aliases


def node_host(node: str) -> str:
    """Host de ``host:port`` (soporta ``[ipv6]:port``)."""
    node = node.strip()
    if node.startswith("["):
        return node[1:].split("]", 1)[0]
    return node.rsplit(":", 1)[0] if ":" in node else node


def resolve_node_ip(node: str) -> str:
    """IP del nodo: el propio host si ya es IP, o resolución DNS con caché (vacío si falla)."""
    host = node_host(node)
    if not host:
        return ""
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    ahora = time.monotonic()
    cached = _IP_CACHE.get(host)
    if cached and ahora - cached[0] < _IP_TTL_SECONDS:
        return cached[1]
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        ips = [i[4][0] for i in infos]
        ip = next((i for i in ips if ":" not in i), ips[0] if ips else "")
    except OSError:
        ip = ""
    _IP_CACHE[host] = (ahora, ip)
    return ip


def parse_worker_nodes(raw: str) -> list[str]:
    """Normaliza ``WORKER_NODES="ip:port,ip:port"`` a lista sin duplicados."""
    nodes: list[str] = []
    for item in (raw or "").split(","):
        item = item.strip()
        if item and item not in nodes:
            nodes.append(item)
    return nodes


def merge_nodes(*lists: object) -> list[str]:
    """Une inventarios de nodos (env + panel admin) sin duplicados, en orden."""
    merged: list[str] = []
    for raw in lists:
        items = raw if isinstance(raw, (list, tuple, set)) else parse_worker_nodes(str(raw or ""))
        for item in items:
            if item and item not in merged:
                merged.append(item)
    return merged


def node_from_url(url: str) -> str:
    """Extrae ``ip:port`` de ``http(s)://ip:port[/...]``; falla si no es HTTP(S)."""
    rest = url.split("://", 1)
    if len(rest) != 2 or rest[0] not in ("http", "https"):
        raise ValueError("advertised_url debe ser http(s)://ip:port")
    host_port = rest[1].split("/", 1)[0].strip()
    if not host_port or ":" not in host_port:
        raise ValueError("advertised_url debe incluir ip:port")
    return host_port
