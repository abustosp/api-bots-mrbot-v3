import os
from random import randint
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

server_proxy = os.getenv("SERVER_PROXY", "False").strip().lower() in ("1", "true", "yes", "y", "on")
server_proxy_host = os.getenv("SERVER_PROXY_HOST")
puerto_proxy_fijo = os.getenv("PUERTO_PROXY_FIJO", "False").strip().lower() in ("1", "true", "yes", "y", "on")
puerto_proxy_fijo_numero = int(os.getenv("PUERTO_PROXY_FIJO_NUMERO", 0))
puerto_proxy_min = os.getenv("PUERTO_PROXY_MINIMO")
puerto_proxy_max = os.getenv("PUERTO_PROXY_MAXIMO")
username_proxy = os.getenv("USERNAME_PROXY", "")
password_proxy = os.getenv("PASSWORD_PROXY", "")
proxy_country = os.getenv("PROXY_COUNTRY", "ar").strip().lower()

sticky_session = os.getenv("STICKY_SESSION", "False").strip().lower() in ("1", "true", "yes", "y", "on")
session_min = os.getenv("SESSION_MIN", "")
session_max = os.getenv("SESSION_MAX", "")

rotating_proxies = os.getenv("ROTATING_PROXIES", "False").strip().lower() in ("1", "true", "yes", "y", "on")
rotating_proxies_host = os.getenv("ROTATING_PROXIES_HOST", "")

datacenter_proxy = os.getenv("DATACENTER_PROXY", "False").strip().lower() in ("1", "true", "yes", "y", "on")
datacenter_proxy_username = os.getenv("DATACENTER_USERNAME") or os.getenv("DATACENTER_PROXY_USERNAME", "")
datacenter_proxy_password = os.getenv("DATACENTER_PASSWORD") or os.getenv("DATACENTER_PROXY_PASSWORD", "")
entry_point_proxy = os.getenv("ENTRY_POINT_PROXY", "")
starting_port_proxy = os.getenv("STARTING_PORT_PROXY", "")
ending_port_proxy = os.getenv("ENDING_PORT_PROXY", "")
proxy_debug = os.getenv("PROXY_DEBUG", "False").strip().lower() in ("1", "true", "yes", "y", "on")


def _normalize_customer_username(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return value
    if value.startswith("customer-"):
        return value
    return f"customer-{value}"


def _normalize_datacenter_username(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return value
    if value.startswith("user-"):
        return value
    if value.startswith("customer-"):
        return f"user-{value[len('customer-'):]}"
    return f"user-{value}"


def _ensure_country_suffix(value: str, country: str = "ar") -> str:
    if not value:
        return value
    if not country:
        return value
    if "-cc-" in value:
        return value
    return f"{value}-cc-{country}"


def _normalize_host(host: str) -> str:
    host = (host or "").strip()
    if not host:
        return host
    if host.startswith("http://"):
        host = host[len("http://"):]
    elif host.startswith("https://"):
        host = host[len("https://"):]
    return host.strip("/")


def _ensure_proxy_server(host: str, default_port: int = 7777) -> str:
    host = _normalize_host(host)
    if not host:
        return host
    if ":" not in host:
        host = f"{host}:{default_port}"
    return f"http://{host}"


def _ensure_proxy_server_with_scheme(host: str, port: int, scheme: str = "https") -> str:
    host = _normalize_host(host)
    if not host:
        return host
    if ":" in host:
        host = host.rsplit(":", 1)[0]
    return f"{scheme}://{host}:{port}"


def get_proxy_config():
    
    if rotating_proxies:
        base_username = _ensure_country_suffix(_normalize_customer_username(username_proxy), proxy_country)
        rotating_server = _ensure_proxy_server(rotating_proxies_host or server_proxy_host)
        if not rotating_server:
            raise ValueError("ROTATING_PROXIES_HOST o SERVER_PROXY_HOST requerido cuando ROTATING_PROXIES=True")
        proxy={
            "server": rotating_server,
            "username": base_username,
            "password": password_proxy
        }
        
    elif sticky_session:
        if not session_min or not session_max:
            raise ValueError("SESSION_MIN y SESSION_MAX son requeridos cuando STICKY_SESSION=True")
        session_number = randint(int(session_min), int(session_max))
        session_number = str(session_number).zfill(10)
        base_username = _ensure_country_suffix(_normalize_customer_username(username_proxy), proxy_country)
        proxy={
            "server": f"http://pr.oxylabs.io:7777",
            "username": f"{base_username}-sessid-{session_number}-sesstime-10",
            "password": password_proxy
        }
        
    elif datacenter_proxy:
        if not entry_point_proxy or not starting_port_proxy or not ending_port_proxy:
            raise ValueError("ENTRY_POINT_PROXY, STARTING_PORT_PROXY y ENDING_PORT_PROXY son requeridos cuando DATACENTER_PROXY=True")
        starting_port = int(starting_port_proxy)
        ending_port = int(ending_port_proxy)
        if not datacenter_proxy_username or not datacenter_proxy_password:
            raise ValueError("DATACENTER_USERNAME y DATACENTER_PASSWORD son requeridos cuando DATACENTER_PROXY=True")
        port_variable = randint(starting_port, ending_port)
        proxy={
            "server": _ensure_proxy_server_with_scheme(entry_point_proxy, port_variable, scheme="https"),
            "username": _normalize_datacenter_username(datacenter_proxy_username),
            "password": datacenter_proxy_password
        }

    else:

        if puerto_proxy_fijo:
            proxy={
                "server": f"http://{_normalize_host(server_proxy_host)}:{puerto_proxy_fijo_numero}",
                "username": username_proxy,
                "password": password_proxy
            }
        else:
            puerto_variable = randint(int(puerto_proxy_min), int(puerto_proxy_max))
            proxy={
                "server": f"http://{_normalize_host(server_proxy_host)}:{puerto_variable}",
                "username": username_proxy,
                "password": password_proxy
            }
            
    if not server_proxy:
        proxy = None
        
    if proxy_debug:
        if proxy is None:
            print("PROXY CONFIG: None (SERVER_PROXY=False)")
        else:
            safe_proxy = dict(proxy)
            if safe_proxy.get("password"):
                safe_proxy["password"] = "***"
            print(f"PROXY CONFIG: {safe_proxy}")

    return proxy
