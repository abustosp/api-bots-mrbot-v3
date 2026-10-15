# carga_libro_iva / samples

Carpeta para los Excels de entrada y salida del bot **carga_libro_iva**.

Los archivos reales contienen CUITs, períodos y montos, por lo que
**no se commitean**: este directorio se preserva vía `.gitignore`
con la regla `*` y la excepción para `README.md` y `.gitignore`.

## Archivos esperados

- `Lista libros SOS.xlsx` — entrada con el listado de libros a cargar
  (variable `CARGA_LIBRO_IVA_EXCEL`).
- `informe_carga_libro_iva.xlsx` — salida con el resultado de la carga
  (variable `CARGA_LIBRO_IVA_OUTPUT`).

## Variables de entorno relevantes

```bash
CARGA_LIBRO_IVA_EXCEL=carga_libro_iva/samples/Lista\ libros\ SOS.xlsx
CARGA_LIBRO_IVA_OUTPUT=carga_libro_iva/samples/informe_carga_libro_iva.xlsx
CARGA_LIBRO_IVA_HEADLESS=true
CARGA_LIBRO_IVA_MAX_WORKERS=1
CARGA_LIBRO_IVA_TIMEOUT=600
CARGA_LIBRO_IVA_USE_PROXY_PROPIA=false
```
