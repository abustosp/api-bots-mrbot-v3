import logging
import os
from datetime import datetime
from pathlib import Path

def _safe_step_name(step: str) -> str:
    import re
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", step).strip("_")
    return safe or "step"

class DebugCapture:
    def __init__(self, base_dir: str = "temp", prefix: str = "apoc") -> None:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = Path(base_dir) / f"{prefix}_{run_id}"
        self.logs_dir = self.run_dir / "logs"
        self.screenshots_dir = self.run_dir / "screenshots"
        self.html_dir = self.run_dir / "html"
        for path in (self.logs_dir, self.screenshots_dir, self.html_dir):
            path.mkdir(parents=True, exist_ok=True)

        self.logger = logging.getLogger(f"{prefix}.{run_id}")
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            handler = logging.FileHandler(self.logs_dir / f"{prefix}.log")
            formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.propagate = False

        self.step = 0
        self.logger.info("run_dir=%s", self.run_dir)

    def log(self, message: str) -> None:
        self.logger.info("%s", message)

def buscar_cuit_en_base_apoc(
    cuit: str, 
    ruta_base: str = "base.txt") -> dict:
    """
    Busca un CUIT en el archivo base.txt con estructura:
    CUIT, Fecha Condicion Apocrifo, Fecha Publicacion, Descripcion

    Retorna un diccionario con los campos:
      - cuit
      - fecha_condicion
      - fecha_publicacion
      - apoc (bool)
    """
    debug = DebugCapture(prefix="apoc")
    debug.log(f"Iniciando busqueda de CUIT: {cuit} en {ruta_base}")
    
    try:
        with open(ruta_base, "r", encoding="utf-8") as f:
            for linea in f:
                # Ignorar comentarios o líneas vacías
                if not linea.strip() or linea.startswith("#"):
                    continue
                
                partes = linea.strip().split(",")
                if len(partes) < 3:
                    continue
                
                cuit_archivo = partes[0].strip()
                fecha_condicion = partes[1].strip()
                fecha_publicacion = partes[2].strip()

                if cuit_archivo == cuit:
                    debug.log(f"CUIT encontrado: {cuit_archivo}")
                    return {
                        "cuit": cuit_archivo,
                        "fecha_condicion": fecha_condicion,
                        "fecha_publicacion": fecha_publicacion,
                        "apoc": True
                    }
        
        # Si no se encuentra coincidencia
        debug.log(f"CUIT no encontrado: {cuit}")
        return {
            "cuit": cuit,
            "fecha_condicion": None,
            "fecha_publicacion": None,
            "apoc": False
        }
    except FileNotFoundError:
        debug.log(f"Archivo no encontrado: {ruta_base}")
        raise FileNotFoundError(f"No se encontró el archivo: {ruta_base}")
    except Exception as e:
        debug.log(f"Error general: {e}")
        raise

async def bot_apoc_logger(cuit: str) -> dict:
    """
    Async wrapper for compatibility with run_bots.py
    """
    # Create base.txt if not exists for testing
    if not os.path.exists("base.txt"):
        with open("base.txt", "w") as f:
            f.write("20123456789,01/01/2024,01/01/2024,Apoc Test\n")
            
    try:
        # Run sync function
        return buscar_cuit_en_base_apoc(cuit)
    except Exception as e:
        return {"error": str(e)}

async def main():
    try: from dotenv import load_dotenv; load_dotenv()
    except: pass
    
    cuit = os.getenv("APOC_CUIT", "20123456789")
    if cuit:
        res = await bot_apoc_logger(cuit)
        print("Result:", res)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
