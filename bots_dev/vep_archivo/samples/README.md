# vep_archivo / samples

Carpeta para archivos de entrada del bot **vep_archivo** en formato
F20001 (cabecera `01<VEP ...>` + registros `02<VEP ...>`).

Los archivos reales contienen CUITs y períodos fiscales, por lo que
**no se commitean**: este directorio se preserva vía `.gitignore`
con la regla `*` y la excepción para `README.md` y `.gitignore`.

## Convención de nombres

`<formulario>.cuit.<CUIT>.fecha.<YYYYMMDD>.nro.<NNN>de<N>.txt`

Ejemplo: `F20001.cuit.20127478946.fecha.20260511.nro.001de001.txt`

## Cómo se usa

La variable de entorno `VEP_FILE_PATH` apunta al archivo a procesar.
Por defecto apunta a `vep_test.txt` (ruta relativa al CWD), pero puede
apuntar a un archivo de muestra dentro de esta carpeta:

```bash
VEP_FILE_PATH=vep_archivo/samples/F20001.cuit.20127478946.fecha.20260511.nro.001de001.txt
```
