import asyncio
import csv
import json
import os
import time
from datetime import datetime
from pathlib import Path

from facturometro.logger.facturometro_bot_logger import bot_facturometro_logger

# Sanitizado: credenciales por variables de entorno (antes hardcodeadas).
CUITS = [
    {"cuit": os.getenv("FACTUROMETRO_CUIT_REF", ""), "clave": os.getenv("FACTUROMETRO_CLAVE_REF", ""), "representado": os.getenv("FACTUROMETRO_CUIT_REF", ""), "ref": True},
    {"cuit": os.getenv("FACTUROMETRO_CUIT_2", ""), "clave": os.getenv("FACTUROMETRO_CLAVE_2", ""), "representado": os.getenv("FACTUROMETRO_CUIT_2", "")},
    {"cuit": os.getenv("FACTUROMETRO_CUIT_3", ""), "clave": os.getenv("FACTUROMETRO_CLAVE_3", ""), "representado": os.getenv("FACTUROMETRO_CUIT_3", "")},
]

OUT_DIR = Path("./temp_facturometro_masivo")
OUT_DIR.mkdir(parents=True, exist_ok=True)


async def run_one(item):
    inicio = time.time()
    print(f"\n=== CUIT {item['cuit']} (ref={item.get('ref', False)}) ===")
    try:
        res = await bot_facturometro_logger(
            item["cuit"],
            item["clave"],
            item["representado"],
            headless=True,
        )
    except Exception as exc:
        res = {"monto": None, "tope": None, "categoria": None, "error": str(exc)}
        print(f"Excepcion: {exc}")
    duracion = round(time.time() - inicio, 1)
    estado = "OK" if res.get("monto") else "NO_DATA"
    print(f"Resultado {item['cuit']}: {res} | {duracion}s | {estado}")
    return {
        "cuit": item["cuit"],
        "es_referencia": bool(item.get("ref", False)),
        "monto": res.get("monto"),
        "tope": res.get("tope"),
        "categoria": res.get("categoria"),
        "estado": estado,
        "duracion_s": duracion,
        "timestamp": datetime.now().isoformat(),
    }


async def main():
    resultados = []
    for item in CUITS:
        r = await run_one(item)
        resultados.append(r)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = OUT_DIR / f"resumen_{ts}.csv"
    json_path = OUT_DIR / f"resumen_{ts}.json"

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(resultados[0].keys()))
        writer.writeheader()
        writer.writerows(resultados)

    json_path.write_text(json.dumps(resultados, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n\n=========== RESUMEN FINAL ===========")
    print(f"{'CUIT':<14}{'Ref':<5}{'Estado':<10}{'Monto':<20}{'Tope':<20}{'Categoria':<15}{'Dur(s)':<8}")
    for r in resultados:
        print(
            f"{r['cuit']:<14}"
            f"{'SI' if r['es_referencia'] else 'NO':<5}"
            f"{r['estado']:<10}"
            f"{str(r['monto'] or '-'):<20}"
            f"{str(r['tope'] or '-'):<20}"
            f"{str(r['categoria'] or '-'):<15}"
            f"{r['duracion_s']:<8}"
        )
    ok = sum(1 for r in resultados if r["estado"] == "OK")
    print(f"\nOK: {ok}/{len(resultados)}")
    print(f"CSV: {csv_path}")
    print(f"JSON: {json_path}")


if __name__ == "__main__":
    asyncio.run(main())
