# Retenciones y percepciones IIBB de AGIP

**Propósito.** Consultar el servicio de AGIP para un CUIT representado y un rango de períodos, y obtener un paquete con los reportes disponibles de retenciones/percepciones.

## Operación y ruta V3

Catálogo: `retper_iibb_agip`, operación `consultar`.

- Canónica: `POST /api/v3/bots/retper_iibb_agip/consultar`.
- Alias V3: `POST /api/v3/retenciones_percepciones_iibb/agip/consulta`.

Enviar `{ "payload": { ... }, "credentials": { ... } }`. La aceptación devuelve `job_id`; el resultado se consulta en `/api/v3/jobs/{job_id}`.

## Credenciales

Se requiere la clave de acceso y CUIT representante. Usar `credentials.clave` y `credentials.cuit_representante`; se aceptan `clave_representante` o `contrasena` como nombres alternativos coincidentes, o una `clave_encriptada` válida en Base64. `cuit_inicio_sesion` es alias del CUIT de sesión. El nombre de usuario de AGIP se puede especificar en `payload.usuario`; si se omite, se usa el CUIT de la sesión. La clave omitida, nula o vacía no permite ejecutar. Un usuario opcional nulo se trata como ausente; vacío se rechaza. `pv` selecciona una modalidad alternativa de presentación, no cambia la consulta ni el formato del resultado.

## Campos del payload

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `representado_cuit` | texto | Sí | ninguno | CUIT de 11 dígitos; elimina espacios, guiones, puntos y `/`. Alias: `cuit`, `cuit_representado`. Vacío o `null` inválidos. Identifica la cuenta para el reporte. | `"20-12345678-6"` |
| `usuario` | texto | No | `null` | Usuario ClaveCiudad de 1–256 caracteres si se informa. Omitir o `null` recurre al CUIT representante. Vacío inválido. | `"20123456786"` |
| `denominacion` | texto | Sí | ninguno | Razón social de 1–256 caracteres requerida para buscar y nombrar los reportes. Vacío o `null` inválidos. No se traduce automáticamente desde `representado_nombre`. | `"Comercial Ficticia SA"` |
| `periodo_desde` | texto | Sí | ninguno | Se eliminan caracteres no numéricos, se toman los primeros seis dígitos como `AAAAMM` y se valida mes 01–12. Puede aceptar separadores. Alias: `desde`. Vacío, menos de seis dígitos o mes inválido se rechazan. | `"202601"` |
| `periodo_hasta` | texto | Sí | ninguno | Misma normalización que `periodo_desde`; no puede preceder a `periodo_desde`. Alias: `hasta`. | `"202603"` |
| `incluir_json` | booleano | No | `true` | Se valida y participa en la regla de al menos una salida, pero actualmente no controla qué datos JSON se devuelven. `false` no quita los campos de respuesta; omitido equivale a `true`; `null` inválido. | `true` |
| `subir_archivo` | booleano | No | `true` | `true` publica un archivo ZIP; `false` evita su publicación y no cambia la consulta. Omitido equivale a `true`; `null` inválido. | `true` |
| `pv` | booleano | No | `false` | `true` selecciona la variante alternativa de presentación; `false` o ausente usa la modalidad habitual. `null` equivale a `false`. En esta entrada, textos como `"true"`, `"1"`, `"si"`, `"yes"`, `"on"` (ignorando mayúsculas y espacios) son verdaderos; otros textos son falsos. | `false` |

No se aceptan campos adicionales. `clave`, `clave_representante`, `clave_encriptada`, `cuit_representante`, `cuit_inicio_sesion`, `proxy` y `proxy_request` dentro del payload se descartan por compatibilidad y no reemplazan las credenciales separadas. Debe elegirse al menos una salida (`incluir_json=true` o `subir_archivo=true`).

## Ejemplo ficticio válido

```json
{
  "payload": {
    "representado_cuit": "20-12345678-6",
    "usuario": "20123456786",
    "denominacion": "Comercial Ficticia SA",
    "periodo_desde": "202601",
    "periodo_hasta": "202603",
    "incluir_json": true,
    "subir_archivo": true,
    "pv": false
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "CLAVE_FISCAL_FICTICIA"
  }
}
```

## Resultado, archivos y límites

En la respuesta de `GET /api/v3/jobs/{job_id}`, `result` indica el estado final, `data` describe el paquete y `files` enumera los archivos publicados. `files` es una lista vacía cuando no hay archivos publicados.

| Ruta | Tipo | Condición de presencia | Descripción | Ejemplo |
|---|---|---|---|---|
| `result` | texto | Al finalizar el trabajo | `OK` al completar la consulta; `ERROR` si falla la consulta o la publicación. La operación no devuelve un resultado `PARCIAL`. | `"OK"` |
| `data.operacion` | texto | En un resultado de operación disponible | Nombre de la operación. | `"consultar"` |
| `data.representado_cuit` | texto | En un resultado de operación disponible | CUIT consultado, normalizado a 11 dígitos. | `"20123456786"` |
| `data.periodo_desde`, `data.periodo_hasta` | texto | En un resultado de operación disponible | Primer y último período consultados. | `{"periodo_desde":"202601","periodo_hasta":"202603"}` |
| `data.tipo` | texto | En un resultado de operación disponible | Identifica el paquete de reportes. | `"RETPER IIBB AGIP"` |
| `data.archivo` | texto | En un resultado de operación disponible | Nombre del paquete ZIP generado, incluso si no se publica. | `"retper-agip-demo.zip"` |
| `data.archivos` | lista de textos | En un resultado de operación disponible | Nombres de los archivos incluidos dentro del ZIP. No contiene datos tabulares por fila. | `["retenciones-demo.xlsx","percepciones-demo.txt"]` |
| `data.sha256`, `data.size_bytes` | texto, entero | Solo si `subir_archivo=true` y la publicación del ZIP tuvo éxito | Huella y tamaño del ZIP publicado. | `"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"`, `24576` |
| `files[]` | lista de archivos | Siempre en la respuesta del trabajo; contiene el ZIP si se publicó | Referencia pública del ZIP. Sin publicación, el valor es `[]`. | `[{"name":"retper-agip-demo.zip","url":"","filename":"retper-agip-demo.zip","size":24576,"size_bytes":24576}]` |

La consulta puede agrupar los formatos que el portal ofrezca para retenciones, percepciones, retenciones bancarias, percepciones aduaneras y retenciones SIRTAC. Si el portal no produce reportes o falla una fase obligatoria, no se devuelve una salida parcial. El servicio puede bloquear temporalmente una consulta válida. Es una consulta de reportes; no presenta declaraciones ni genera pagos.
