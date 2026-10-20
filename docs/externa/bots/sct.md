# Sistema de Cuentas (SCT)

**Propósito.** Consultar reportes de vencimientos, deudas y declaraciones juradas pendientes para un CUIT; seleccionar uno o más formatos disponibles.

## Operación y ruta V3

Catálogo: `sct`, operación `consultar`.

- Canónica: `POST /api/v3/bots/sct/consultar`.
- Alias V3: `POST /api/v3/sct/consulta`.

Cuerpo canónico: `{ "payload": { ... }, "credentials": { ... } }`; el alias de compatibilidad recibe los campos en un cuerpo plano. No mezclar ambas formas. La aceptación devuelve `job_id`; consultar estado/resultado en `/api/v3/jobs/{job_id}`. Convenciones compartidas de credenciales: [API pública](../api.md).

## Credenciales

Se requiere clave fiscal y CUIT representante en `credentials.clave` y `credentials.cuit_representante`. Se aceptan `clave_representante` o `contrasena` como nombres alternativos coincidentes y `clave_encriptada` como alternativa cifrada Base64. `cuit_inicio_sesion` equivale al CUIT representante. Si todas las claves faltan, son `null` o `""`, no hay acceso y el trabajo no puede ejecutarse. Para login se requiere un CUIT válido. No se envían claves como parámetros de consulta.

## Campos del payload

| Campo | Tipo, requerido y default | Efecto y tratamiento de omisión/null/vacío/false |
|---|---|---|
| `representado_cuit` | texto requerido; alias `cuit` | CUIT de 11 dígitos, normaliza espacios, guiones, puntos y `/`. Vacío/null inválidos. |
| `secciones` | lista de textos opcional; default: las tres secciones | Valores: `vencimientos`, `deudas`, `ddjj_pendientes`. Se recortan espacios, se ignora mayúsculas y se deduplica/ordena. Lista vacía/null o valor desconocido rechaza. En la API canónica, si se omite esta clave, la normalización agrega las tres secciones. |
| `formatos` | lista de textos opcional; default `['xlsx']` | Valores: `xlsx`, `csv`, `pdf`; se recortan espacios, normalizan a minúsculas, deduplican/ordenan. Lista vacía/null o valor desconocido rechaza. En la API canónica, si se omite esta clave, se agrega `['xlsx']`. |
| `incluir_json` | booleano, default `true` | `true` incluye el listado `reportes`; `false` lo omite. `null` inválido. |
| `subir` | booleano, default `false` | `true` publica archivos; `false` no publica. `null` inválido. En la API V3, si se omite `subir`, la normalización lo establece en `false`; el default efectivo API es `false`. Envíelo explícitamente para controlar la publicación. |
| `omitir_vacias` | booleano, default `true` | Omite una sección solo si el portal informa explícitamente cero elementos. `false` intenta descargarla de todos modos. Omitir = `true`; `null` inválido. |

La normalización de payload se aplica también a la ruta canónica. Si no se envían opciones de sección/formato, el comportamiento efectivo es consultar las tres secciones en formato XLSX; la publicación queda desactivada (`subir=false`). Para controlar selección y publicación, enviar explícitamente `secciones`, `formatos` y `subir`. Use los nombres canónicos y no mezcle grafías alternativas, pues una forma antigua puede eliminarse antes de validar.

Se exige al menos una salida: `incluir_json=true` o `subir=true`.

## Ejemplo ficticio válido

```json
{
  "payload": {
    "representado_cuit": "20-12345678-6",
    "secciones": ["vencimientos", "deudas"],
    "formatos": ["xlsx", "csv"],
    "incluir_json": true,
    "subir": true,
    "omitir_vacias": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "CLAVE_FISCAL_FICTICIA"
  }
}
```

## Resultado, archivos y resultados parciales

`data.reportes` describe por sección/formato el nombre de cada archivo y las secciones omitidas por estar vacías; los archivos publicados incluyen huella y tamaño. Los errores se informan por sección/formato. Si al menos un reporte se obtiene y otro falla, el resultado es `PARCIAL` y conserva los éxitos. Si no se obtiene ningún reporte, el trabajo falla. Una sección sin contador legible se intenta, en vez de asumirse vacía. La consulta no modifica declaraciones ni efectúa pagos.
