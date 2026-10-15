import requests
import os
import logging
from datetime import datetime
from pathlib import Path
from shared.proxy import get_proxy_config

API_CONSULTA_CUIT_URL = os.getenv("API_CONSULTA_CUIT_URL", "https://api-constancias-de-inscripcion.mrbot.com.ar/consulta_constancia/")
API_CONSULTA_CUIT_MASIVA_URL = os.getenv("API_CONSULTA_CUIT_MASIVA_URL", "https://api-constancias-de-inscripcion.mrbot.com.ar/consulta_constancia_masiva/")
API_CONSULTA_CUIT_USUARIO = os.getenv("API_CONSULTA_CUIT_USUARIO")
API_CONSULTA_CUIT_KEY = os.getenv("API_CONSULTA_CUIT_KEY")

class DebugCapture:
    def __init__(self, base_dir: str = "temp", prefix: str = "consulta_cuit") -> None:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = Path(base_dir) / f"{prefix}_{run_id}"
        self.logs_dir = self.run_dir / "logs"
        for path in (self.logs_dir,):
            path.mkdir(parents=True, exist_ok=True)

        self.logger = logging.getLogger(f"{prefix}.{run_id}")
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            handler = logging.FileHandler(self.logs_dir / f"{prefix}.log")
            formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.propagate = False
        self.logger.info("run_dir=%s", self.run_dir)

    def log(self, message: str) -> None:
        self.logger.info("%s", message)

def consulta_cuit(cuit: str, proxy_request: bool = False) -> dict:
    debug = DebugCapture(prefix="consulta_cuit")
    debug.log(f"Consulta Individual CUIT: {cuit}")
    
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
            if 'server' in conf:
                srv = conf['server']
                if 'username' in conf and 'password' in conf:
                    auth = f"{conf['username']}:{conf['password']}@"
                    p = f"http://{auth}{srv}"
                else:
                    p = f"http://{srv}"
                proxies = {'http': p, 'https': p}
                debug.log(f"Usando Proxy: {srv}")

    try:
        debug.log(f"Request GET to {API_CONSULTA_CUIT_URL}")
        response = requests.get(API_CONSULTA_CUIT_URL, params=params, proxies=proxies)
        debug.log(f"Status: {response.status_code}")
        response.raise_for_status()
        data = response.json()
        debug.log("Success")
        return data
    except Exception as e:
        debug.log(f"Error: {e}")
        raise
        
def consulta_cuit_masiva(cuits: list, proxy_request: bool = False) -> dict:
    debug = DebugCapture(prefix="consulta_cuit_masiva")
    debug.log(f"Consulta Masiva CUITs: {len(cuits)}")

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

    try:
        response = requests.post(API_CONSULTA_CUIT_MASIVA_URL, params=params, json=payload, proxies=proxies)
        debug.log(f"Status: {response.status_code}")
        response.raise_for_status()
        data = response.json()
        debug.log("Success")
        return data
    except Exception as e:
        debug.log(f"Error: {e}")
        raise

async def bot_consulta_cuit_logger(cuit: str) -> dict:
    """
    Async wrapper for compatibility with run_bots.py
    """
    try:
        # Run sync function
        return consulta_cuit(cuit)
    except Exception as e:
        return {"error": str(e)}

async def main():
    try: from dotenv import load_dotenv; load_dotenv()
    except: pass
    
    cuit = os.getenv("CUIT_INDIVIDUAL", "")  # Sanitizado: sin CUIT por defecto hardcodeado
    if cuit:
        res = await bot_consulta_cuit_logger(cuit)
        print("Result:", res)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
