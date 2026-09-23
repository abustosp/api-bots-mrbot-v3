"""Utilidades stdlib-only de inventario de workers.

La central SOLO conoce a los workers por su dirección ``"ip:port"``.
Este módulo no depende de FastAPI/Pydantic para poder probarse aislado.
"""


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
