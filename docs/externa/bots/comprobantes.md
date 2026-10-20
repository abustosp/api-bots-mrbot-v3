# Comprobantes

Consulta comprobantes emitidos/recibidos del contribuyente representado, solicita al portal que genere una consulta asíncrona y descarga consultas ya disponibles en el historial. **Consultar** y **historial** son lecturas. **Solicitar** registra una nueva solicitud en el portal: es un efecto externo y no conviene repetirlo a ciegas.

## Ruta V3 y catálogo

Ruta canónica de este bot: `POST /api/v3/bots/comprobantes/{operacion}`. El catálogo publica `consultar`, `solicitar` e `historial`, y marca `comprobantes` como **deprecated** con `alias_de: mis_comprobantes`. Es una anotación de catálogo, no convierte ambas entradas en una sola operación: `mis_comprobantes` tiene su propio registro y contrato. La operación va en la ruta, no dentro de `payload`.

La ruta canónica valida con las reglas efectivas de la operación: la CUIT representada se normaliza quitando espacios, guiones, puntos y barras, y luego debe quedar en 11 dígitos. No se valida el dígito verificador. Los aliases HTTP planos tienen un modelo de entrada distinto; no aplicar sus restricciones a esta ruta.

Cuerpo canónico: `{"payload": {...}, "credentials": {...}}`. La identidad de sesión y la contraseña fiscal pertenecen a `credentials`.

## Campos de `payload`

| Campo | Tipo; requerido/default | Omisión, `null`, vacío o `false`; efecto |
|---|---|---|
| `representado_cuit` | string, requerido; alias `cuit` | Se quitan separadores y se exigen 11 dígitos. Identifica al contribuyente consultado. `null` o vacío no satisfacen el requisito. |
| `representado_nombre` | string 1–128, requerido | Nombre que se selecciona/valida en el portal. Vacío o `null` inválidos. |
| `fecha_desde`, `fecha_hasta` | strings, requeridos, `DD/MM/AAAA`; aliases `desde`, `hasta` | Rango inclusivo y `fecha_desde <= fecha_hasta`. Fechas inválidas, vacías o nulas se rechazan. |
| `emitidos`, `recibidos` | boolean, ambos default `false`; aliases `descarga_emitidos`, `descarga_recibidos` | Debe ser `true` al menos uno. `false` omite ese tipo. Si se omite se aplica `false`; `null` explícito se rechaza. |
| `puntos_venta_emitidos`, `puntos_venta_recibidos` | lista de strings o enteros, opcional, hasta 200 elementos | Se conservan ceros iniciales y cada elemento debe contener solo dígitos. Omitido, `null` o lista vacía significa sin filtro. Debe enviarse una lista, no un valor escalar. |
| `timeout_mc` | entero 1–86400, opcional; default efectivo 30 segundos | Omitido o `null` usa el valor efectivo de 30 segundos. Controla la espera del servicio. Cero y otros valores fuera del rango se rechazan. |
| `incluir_json` | boolean, default `true`; alias `carga_json` | `false` omite la muestra de filas, no el resumen. Omitido aplica `true`; `null` explícito se rechaza. |
| `subir_csv` | boolean, default `true` | `false` no publica CSV en `files[]`, pero el archivo aún se procesa. Omitido aplica `true`; `null` explícito se rechaza. |
| `carga_b64` | boolean, default `false`, obsoleto | Se acepta y se ignora: los archivos se entregan como archivos, nunca como base64. `null` explícito se rechaza. |
| `eliminar_descargas` | boolean, default `true` | Se elimina antes de procesar la operación, por lo que no tiene efecto. En la ruta canónica también se ignoran `null` y valores no booleanos. |
| `proxy_request` | boolean nullable, default `null` | Aceptado por compatibilidad y sin efecto de selección de conexión; `null` es válido. |
| `verbose` | boolean, default `false` | Aceptado sin cambiar el resultado. `null` explícito se rechaza. |
| `cookies_header` | string nullable, máximo 8192, solo para `historial` | Campo legado sin efecto de autenticación. Omitido o `null` no modifica la sesión; cadena vacía se acepta sin efecto. |

Para `solicitar`, `incluir_json` y `subir_csv` no forman parte del modelo. `cookies_header` solo corresponde a `historial`. La validación rechaza campos desconocidos y requiere `emitidos=true` o `recibidos=true`. Los valores booleanos deben ser booleanos, no texto como `"false"`. En la ruta canónica se aplican estas reglas del bot; el alias HTTP plano tiene un DTO separado y puede tratar `null` de forma distinta.

## Credenciales

El objeto `credentials` puede omitirse en el envelope, pero se necesita una sesión fiscal para completar la operación. La identidad de sesión y la contraseña no tienen default; su falta normalmente causa un fallo de autenticación. La normalización central de credenciales no exige un patrón numérico para la identidad.

| Campo | Tipo; requerido/default | Omisión, `null`, vacío o `false`; efecto |
|---|---|---|
| `cuit_representante`, `cuit_inicio_sesion` | strings; se necesita una identidad para iniciar sesión; sin default | Si se omiten, son `null` o `""`, no queda identidad y falla la autenticación. `false` y otros tipos no textuales se rechazan. Si se envían ambos, deben coincidir. |
| `clave`, `clave_representante`, `contrasena` | strings; se necesita una contraseña, salvo que se aporte `clave_encriptada`; sin default | Omitir, `null` o `""` equivale a no aportar esa alternativa. `false` y otros tipos no textuales se rechazan. Si se envían varios aliases, deben coincidir. |
| `clave_encriptada` | string opcional; sin default | Omitido, `null` o `""` se ignora. `false` y otros tipos no textuales se rechazan. Si se informa, debe poder descifrarse; si también hay texto claro, ambos deben coincidir. |

No incluir credenciales reales en ejemplos.

## Ejemplos ficticios

Consultar emitidos y recibidos, ruta `POST /api/v3/bots/comprobantes/consultar`:

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

Para solicitar comprobantes emitidos, use esta ruta y un cuerpo mínimo compatible con `solicitar`:

Ruta: `POST /api/v3/bots/comprobantes/solicitar`.

```json
{
  "payload": {
    "representado_cuit": "20123456786",
    "representado_nombre": "Comercial Ejemplo SRL",
    "fecha_desde": "01/09/2026",
    "fecha_hasta": "30/09/2026",
    "emitidos": true,
    "recibidos": false
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "CLAVE-FICTICIA-NO-UTILIZABLE"
  }
}
```

La operación crea la solicitud en el portal, no un CSV. Para consultar o ver el historial se usan, respectivamente, `/api/v3/bots/comprobantes/consultar` y `/api/v3/bots/comprobantes/historial`; sus campos específicos se documentan por separado en este contrato.

## Resultado

El estado del trabajo incluye `result` como `OK`, `PARCIAL` o `ERROR`; el objeto del bot va en `data`. Campos de `data`:

| Operación | Clave | Tipo / condición |
|---|---|---|
| Todas | `operacion`, `representado_cuit`, `fecha_desde`, `fecha_hasta` | string; siempre presentes en una respuesta con datos. |
| `solicitar` | `ids_consulta` | objeto cuyas claves son `emitidos`/`recibidos` y valores son identificadores string de solicitud. |
| `consultar` / `historial` | `emitidos`, `recibidos` | objeto por tipo consultado: `filas` entero y `archivo` string; `sha256` string y `size_bytes` entero si se publicó; `muestra` lista de objetos si se pidió JSON. |
| `historial` | `consultas` | objeto por tipo con listas de registros encontrados. |
| `historial` | `faltantes` | lista de strings con los tipos no disponibles. |

Los CSV publicados aparecen en `files[]`. `solicitar` no genera CSV. `historial` puede ser `PARCIAL` si algunos tipos no están disponibles, o `ERROR` si no hay resultado utilizable. La composición de cada muestra y los registros del portal varían con los datos consultados.

`POST /api/v3/uploads` es la vía pública para aportar archivos en bots que lo solicitan; este bot no requiere carga de archivos de entrada.

El catálogo en `GET /api/v3/bots` publica `costo_creditos` como metadata informativa. La creación actual reserva una unidad de cuota por trabajo; consultar disponibilidad y límites en la API, no interpretar la metadata como cargo efectivo.
