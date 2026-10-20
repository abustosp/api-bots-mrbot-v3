# Comprobantes

Consulta comprobantes emitidos/recibidos del contribuyente representado, solicita al portal que genere una consulta asíncrona y descarga consultas ya disponibles en el historial. **Consultar** y **historial** son lecturas. **Solicitar** registra una nueva solicitud en el portal: es un efecto externo y no conviene repetirlo a ciegas.

## Ruta V3 y catálogo

Ruta genérica: `POST /api/v3/bots/{bot}/{operacion}`. El catálogo V3 todavía publica `comprobantes` con `consultar`, `solicitar` e `historial` (2 créditos cada una), pero lo marca **deprecated** y `alias_de: mis_comprobantes`. Para integraciones nuevas, usar `/api/v3/bots/mis_comprobantes/consultar`, `/solicitar` o `/historial` con el mismo contrato de campos. La operación va en la ruta, no dentro de `payload`.

En la ruta pública V3 el formato admitido es exactamente 11 dígitos sin separadores, por el patrón de validación del endpoint; la normalización interna acepta separadores, pero no amplía ese patrón público. En los ejemplos usar CUIT ficticio numérico.

Cuerpo canónico: `{"payload": {...}, "credentials": {...}}`. La identidad de sesión y la contraseña fiscal pertenecen a `credentials`.

## Campos de `payload`

| Campo | Tipo; requerido/default | Omisión, `null`, vacío o `false`; efecto |
|---|---|---|
| `representado_cuit` | string, requerido; alias `cuit` | Se quitan separadores y se exigen 11 dígitos. Identifica al contribuyente consultado. `null` o vacío no satisfacen el requisito. |
| `representado_nombre` | string 1–128, requerido | Nombre que se selecciona/valida en el portal. Vacío o `null` inválidos. |
| `fecha_desde`, `fecha_hasta` | strings, requeridos, `DD/MM/AAAA`; aliases `desde`, `hasta` | Rango inclusivo y `fecha_desde <= fecha_hasta`. Fechas inválidas, vacías o nulas se rechazan. |
| `emitidos`, `recibidos` | boolean, ambos default `false`; aliases `descarga_emitidos`, `descarga_recibidos` | Debe ser `true` al menos uno. `false` omite ese tipo. `null` se trata como omitido en la ruta pública y queda en `false`. |
| `puntos_venta_emitidos`, `puntos_venta_recibidos` | lista de string o entero, opcional | Aceptan también un solo string/entero, hasta 200 valores. Se conservan ceros iniciales y se aceptan solo dígitos. Omitido, `null` o lista vacía significa sin filtro por punto de venta. |
| `timeout_mc` | entero 1–86400, opcional; default operativo 30 segundos | Si se omite o es `null`, se usa 30. Controla la espera del portal. No usar cero. |
| `incluir_json` | boolean, default `true`; alias `carga_json` | `false` omite la muestra de filas, no el resumen. `null` público aplica el default. |
| `subir_csv` | boolean, default `true`; aliases de carga históricos | `false` no publica los CSV en `files[]`; el proceso todavía los descarga/procesa para contar filas. `null` público aplica el default. |
| `carga_b64` | boolean, default `false`, obsoleto | Se acepta por compatibilidad, pero no cambia la salida: los archivos se entregan como archivos, nunca como base64. `null` público aplica `false`. |
| `eliminar_descargas` | boolean, default `true` | Aceptado por compatibilidad, sin efecto adicional en la ejecución actual. |
| `proxy_request` | boolean nullable, default `null` | Aceptado por compatibilidad; el cliente no selecciona la conexión del servicio con este campo. |
| `verbose` | boolean, default `false` | Aceptado, no cambia el resultado público. |
| `cookies_header` | string opcional, máximo 8192, solo para `historial` | Campo legado, no se usa para autenticar ni modificar la sesión. Vacío se acepta sin efecto. |

Para `solicitar`, los únicos campos de esta tabla que no forman parte del modelo son `incluir_json` y `subir_csv`. `cookies_header` solo corresponde a `historial`. La validación rechaza campos desconocidos y requiere `emitidos=true` o `recibidos=true`. Los valores booleanos deben ser booleanos, no texto como `"false"`.

## Credenciales

`credentials.cuit_representante` es string de exactamente 11 dígitos sin separadores en la ruta pública V3; la validación de ejecución normaliza separadores, pero la capa pública exige el patrón estricto. Es requerido para la sesión fiscal. `credentials.clave` es string requerido para ingresar. Se aceptan los nombres heredados `clave_representante` o `contrasena` en lugar de `clave`; `clave_encriptada` es una alternativa cifrada admitida por la plataforma. No enviar varias contraseñas distintas. Credencial omitida, vacía o nula no inicia una sesión válida. No incluir credenciales reales en ejemplos, registros o documentación.

## Ejemplos ficticios

Consultar emitidos y recibidos:

```json
{
  "payload": {
    "representado_cuit": "20123456786",
    "representado_nombre": "Comercial Ejemplo SRL",
    "fecha_desde": "01/09/2026",
    "fecha_hasta": "30/09/2026",
    "emitidos": true,
    "recibidos": true,
    "puntos_venta_emitidos": ["0001", "0007"],
    "puntos_venta_recibidos": [],
    "incluir_json": true,
    "subir_csv": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "CLAVE-FICTICIA-NO-UTILIZABLE"
  }
}
```

Para solicitar, el mismo cuerpo debe ir a `/api/v3/bots/mis_comprobantes/solicitar`; la consulta crea solicitudes para cada tipo activado. Para historial, usar `/historial` y las fechas/tipos de interés. El historial busca solicitudes existentes, no crea una nueva.

## Resultado

La respuesta de trabajo incluye estado y `result`. `consultar` informa `operacion`, CUIT y rango, y por cada tipo solicitado un resumen de filas, nombre de archivo y metadatos disponibles (hash SHA-256, tamaño y muestra si se pidió). Si `subir_csv=true`, el CSV aparece en `files[]`; si es `false`, no hay referencia descargable en `files[]`. `solicitar` devuelve `ids_consulta` por tipo y no devuelve CSV. `historial` devuelve `consultas` y `faltantes`; puede terminar `PARCIAL` o `ERROR` si no hay una descarga para todos los tipos solicitados. Los nombres de archivo y el número de comprobantes dependen de los datos del portal.

`POST /api/v3/uploads` es la vía pública para aportar archivos en bots que lo solicitan; este bot no requiere carga de archivos de entrada.
