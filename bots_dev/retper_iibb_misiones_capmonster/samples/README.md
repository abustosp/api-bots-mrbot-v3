# retper_iibb_misiones_capmonster / samples

Carpeta para el Excel de entrada del bot **retper_iibb_misiones_capmonster**.

Los archivos reales contienen CUITs y datos fiscales, por lo que
**no se commitean**: este directorio se preserva vía `.gitignore`
con la regla `*` y la excepción para `README.md` y `.gitignore`.

## Archivos esperados

- `Listado Mis Comprobantes 2.0.xlsx` — padrón de comprobantes a procesar.

## Variables de entorno relevantes

```bash
PROXY_USERNAME=...
PROXY_PASSWORD=...
RETPER_CAPMONSTER_API_KEY=...
RETPER_CAPMONSTER_TASK_TYPE=RecaptchaV2Task
```
