# Liquidación de Granos

## Propósito y operación

Consulta las liquidaciones de granos asociadas a un representado en el portal de ARCA. Recorre las categorías seleccionadas y descarga sus planillas y comprobantes disponibles. No crea, presenta ni modifica liquidaciones.

Ruta V3: `POST /api/v3/bots/liquidacion_granos/consultar`. Ruta compatible: `POST /api/v3/liquidacion_granos/consulta`, equivalente a `consultar`. Use el sobre `{"payload": {...}, "credentials": {...}}`; la operación va en la URL. La solicitud queda aceptada asíncronamente (`202`) y se completa como trabajo consultable mediante `GET /api/v3/jobs/{job_id}`.

## Payload

| Campo | Tipo; requerido; default | Efecto y comportamiento al omitir, enviar `null`, vacío o `false` |
|---|---|---|
| `representado_cuit` | texto de 11 dígitos; requerido | Identifica el representado. Acepta `cuit` o `cuit_representado`; se quitan espacios, guiones, puntos y `/`. Omitido, `null`, vacío o CUIT inválido: rechazo. |
| `representado_nombre` | texto, 1–128 caracteres; requerido | Nombre con el que se elige el representado en el portal. `denominacion` es alias compatible. Omitido, `null` o vacío: rechazo. Debe corresponder a una relación visible para la cuenta. |
| `fecha_desde` | fecha `dd/mm/aaaa`; requerida | Inicio del rango. Alias `desde`. Debe ser una fecha real. Omitida, `null`, vacía o inválida: rechazo. |
| `fecha_hasta` | fecha `dd/mm/aaaa`; requerida | Fin inclusive del rango. Alias `hasta`. Debe ser igual o posterior a `fecha_desde`. Omitida, `null`, vacía o inválida: rechazo. |
| `lpg_emitidas` | booleano; opcional; `true` | Incluye LPG emitidas. `false` excluye esa categoría; `null` se rechaza. |
| `lpg_recibidas` | booleano; opcional; `true` | Incluye LPG recibidas. `false` excluye esa categoría; `null` se rechaza. |
| `lsg_emitidas` | booleano; opcional; `true` | Incluye LSG emitidas. `false` excluye esa categoría; `null` se rechaza. |
| `lsg_recibidas` | booleano; opcional; `true` | Incluye LSG recibidas. `false` excluye esa categoría; `null` se rechaza. |
| `certificados_deposito` | booleano; opcional; `true` | Incluye certificados de depósito. `false` excluye esa categoría; `null` se rechaza. |
| `subir_archivos` | booleano; opcional; `true` | Si es `true`, adjunta las planillas y comprobantes producidos. `false` evita adjuntarlos al resultado, pero no evita consultar ni generar los archivos durante el trabajo. Omitido o `null` equivale al default `true`; un booleano `false` desactiva la subida. |

Los campos booleanos de sección en `null` se rechazan. La opción de subida es excepción: `subir_archivos: null` se trata como omitido y recupera el default `true`. Debe quedar al menos una de las cinco categorías en `true`; si todas son `false`, se rechaza. Los campos desconocidos se rechazan.

## Credenciales

Se requieren credenciales fiscales funcionales y autorización de ARCA sobre el CUIT consultado.

| Campo en el sobre/`credentials` | Tipo; requisito; default | Efecto al enviarlo; omisión, `null`, vacío o `false` |
|---|---|---|
| `credentials` | objeto JSON; opcional; default `{}` | Omitirlo usa `{}`. `{}` pasa el sobre pero no aporta acceso. `null`, `""` o el booleano `false` se rechazan por tipo. |
| `credentials.cuit_representante` | texto; requerido junto con una clave, salvo alias; sin default | Identifica la cuenta que inicia sesión. Omitido, `null` o `""` no aporta identidad; el booleano `false` se rechaza por tipo. |
| `credentials.cuit_inicio_sesion` | texto; alias opcional de `cuit_representante`; sin default | Aporta la misma identidad. Omitido, `null` o `""` equivale a no enviarlo; `false` se rechaza. Si se envía con el otro nombre, los valores deben coincidir. |
| `credentials.clave` | texto; requerida junto con una identidad, salvo alias o alternativa cifrada; sin default | Clave fiscal en texto. Omitida, `null` o `""` no aporta clave; `false` se rechaza por tipo. |
| `credentials.clave_representante` | texto; alias opcional de `clave`; sin default | Aporta la clave fiscal. Omitida, `null` o `""` equivale a no enviarla; `false` se rechaza. Si se repite con otro alias de clave, deben coincidir. |
| `credentials.contrasena` | texto; alias opcional de `clave`; sin default | Aporta la clave fiscal. Omitida, `null` o `""` equivale a no enviarla; `false` se rechaza. Si se repite con otro alias de clave, deben coincidir. |
| `credentials.clave_encriptada` | texto Base64 cifrado; alternativa opcional; sin default | Se descifra mediante el mecanismo admitido por la API. Omitida, `null` o `""` equivale a ausencia; `false` o cifrado inválido se rechaza. Si se envía con una clave en texto, ambas deben coincidir. |

Si se envían alias repetidos con valores distintos, la solicitud se rechaza. Mantenga las credenciales fuera de `payload`.

## Ejemplo ficticio válido

```json
{
  "payload": {
    "representado_cuit": "30876543210",
    "representado_nombre": "Productora Ejemplo SA",
    "fecha_desde": "01/01/2025",
    "fecha_hasta": "31/01/2025",
    "lpg_emitidas": true,
    "lpg_recibidas": false,
    "lsg_emitidas": false,
    "lsg_recibidas": false,
    "certificados_deposito": false,
    "subir_archivos": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "Clave-Ficticia-No-Usar-2026"
  }
}
```

El CUIT y la denominación del ejemplo son ficticios. Para obtener resultados, el representado y el período deben existir en la cuenta real.

## Resultado y archivos

`data.secciones` incluye solo categorías solicitadas. Cada sección resume la planilla (nombre y tamaño) y los comprobantes encontrados; cada comprobante puede tener su propio archivo PDF. Los archivos generados se exponen en `files[]` si `subir_archivos` es `true`. Con `false`, el resumen puede informar nombres locales, pero no se adjuntan archivos descargables al trabajo.

Una consulta completada devuelve estado `OK`. Una falla durante el acceso o la extracción puede abortar el trabajo; no se ofrece un estado parcial independiente por categoría. Una sección sin datos puede no producir planilla o comprobantes.