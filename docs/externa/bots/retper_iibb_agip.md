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

`data` incluye operación, CUIT representado, períodos, tipo de reporte, nombre del ZIP y nombres de sus archivos internos. El paquete agrupa formatos de hoja de cálculo y texto de las secciones de retenciones, percepciones, retenciones bancarias, percepciones aduaneras y retenciones SIRTAC que el portal ofrezca para el rango. Si se publica el ZIP, también se devuelven huella y tamaño. No se informan registros estructurados por fila.

La respuesta se marca `OK` al completar; si el portal no produce archivos o falla la carga, no se devuelve un resultado parcial. El servicio de AGIP puede bloquear temporalmente la consulta, por lo que un trabajo puede fallar aunque las entradas sean válidas. Es una consulta de reportes; no presenta declaraciones ni genera pagos.
