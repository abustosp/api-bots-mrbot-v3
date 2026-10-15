import requests
import os
from shared.proxy import get_proxy_config

API_CONSULTA_CUIT_URL = os.getenv("API_CONSULTA_CUIT_URL", "https://api-constancias-de-inscripcion.mrbot.com.ar/consulta_constancia/")
API_CONSULTA_CUIT_MASIVA_URL = os.getenv("API_CONSULTA_CUIT_MASIVA_URL", "https://api-constancias-de-inscripcion.mrbot.com.ar/consulta_constancia_masiva/")
API_CONSULTA_CUIT_USUARIO = os.getenv("API_CONSULTA_CUIT_USUARIO")
API_CONSULTA_CUIT_KEY = os.getenv("API_CONSULTA_CUIT_KEY")

def consulta_cuit(cuit: str, proxy_request: bool = False) -> dict:
    """
    Consulta el CUIT en la API externa de constancias de inscripción.
    """
    params = {
        "cuit": cuit
    }
    if API_CONSULTA_CUIT_USUARIO:
        params["usuario"] = API_CONSULTA_CUIT_USUARIO
    if API_CONSULTA_CUIT_KEY:
        params["api_key"] = API_CONSULTA_CUIT_KEY
    
    proxies = None
    if proxy_request:
        conf = get_proxy_config()
        if conf:
            # requests proxies dict format
            # conf usually has {'server': ...} from playwright proxy
            # we need http/https keys
            if 'server' in conf:
                srv = conf['server']
                if 'username' in conf and 'password' in conf:
                    auth = f"{conf['username']}:{conf['password']}@"
                    # server likely doesn't have protocol, assume http
                    p = f"http://{auth}{srv}"
                else:
                    p = f"http://{srv}"
                proxies = {'http': p, 'https': p}

    response = requests.get(API_CONSULTA_CUIT_URL, params=params, proxies=proxies)

    response.raise_for_status()
    return response.json()
        
def consulta_cuit_masiva(cuits: list, proxy_request: bool = False) -> dict:
    """
    Consulta múltiples CUITs en la API externa de constancias de inscripción.
    """
    payload = {
        "cuits": cuits
    }
    params = {}
    if API_CONSULTA_CUIT_USUARIO:
        params["usuario"] = API_CONSULTA_CUIT_USUARIO
    if API_CONSULTA_CUIT_KEY:
        params["api_key"] = API_CONSULTA_CUIT_KEY

    proxies = None
    if proxy_request:
        conf = get_proxy_config()
        if conf:
            if 'server' in conf:
                srv = conf['server']
                if 'username' in conf and 'password' in conf:
                    auth = f"{conf['username']}:{conf['password']}@"
                    p = f"http://{auth}{srv}"
                else:
                    p = f"http://{srv}"
                proxies = {'http': p, 'https': p}

    response = requests.post(API_CONSULTA_CUIT_MASIVA_URL, params=params, json=payload, proxies=proxies)

    response.raise_for_status()
    return response.json()
