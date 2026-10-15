

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
                    return {
                        "cuit": cuit_archivo,
                        "fecha_condicion": fecha_condicion,
                        "fecha_publicacion": fecha_publicacion,
                        "apoc": True
                    }
        
        # Si no se encuentra coincidencia
        return {
            "cuit": cuit,
            "fecha_condicion": None,
            "fecha_publicacion": None,
            "apoc": False
        }
    except FileNotFoundError:
        raise FileNotFoundError(f"No se encontró el archivo: {ruta_base}")
