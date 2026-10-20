# Mis Retenciones IVA Simple

## Propósito y operación

Consulta las retenciones/percepciones del modo IVA Simple para el representado y rango solicitados, generando exportaciones descargables. Solo admite la operación `consultar`.

Ruta V3: `POST /api/v3/bots/mis_retenciones_iva_simple/consultar`. Alias compatible: `POST /api/v3/mis_retenciones_iva_simple/consulta`. Envíe `{"payload": {...}, "credentials": {...}}`; la operación va en la URL. El trabajo se acepta asíncronamente (`202`) y se consulta con `GET /api/v3/jobs/{job_id}`.

## Payload

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `representado_cuit` | texto | Sí | ninguno | CUIT consultado; alias `cuit`. Se quitan espacios, guiones, puntos y `/` y debe quedar en 11 dígitos. Omitido, `null`, vacío o inválido: rechazo. | `"30876543210"` |
| `representado_nombre` | texto | Sí | ninguno | Identificación del representado, de 1 a 128 caracteres. Alias compatible: `denominacion`. Omitido, `null` o vacío: rechazo. | `"Comercio Ejemplo SA"` |
| `fecha_desde` | texto | Sí | ninguno | Inicio del período en formato `dd/mm/aaaa`; alias `desde`. Fecha real obligatoria; omitida, `null`, vacía o inválida: rechazo. | `"01/09/2025"` |
| `fecha_hasta` | texto | Sí | ninguno | Fin inclusive en formato `dd/mm/aaaa`; alias `hasta`. Debe ser igual o posterior a `fecha_desde` y no posterior al último día del mes siguiente al mes de `fecha_desde`. Omitida, `null`, vacía, inválida o fuera de ese rango: rechazo. | `"31/10/2025"` |
| `incluir_json` | booleano | No | `true` | Incluye muestras y detalle resumido en el resultado. `false` omite ese detalle, no detiene la consulta. Omitido equivale a `true`; `null` se rechaza. | `true` |
| `subir_csv` | booleano | No | `true` | Adjunta los CSV generados. `false` evita adjuntarlos, no detiene su consulta ni su generación. Omitido equivale a `true`; `null` se rechaza. | `true` |
| `required_result_marker` | texto | No | `"IVA SIMPLE"` | Texto requerido para comprobar que la exportación corresponde al modo esperado. Si se omite, se usa `"IVA SIMPLE"`; `null` se rechaza. Cadena vacía se acepta y desactiva esa comprobación; un marcador no vacío debe aparecer en el contenido descargado, sin distinguir mayúsculas y minúsculas, o la exportación se considera fallida. | `"IVA SIMPLE"` |

Debe estar activo `incluir_json` o `subir_csv`; ambos en `false` se rechazan. Se aceptan campos adicionales, pero no se documenta ni garantiza efecto para los no listados.

## Credenciales

Se requieren credenciales fiscales válidas y acceso al servicio para el CUIT indicado.

| Sección | Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|---|
| `sobre` | `credentials` | objeto | No | `{}` | Omitirlo usa `{}`. `{}` pasa el sobre pero no aporta acceso. `null`, `""` o el booleano `false` se rechazan por tipo. | `{}` |
| `credentials` | `cuit_representante` | texto | Condicional | ninguno | Requerido junto con una clave, salvo alias. Identifica la cuenta que inicia sesión. Omitido, `null` o `""` no aporta identidad; el booleano `false` se rechaza por tipo. | `"20123456786"` |
| `credentials` | `cuit_inicio_sesion` | texto | No | ninguno | Alias opcional de `cuit_representante`; aporta la misma identidad. Omitido, `null` o `""` equivale a no enviarlo; `false` se rechaza. Si se envía con el otro nombre, los valores deben coincidir. | `"20123456786"` |
| `credentials` | `clave` | texto | Condicional | ninguno | Requerida junto con una identidad, salvo alias o alternativa cifrada. Clave fiscal en texto. Omitida, `null` o `""` no aporta clave; `false` se rechaza por tipo. | `"CLAVE_FISCAL_FICTICIA"` |
| `credentials` | `clave_representante` | texto | No | ninguno | Alias opcional de `clave`; aporta la clave fiscal. Omitida, `null` o `""` equivale a no enviarla; `false` se rechaza. Si se repite con otro alias de clave, deben coincidir. | `"CLAVE_FISCAL_FICTICIA"` |
| `credentials` | `contrasena` | texto | No | ninguno | Alias opcional de `clave`; aporta la clave fiscal. Omitida, `null` o `""` equivale a no enviarla; `false` se rechaza. Si se repite con otro alias de clave, deben coincidir. | `"CLAVE_FISCAL_FICTICIA"` |
| `credentials` | `clave_encriptada` | texto | No | ninguno | Alternativa cifrada en Base64 que se descifra mediante el mecanismo admitido por la API. Omitida, `null` o `""` equivale a ausencia; `false` o cifrado inválido se rechaza. Si se envía con una clave en texto, ambas deben coincidir. | `"Q0xBVkUtRklDVElDSUEtRkFDVElCSUxFLU5PLVVUSUxJWkFCTEU="` |

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