# Portal IVA

**Propósito.** Consultar libros de IVA de ventas/compras de un período, importar TXT preparados o hacer ambas cosas. La descarga también puede actualizar datos del portal y borrar registros; no es necesariamente una consulta sin cambios.

## Operación y ruta V3

Catálogo: `portal_iva` con `descargar`, `importar` y `gestionar`.

- Ruta canónica: `POST /api/v3/bots/portal_iva/{operacion}`.
- Alias V3: `POST /api/v3/portal_iva/consulta` equivale solo a `descargar`.
- No hay alias equivalente para `importar` ni `gestionar`; usar la ruta canónica.

La ruta canónica recibe `{ "payload": { ... }, "credentials": { ... } }`; el alias de compatibilidad recibe un cuerpo plano. No mezclar ambas formas. La solicitud aceptada devuelve `job_id`; consultar su estado y resultado en `/api/v3/jobs/{job_id}`. Los nombres de archivo de entrada deben referenciar cargas previas mediante `object_key`. Convenciones compartidas de credenciales: [API pública](../api.md).

## Credenciales fiscales

El acceso requiere clave fiscal y CUIT representante para iniciar sesión. Enviar `credentials.clave` y `credentials.cuit_representante`. El contrato también acepta `clave_representante` o `contrasena` como nombres alternativos para la clave, siempre que los nombres de clave enviados coincidan; `clave_encriptada` es una alternativa cifrada Base64. `cuit_inicio_sesion` equivale a `cuit_representante`. Los secretos enviados en `payload` se trasladan al apartado de credenciales; no deben mezclarse valores distintos. La omisión, `null` o cadena vacía de todas las claves no proporciona acceso: el trabajo no podrá ejecutarse. Un CUIT vacío/null se omite; para la sesión debe informarse uno válido.

## Campos del payload

Tipos y valores siguientes reflejan validación efectiva. Campos de descarga son aplicables a `descargar` y `gestionar`; archivos de entrada a `importar` y `gestionar`; las opciones de preparación se aceptan en las tres operaciones.

| Campo | Tipo, requerido y default | Carga, omisión, `null`, vacío o `false` |
|---|---|---|
| `periodo` | texto, requerido | Acepta `MM/AAAA`, `MM-AAAA`, `AAAA/MM`, `AAAA-MM` o `AAAAMM`; se normaliza a `AAAAMM`, mes 01–12. `null`/vacío inválidos. |
| `representado_cuit` | texto nullable, opcional; default `null`; alias `cuit` | CUIT de 11 dígitos; admite espacios, guiones, puntos y `/`, que se quitan. Omitir o `null` usa el CUIT representante. Vacío inválido. |
| `representado_nombre` | texto nullable, opcional; default `null`; máximo 128 caracteres | Nombre opcional. `null`/omitir no agrega nombre; cadena vacía no cumple mínimo de 1. |
| `descarga_ventas` | booleano; default `false`; alias `descarga_CSV_ventas` | `true` solicita libro ventas; `false` no lo descarga. Omitir equivale a `false`; `null` inválido. |
| `descarga_compras` | booleano; default `false`; alias `descarga_CSV_compras` | `true` solicita libro compras; `false` no lo descarga. Omitir equivale a `false`; `null` inválido. Debe activarse ventas y/o compras. |
| `incluir_json` | booleano; default `true` | Incluye resúmenes y muestras de filas en el resultado. `false` los omite; no afecta la consulta. Omitir equivale a `true`; `null` inválido. |
| `subir_csv` | booleano; default `true` | Solicita que los CSV queden disponibles como archivos de resultado. `false` no los publica; omitir equivale a `true`; `null` inválido. Envíe este nombre canónico para controlar la publicación. |
| `refrescar_desde_arca` | booleano; default `true` | Antes de cada descarga solicita importar datos desde el organismo fiscal al libro. `false` evita esa actualización. Omitir activa la actualización; `null` inválido. |
| `limpiar_despues` | booleano; default `true` | Después de cada descarga intenta borrar los registros del libro. `false` evita ese borrado. Omitir lo activa; `null` inválido. |
| `descartar_borrador` | booleano; default `true` | Tras completar las descargas intenta descartar el borrador de declaración. `false` evita el intento. Omitir lo activa; `null` inválido. |
| `operaciones_ng_o_e` | booleano; default `false` | Marca la opción de operaciones no gravadas/exentas al preparar el período. `false` la desmarca; omitir equivale a `false`; `null` inválido. |
| `prorrateo_global` | booleano; default `false` | Selecciona opción de prorrateo global cuando corresponde. `false` no la selecciona; `null` inválido. |
| `prorrateo_asignacion_directa` | booleano; default `false` | Selecciona prorrateo de asignación directa cuando corresponde. `false` no lo selecciona; `null` inválido. |
| `prorrateo_ambos` | booleano; default `false` | Selecciona ambas opciones de prorrateo cuando corresponde. `false` no las selecciona; `null` inválido. Si varias opciones de prorrateo son `true`, el portal recibe la prioridad «ambos», luego global y luego directa. |
| `importacion_definitiva_bienes` | booleano; default `false` | Marca la opción declarativa correspondiente; `false` la desmarca; `null` inválido. |
| `importacion_servicios` | booleano; default `false` | Marca importación de servicios; `false` la desmarca; `null` inválido. |
| `regimen_turiva` | booleano; default `false` | Marca el régimen TURIVA; `false` la desmarca; `null` inválido. |
| `bienes_usados` | booleano; default `false` | Marca bienes usados/material reciclable; `false` la desmarca; `null` inválido. |
| `ninguna_anteriores` | booleano; default `true` | Marca «ninguna de las anteriores» salvo que se indique `false`; `null` inválido. |
| `periodos_seleccionables` | lista de objetos `{string:string}`; default `[]` | Se acepta, pero no determina el período consultado. El portal devuelve su propia lista al preparar el período y puede reemplazar este valor. `null` inválido. |
| `ventas_txt_key` | texto, opcional, solo importar/gestionar | Referencia a TXT ya cargado. `null` se interpreta como ausencia; vacío inválido. Debe enviarse junto con `ventas_alicuotas_txt_key`. |
| `ventas_alicuotas_txt_key` | texto, opcional, solo importar/gestionar | Referencia al TXT de alícuotas de ventas. `null` equivale a ausencia; vacío inválido. Se exige junto con `ventas_txt_key`. |
| `compras_txt_key` | texto, opcional, solo importar/gestionar | Referencia a TXT ya cargado. `null` equivale a ausencia; vacío inválido. Debe enviarse junto con `compras_alicuotas_txt_key`. |
| `compras_alicuotas_txt_key` | texto, opcional, solo importar/gestionar | Referencia al TXT de alícuotas de compras. `null` equivale a ausencia; vacío inválido. Se exige junto con `compras_txt_key`. |

Cada referencia de archivo debe provenir de `POST /api/v3/uploads`. El contenido debe ser un TXT existente, no vacío, menor o igual a 5 MB. No enviar el contenido del archivo dentro del JSON ni usar los nombres locales terminados en `_path`.

Ejemplo ficticio mínimo para `POST /api/v3/bots/portal_iva/importar`; las dos claves deben reemplazarse por referencias efectivamente obtenidas al cargar archivos:

```json
{"payload":{"periodo":"05/2026","representado_cuit":"20-12345678-6","ventas_txt_key":"uploads/demo/ventas.txt","ventas_alicuotas_txt_key":"uploads/demo/ventas-alicuotas.txt"},"credentials":{"cuit_representante":"20123456786","clave":"CLAVE_FISCAL_FICTICIA"}}
```

Para `POST /api/v3/bots/portal_iva/gestionar`, envíe por lo menos un par TXT como arriba, active al menos una descarga (`descarga_ventas` o `descarga_compras`) y una salida. Para `POST /api/v3/bots/portal_iva/descargar`, no envíe claves TXT y active uno o ambos libros.

### Restricciones por operación

- `descargar`: activar ventas y/o compras, y dejar al menos una salida (`incluir_json=true` o `subir_csv=true`).
- `importar`: se requiere por lo menos el par TXT de ventas o el par TXT de compras. Importar reemplaza los registros existentes del libro.
- `gestionar`: aplica las reglas de `descargar` y de `importar`; primero importa los TXT proporcionados y después descarga los libros elegidos.
- Los flags de opciones declarativas se envían al preparar y guardar el período antes de importar/descargar.

## Ejemplo ficticio válido: `POST /api/v3/bots/portal_iva/descargar`

```json
{
  "payload": {
    "periodo": "05/2026",
    "representado_cuit": "20-12345678-6",
    "representado_nombre": "Comercial Ficticia SA",
    "descarga_ventas": true,
    "descarga_compras": false,
    "incluir_json": true,
    "subir_csv": true,
    "refrescar_desde_arca": false,
    "limpiar_despues": false,
    "descartar_borrador": false,
    "operaciones_ng_o_e": false,
    "prorrateo_global": false,
    "prorrateo_asignacion_directa": false,
    "prorrateo_ambos": false,
    "importacion_definitiva_bienes": false,
    "importacion_servicios": false,
    "regimen_turiva": false,
    "bienes_usados": false,
    "ninguna_anteriores": true,
    "periodos_seleccionables": []
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "CLAVE_FISCAL_FICTICIA"
  }
}
```

## Resultado y efectos

Claves reales de `data`:

| Clave | Tipo | Cuándo aparece |
|---|---|---|
| `operacion`, `representado_cuit`, `periodo` | textos | Siempre; `periodo` normalizado como `AAAAMM`. |
| `periodos_seleccionables` | lista de objetos `{string:string}` | Solo si el portal devuelve valores durante la preparación. |
| `ventas`, `compras` | objetos | Por cada libro solicitado en descarga/gestión. |
| `ventas.filas`, `compras.filas` | entero | Filas de datos, sin contar cabecera. |
| `ventas.archivo`, `compras.archivo` | texto | Nombre del CSV resultante. |
| `ventas.sha256`, `compras.sha256` | texto | Solo si se publica el archivo. |
| `ventas.size_bytes`, `compras.size_bytes` | entero | Solo si se publica el archivo. |
| `ventas.muestra`, `compras.muestra` | lista de objetos de texto | Solo con `incluir_json=true`, hasta cinco filas con claves de la cabecera CSV. |
| `importaciones` | lista de `{libro: texto, ok: booleano}` | En `importar` y `gestionar`, con un elemento por libro importado. |
| `avisos` | lista de textos | Puede aparecer si falla el intento opcional de descartar borrador tras descargas completas. |

Los CSV publicados aparecen como archivos de resultado. `gestionar` combina datos de descarga e importación. Si una descarga posterior falla, no hay contrato de resultado parcial: el trabajo puede fallar después de que una fase anterior ya haya cambiado el portal o publicado un archivo.

**Efectos importantes:** por defecto una descarga actualiza desde el organismo, guarda las opciones del período, borra registros después de exportar y descarta el borrador tras completar. `importar` borra todos los registros del libro antes de cargar el TXT nuevo. Estos borrados pueden ser irreversibles. Para impedir las tres acciones adicionales al descargar, especificar explícitamente `refrescar_desde_arca=false`, `limpiar_despues=false` y `descartar_borrador=false`. Generar un CSV no equivale a presentar una declaración.
