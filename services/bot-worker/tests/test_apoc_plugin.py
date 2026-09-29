from bot_worker.bots.apoc.plugin import ApocPlugin, buscar_en_base


def test_busca_ignora_encabezados_y_acepta_descripcion_y_tab_final():
    base = (
        "# Padrón de prueba\n"
        "# Encabezado sintético\n"
        "# CUIT,Fecha Condicion Apocrifo, Fecha Publicacion, Descripcion,\t\n"
        "20-12345678-9,01/02/2024,03/04/2024,Descripción sintética,\t\n"
    )
    resultado = buscar_en_base("20123456789", base)
    assert resultado == {
        "cuit": "20123456789",
        "fecha_condicion": "01/02/2024",
        "fecha_publicacion": "03/04/2024",
        "apoc": True,
    }


def test_busca_base_con_bom():
    base = "\ufeff# encabezado\n20-12345678-9,fecha A,fecha B,detalle,\t\n"
    assert buscar_en_base("20 12345678 9", base)["apoc"] is True


def test_cuit_ausente_devuelve_falso():
    resultado = buscar_en_base("20123456789", "# encabezado\n27-98765432-1,x,y,detalle,\t\n")
    assert resultado == {
        "cuit": "20123456789",
        "fecha_condicion": None,
        "fecha_publicacion": None,
        "apoc": False,
    }


def test_base_grande_sintetica():
    filas = [f"20-{numero:08d}-1,fecha A,fecha B,detalle sintético,\t" for numero in range(50_000)]
    base = "# encabezado\n" + "\n".join(filas)
    resultado = buscar_en_base("20-00049999-1", base)
    assert resultado["apoc"] is True
    assert resultado["cuit"] == "20000499991"


def test_configure_guarda_texto_sin_copiarlo_a_workdir():
    texto = "# base sintética\n"
    plugin = ApocPlugin().configure({"apoc_base_text": texto})
    assert plugin._base_text is texto
