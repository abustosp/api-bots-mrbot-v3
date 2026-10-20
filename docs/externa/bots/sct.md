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

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `representado_cuit` | texto | Sí | ninguno | CUIT de 11 dígitos; normaliza espacios, guiones, puntos y `/`. Alias: `cuit`. Vacío o `null` inválidos. | `"20-12345678-6"` |
| `secciones` | lista de textos | No | `["vencimientos","deudas","ddjj_pendientes"]` | Valores: `vencimientos`, `deudas`, `ddjj_pendientes`. Se recortan espacios, se ignora mayúsculas y se deduplica y ordena. Lista vacía, `null` o valor desconocido rechaza. En la API canónica, si se omite esta clave, la normalización agrega las tres secciones. | `["vencimientos","deudas"]` |
| `formatos` | lista de textos | No | `["xlsx"]` | Valores: `xlsx`, `csv`, `pdf`; se recortan espacios, se normalizan a minúsculas, se deduplican y ordenan. Lista vacía, `null` o valor desconocido rechaza. En la API canónica, si se omite esta clave, se agrega `["xlsx"]`. | `["xlsx","csv"]` |
| `incluir_json` | booleano | No | `true` | `true` incluye el listado `reportes`; `false` lo omite. `null` inválido. | `true` |
| `subir` | booleano | No | `false` | `true` publica archivos; `false` no publica. `null` inválido. En la API V3, si se omite `subir`, la normalización lo establece en `false`; el default efectivo de la API es `false`. Debe enviarse explícitamente para controlar la publicación. | `true` |
| `omitir_vacias` | booleano | No | `true` | Omite una sección solo si el portal informa explícitamente cero elementos. `false` intenta descargarla de todos modos. Omitir equivale a `true`; `null` inválido. | `true` |

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
