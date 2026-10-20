# Mis Facilidades

## Propósito y operación

Consulta planes de facilidades de pago, sus cuotas y obligaciones, exportando la información disponible. No adhiere, modifica, cancela ni paga planes.

Ruta V3: `POST /api/v3/bots/mis_facilidades/consultar`. Alias compatible: `POST /api/v3/mis_facilidades/consulta`. Envíe `{"payload": {...}, "credentials": {...}}`; la operación forma parte de la ruta. El trabajo se acepta de forma asíncrona (`202`) y se sigue con `GET /api/v3/jobs/{job_id}`.

## Payload

| Campo | Tipo; requerido; default | Efecto y comportamiento al omitir, enviar `null`, vacío o `false` |
|---|---|---|
| `representado_cuit` | texto de 11 dígitos; opcional; `null` | Selecciona el representado. Alias `cuit`; elimina espacios, guiones, puntos y `/`. Si se omite o es `null`, se usa el CUIT de `credentials.cuit_representante`. `""` no equivale a omisión: falla el formato. |
| `denominacion` | texto 1–128; opcional; `null` | Nombre alternativo usado al identificar/nombrar el representado. Omitido o `null`: sin sobreescritura. Cadena vacía: rechazo. |
| `situacion_excluyente` | lista de textos; opcional; `[]`; máximo 20 | Excluye planes cuya situación contenga alguno de los textos, sin distinguir mayúsculas/minúsculas. Alias compatible `lista_exclusion_situacion`. Omitido o `[]`: no excluir por situación. `null`, elemento vacío o más de 20 elementos: rechazo. |
| `incluir_pdf` | booleano; opcional; `true` | Produce PDF para las vistas de plan disponibles. `false` omite los PDF. `null` se rechaza. |
| `incluir_xlsx` | booleano; opcional; `true` | Produce libro de cálculo con las tablas disponibles. `false` omite el libro. `null` se rechaza. |
| `subir_archivos` | booleano; opcional; `true` | Adjunta los archivos producidos. `false` evita los adjuntos, pero no desactiva la consulta ni la generación de las salidas solicitadas. `null` se rechaza. |

Debe estar activo `incluir_pdf` o `incluir_xlsx`; ambas opciones en `false` se rechazan. Los campos no reconocidos se rechazan.

## Credenciales

Se necesitan credenciales fiscales funcionales y permiso de consulta sobre el representado. La cuenta de acceso debe poder abrir Mis Facilidades.

| Campo en el sobre/`credentials` | Tipo; requisito; default | Efecto al enviarlo; omisión, `null`, vacío o `false` |
|---|---|---|
| `credentials` | objeto JSON; opcional; default `{}` | Omitirlo usa `{}`. `{}` pasa el sobre pero no aporta acceso. `null`, `""` o el booleano `false` se rechazan por tipo. |
| `credentials.cuit_representante` | texto; requerido junto con una clave, salvo alias; sin default | Identifica la cuenta que inicia sesión. Omitido, `null` o `""` no aporta identidad; el booleano `false` se rechaza por tipo. |
| `credentials.cuit_inicio_sesion` | texto; alias opcional de `cuit_representante`; sin default | Aporta la misma identidad. Omitido, `null` o `""` equivale a no enviarlo; `false` se rechaza. Si se envía con el otro nombre, los valores deben coincidir. |
| `credentials.clave` | texto; requerida junto con una identidad, salvo alias o alternativa cifrada; sin default | Clave fiscal en texto. Omitida, `null` o `""` no aporta clave; `false` se rechaza por tipo. |
| `credentials.clave_representante` | texto; alias opcional de `clave`; sin default | Aporta la clave fiscal. Omitida, `null` o `""` equivale a no enviarla; `false` se rechaza. Si se repite con otro alias de clave, deben coincidir. |
| `credentials.contrasena` | texto; alias opcional de `clave`; sin default | Aporta la clave fiscal. Omitida, `null` o `""` equivale a no enviarla; `false` se rechaza. Si se repite con otro alias de clave, deben coincidir. |
| `credentials.clave_encriptada` | texto Base64 cifrado; alternativa opcional; sin default | Se descifra mediante el mecanismo admitido por la API. Omitida, `null` o `""` equivale a ausencia; `false` o cifrado inválido se rechaza. Si se envía con una clave en texto, ambas deben coincidir. |

Los valores de alias duplicados deben coincidir. Envíe estos datos dentro de `credentials`, no en `payload`.

## Ejemplo ficticio válido

```json
{
  "payload": {
    "representado_cuit": "30876543210",
    "denominacion": "Comercio Ejemplo SA",
    "situacion_excluyente": ["Plan Cancelado"],
    "incluir_pdf": true,
    "incluir_xlsx": true,
    "subir_archivos": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "Clave-Ficticia-No-Usar-2026"
  }
}
```

## Resultado y archivos

El resultado lista los CUIT disponibles, los planes examinados y procesados, y los errores asociados a planes o secciones. Los planes cancelados se excluyen; además, se excluye cualquier plan cuya situación coincida con `situacion_excluyente`. Para cada plan procesado se pueden generar PDFs y un archivo XLSX. Los adjuntos aparecen en `files[]` si `subir_archivos` está activo.

Si algunas exportaciones fallan y otras continúan, el trabajo puede terminar `PARCIAL`, con errores por plan/sección. Una consulta sin errores termina `OK`. El resultado no garantiza que cada plan tenga todas las vistas exportables.