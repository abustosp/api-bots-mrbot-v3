# Sistema de Cuentas (SCT)

**Propósito.** Consultar reportes de vencimientos, deudas y declaraciones juradas pendientes para un CUIT; seleccionar uno o más formatos disponibles.

## Operación y ruta V3

Catálogo: `sct`, operación `consultar`.

- Canónica: `POST /api/v3/bots/sct/consultar`.
- Alias V3: `POST /api/v3/sct/consulta`.

Cuerpo: `{ "payload": { ... }, "credentials": { ... } }`. La aceptación devuelve `job_id`; consultar estado/resultado en `/api/v3/jobs/{job_id}`.

## Credenciales

Se requiere clave fiscal y CUIT representante en `credentials.clave` y `credentials.cuit_representante`. Se aceptan `clave_representante` o `contrasena` como nombres alternativos coincidentes y `clave_encriptada` como alternativa cifrada Base64. `cuit_inicio_sesion` equivale al CUIT representante. Si todas las claves faltan, son `null` o `""`, no hay acceso y el trabajo no puede ejecutarse. Para login se requiere un CUIT válido. No se envían claves como parámetros de consulta.

## Campos del payload

| Campo | Tipo, requerido y default | Efecto y tratamiento de omisión/null/vacío/false |
|---|---|---|
| `representado_cuit` | texto requerido; alias `cuit` | CUIT de 11 dígitos, normaliza espacios, guiones, puntos y `/`. Vacío/null inválidos. |
| `secciones` | lista no vacía de textos, requerida | Valores: `vencimientos`, `deudas`, `ddjj_pendientes`. Se recortan espacios, se ignora mayúsculas y se deduplica/ordena. Vacío/null o valor desconocido rechaza. Define qué pestañas consultar. |
| `formatos` | lista no vacía de textos, requerida | Valores: `xlsx`, `csv`, `pdf`; se recortan espacios, normalizan a minúsculas, deduplican/ordenan. Vacío/null o valor desconocido rechaza. |
| `incluir_json` | booleano, default `true` | `true` incluye el listado `reportes`; `false` lo omite. `null` inválido. |
| `subir` | booleano, default `true` | `true` publica archivos; `false` no publica. Omitir = `true`; `null` inválido. Alias V3 de subida: `carga_minio`, `minio_upload` y `archivo_historico_minio`. |
| `omitir_vacias` | booleano, default `true` | Omite una sección solo si el portal informa explícitamente cero elementos. `false` intenta descargarla de todos modos. Omitir = `true`; `null` inválido. |

En la forma V3 compatible también se aceptan las banderas históricas `vencimientos_{excel,xlsx,csv,pdf}_minio`, `deudas_{excel,xlsx,csv,pdf}_minio` y `ddjj_pendientes_{excel,xlsx,csv,pdf}_minio`; una bandera verdadera incorpora esa sección y formato. Si se envían todas ausentes o falsas, la normalización histórica selecciona las tres secciones con XLSX y desactiva la publicación de archivos. No enviar valores contradictorios entre forma moderna y banderas antiguas.

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