# Mis Retenciones IVA Simple

## Propósito y operación

Consulta las retenciones/percepciones del modo IVA Simple para el representado y rango solicitados, generando exportaciones descargables. Solo admite la operación `consultar`.

Ruta V3: `POST /api/v3/bots/mis_retenciones_iva_simple/consultar`. Alias compatible: `POST /api/v3/mis_retenciones_iva_simple/consulta`. Envíe `{"payload": {...}, "credentials": {...}}`; la operación va en la URL. El trabajo se acepta asíncronamente (`202`) y se consulta con `GET /api/v3/jobs/{job_id}`.

## Payload

| Campo | Tipo; requerido; default | Efecto y comportamiento al omitir, enviar `null`, vacío o `false` |
|---|---|---|
| `representado_cuit` | texto de 11 dígitos; requerido | CUIT consultado; alias `cuit`. Se quitan espacios, guiones, puntos y `/`. Omitido, `null`, vacío o inválido: rechazo. |
| `representado_nombre` | texto 1–128; requerido | Identificación del representado. `denominacion` es alias compatible. Omitido, `null` o vacío: rechazo. |
| `fecha_desde` | fecha `dd/mm/aaaa`; requerida | Inicio del período. Alias `desde`. Fecha real obligatoria; omitida, `null`, vacía o inválida: rechazo. |
| `fecha_hasta` | fecha `dd/mm/aaaa`; requerida | Fin inclusive. Alias `hasta`. Debe ser igual o posterior a `fecha_desde` y no posterior al último día del mes siguiente al mes de `fecha_desde`. Omitida, `null`, vacía, inválida o fuera de ese rango: rechazo. |
| `incluir_json` | booleano; opcional; `true` | Incluye muestras/detalle resumido en el resultado. `false` omite ese detalle, no detiene la consulta. `null` se rechaza. |
| `subir_csv` | booleano; opcional; `true` | Adjunta los CSV generados. `false` evita adjuntarlos, no detiene su consulta/generación. `null` se rechaza. |
| `required_result_marker` | texto; opcional; `"IVA SIMPLE"` | Texto requerido para comprobar que la exportación corresponde al modo esperado. Si se omite, se usa `"IVA SIMPLE"`; `null` se rechaza. Cadena vacía se acepta y desactiva esa comprobación; un marcador no vacío debe aparecer en el contenido descargado, sin distinguir mayúsculas/minúsculas, o la exportación se considera fallida. |

Debe estar activo `incluir_json` o `subir_csv`; ambos en `false` se rechazan. Se aceptan campos adicionales, pero no se documenta ni garantiza efecto para los no listados.

## Credenciales

Se requieren credenciales fiscales válidas y acceso al servicio para el CUIT indicado.

| Campo en el sobre/`credentials` | Tipo; requisito; default | Efecto al enviarlo; omisión, `null`, vacío o `false` |
|---|---|---|
| `credentials` | objeto JSON; opcional; default `{}` | Omitirlo usa `{}`. `{}` pasa el sobre pero no aporta acceso. `null`, `""` o el booleano `false` se rechazan por tipo. |
| `credentials.cuit_representante` | texto; requerido junto con una clave, salvo alias; sin default | Identifica la cuenta que inicia sesión. Omitido, `null` o `""` no aporta identidad; el booleano `false` se rechaza por tipo. |
| `credentials.cuit_inicio_sesion` | texto; alias opcional de `cuit_representante`; sin default | Aporta la misma identidad. Omitido, `null` o `""` equivale a no enviarlo; `false` se rechaza. Si se envía con el otro nombre, los valores deben coincidir. |
| `credentials.clave` | texto; requerida junto con una identidad, salvo alias o alternativa cifrada; sin default | Clave fiscal en texto. Omitida, `null` o `""` no aporta clave; `false` se rechaza por tipo. |
| `credentials.clave_representante` | texto; alias opcional de `clave`; sin default | Aporta la clave fiscal. Omitida, `null` o `""` equivale a no enviarla; `false` se rechaza. Si se repite con otro alias de clave, deben coincidir. |
| `credentials.contrasena` | texto; alias opcional de `clave`; sin default | Aporta la clave fiscal. Omitida, `null` o `""` equivale a no enviarla; `false` se rechaza. Si se repite con otro alias de clave, deben coincidir. |
| `credentials.clave_encriptada` | texto Base64 cifrado; alternativa opcional; sin default | Se descifra mediante el mecanismo admitido por la API. Omitida, `null` o `""` equivale a ausencia; `false` o cifrado inválido se rechaza. Si se envía con una clave en texto, ambas deben coincidir. |

No incluya secretos en `payload`. Los alias duplicados deben coincidir.

## Ejemplo ficticio válido

```json
{
  "payload": {
    "representado_cuit": "30876543210",
    "representado_nombre": "Comercio Ejemplo SA",
    "fecha_desde": "01/09/2025",
    "fecha_hasta": "31/10/2025",
    "incluir_json": true,
    "subir_csv": true,
    "required_result_marker": "IVA SIMPLE"
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "Clave-Ficticia-No-Usar-2026"
  }
}
```

## Resultado y archivos

La consulta activa el modo IVA Simple y procesa tres categorías de operación, en exportación IVA Simple y su alternativa XLS. Para cada combinación se informa cantidad de filas y estado/mensaje; los CSV se adjuntan en `files[]` cuando `subir_csv` está activo. `incluir_json: false` omite datos de muestra, no los archivos solicitados.

Los errores de una categoría/formato se incluyen en el resultado y el trabajo continúa con las demás combinaciones; el estado global puede permanecer `OK`. Verifique los mensajes y conteos para identificar resultados incompletos. El portal limita fechas futuras al día actual al consultar, aunque la validación de entrada permite el rango indicado si cumple la regla mensual.