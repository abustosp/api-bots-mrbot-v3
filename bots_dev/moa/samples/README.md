# moa / samples

Carpeta para los listados planos (uno por línea) que consume **moa_bot**.

Los archivos reales contienen CUITs, por lo que **no se commitean**:
este directorio se preserva vía `.gitignore` con la regla `*` y la
excepción para `README.md` y `.gitignore`.

## Archivos esperados

- `despachos.txt` — un despacho por línea (formato `26086EC01000236M`).

## Variables de entorno relevantes

```bash
CLAVE_MOA=...
CUIT_REPRESENTANTE_MOA=...
CUIT_REPRESENTADO_MOA=...
```
