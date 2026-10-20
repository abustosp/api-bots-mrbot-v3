# Mis Retenciones

## Propósito y operación

Consulta retenciones y percepciones de impuestos seleccionados para un representado y período; genera archivos de salida. La operación es de consulta, no de modificación.

Ruta canónica: `POST /api/v3/bots/mis_retenciones/consultar`. Alias compatible: `POST /api/v3/mis_retenciones/consulta`. Envíe `{"payload": {...}, "credentials": {...}}`; la operación se fija en la ruta. El trabajo se acepta con `202` y puede consultarse en `GET /api/v3/jobs/{job_id}`.

## Payload

| Campo | Tipo; requerido; default | Efecto y comportamiento al omitir, enviar `null`, vacío o `false` |
|---|---|---|
| `representado_cuit` | texto de 11 dígitos; requerido | CUIT consultado; alias `cuit`. Se quitan espacios, guiones, puntos y `/`. Omitido, `null`, vacío o inválido: rechazo. |
| `representado_nombre` | texto 1–128; requerido | Nombre para la consulta y los archivos. `denominacion` es alias compatible. Omitido, `null` o vacío: rechazo. |
| `fecha_desde` | fecha `dd/mm/aaaa`; requerida | Inicio del período. Alias `desde`. Debe ser fecha real. Omitida, `null`, vacía o inválida: rechazo. |
| `fecha_hasta` | fecha `dd/mm/aaaa`; requerida | Fin inclusive, no anterior a `fecha_desde`. Alias `hasta`. Omitida, `null`, vacía o inválida: rechazo. Si queda en el futuro, el portal limita la fecha final a hoy. |
| `impuestos` | lista de textos; opcional; `null`; máximo 6 | Códigos admitidos: `216`, `217`, `219`, `353`, `767`, `787`. Omitido, `null` o `[]`: consulta el conjunto predeterminado (los seis códigos en exportación normal; en modo aplicativo se limita al conjunto disponible para ese formato). Elementos se convierten a texto y se recortan; código desconocido o más de 6 elementos: rechazo. |
| `tipos` | lista de `"Retencion"` y/o `"Percepcion"`; opcional; [`"Retencion"`, `"Percepcion"`] | Selecciona tipos. Debe tener al menos un valor; `null`, `[]` o cualquier otra etiqueta: rechazo. Los códigos `216` y `787` solo admiten `Retencion`; los demás admiten ambos tipos. Una selección incompatible con cualquier impuesto elegido se rechaza. |
| `exportar_para_aplicativo` | booleano; opcional; `false` | `true` produce las variantes de exportación para aplicativo, usando los códigos admitidos en ese modo; el código `787` se rechaza si se solicita expresamente. `false` genera la salida CSV estándar. `null` se rechaza. |
| `incluir_json` | booleano; opcional; `true` | Incluye muestra/resumen de filas. `false` omite la muestra, no la consulta. `null` se rechaza. |
| `subir_csv` | booleano; opcional; `true` | Adjunta archivos producidos. `false` evita los adjuntos, no la consulta ni la generación necesaria. `null` se rechaza. |

Debe quedar activo `incluir_json` o `subir_csv`; si ambos son `false`, se rechaza. Los valores de `tipos` distinguen exactamente `Retencion` y `Percepcion`. Se aceptan claves de payload adicionales, pero no se documenta ni garantiza efecto alguno para ellas.

## Credenciales

Se requieren credenciales fiscales válidas y autorización sobre el representado.

| Campo en el sobre/`credentials` | Tipo; requisito; default | Efecto al enviarlo; omisión, `null`, vacío o `false` |
|---|---|---|
| `credentials` | objeto JSON; opcional; default `{}` | Omitirlo usa `{}`. `{}` pasa el sobre pero no aporta acceso. `null`, `""` o el booleano `false` se rechazan por tipo. |
| `credentials.cuit_representante` | texto; requerido junto con una clave, salvo alias; sin default | Identifica la cuenta que inicia sesión. Omitido, `null` o `""` no aporta identidad; el booleano `false` se rechaza por tipo. |
| `credentials.cuit_inicio_sesion` | texto; alias opcional de `cuit_representante`; sin default | Aporta la misma identidad. Omitido, `null` o `""` equivale a no enviarlo; `false` se rechaza. Si se envía con el otro nombre, los valores deben coincidir. |
| `credentials.clave` | texto; requerida junto con una identidad, salvo alias o alternativa cifrada; sin default | Clave fiscal en texto. Omitida, `null` o `""` no aporta clave; `false` se rechaza por tipo. |
| `credentials.clave_representante` | texto; alias opcional de `clave`; sin default | Aporta la clave fiscal. Omitida, `null` o `""` equivale a no enviarla; `false` se rechaza. Si se repite con otro alias de clave, deben coincidir. |
| `credentials.contrasena` | texto; alias opcional de `clave`; sin default | Aporta la clave fiscal. Omitida, `null` o `""` equivale a no enviarla; `false` se rechaza. Si se repite con otro alias de clave, deben coincidir. |
| `credentials.clave_encriptada` | texto Base64 cifrado; alternativa opcional; sin default | Se descifra mediante el mecanismo admitido por la API. Omitida, `null` o `""` equivale a ausencia; `false` o cifrado inválido se rechaza. Si se envía con una clave en texto, ambas deben coincidir. |

No envíe credenciales en `payload`. Los nombres alternativos repetidos deben contener el mismo valor.

## Ejemplo ficticio válido

```json
{
  "payload": {
    "representado_cuit": "30876543210",
    "representado_nombre": "Comercio Ejemplo SA",
    "fecha_desde": "01/01/2025",
    "fecha_hasta": "31/01/2025",
    "impuestos": ["217"],
    "tipos": ["Retencion", "Percepcion"],
    "exportar_para_aplicativo": false,
    "incluir_json": true,
    "subir_csv": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "Clave-Ficticia-No-Usar-2026"
  }
}
```

## Resultado y archivos

`data.impuestos` organiza resultados por código de impuesto, tipo y formato. Las entradas resumen filas y nombre de archivo; si `incluir_json` está activo, puede incluir muestra, tamaño y huella del archivo. `files[]` contiene archivos adjuntos cuando `subir_csv` está activo.

Los fallos de una combinación impuesto/tipo/formato se registran dentro de `data.errores` y el bot continúa con las demás combinaciones. El estado global puede ser `OK` aunque se informen errores por combinación; compruebe `data.errores` y las cantidades por salida. Si ARCA no confirma el representado, el resultado puede incluir un aviso y consultar la relación activa.