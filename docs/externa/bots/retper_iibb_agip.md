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

| Campo | Tipo, requerido y default | Efecto y tratamiento de omisión/null/vacío/false |
|---|---|---|
| `representado_cuit` | texto, requerido; aliases `cuit`, `cuit_representado` | CUIT de 11 dígitos; elimina espacios, guiones, puntos y `/`. Vacío/null inválidos. Identifica la cuenta para el reporte. |
| `usuario` | texto nullable, opcional; default `null`, 1–256 caracteres si se informa | Usuario ClaveCiudad. Omitir/null recurre al CUIT representante. Vacío inválido. |
| `denominacion` | texto, requerido, 1–256 caracteres | Razón social requerida para buscar/nombrar los reportes. Vacío/null inválidos. No se traduce automáticamente desde `representado_nombre`. |
| `periodo_desde` | texto, requerido; alias `desde` | Se eliminan caracteres no numéricos, se toman los primeros seis dígitos como `AAAAMM` y se valida mes 01–12. Puede aceptar separadores. Vacío, menos de seis dígitos o mes inválido se rechazan. |
| `periodo_hasta` | texto, requerido; alias `hasta` | Misma normalización; no puede preceder a `periodo_desde`. |
| `incluir_json` | booleano, default `true` | Se valida y participa en la regla de al menos una salida, pero actualmente no controla qué datos JSON se devuelven. `false` no quita los campos de respuesta; omitido = `true`; `null` inválido. |
| `subir_archivo` | booleano, default `true`; aliases `carga_minio`, `minio_upload`, `archivo_historico_minio` | `true` publica un archivo ZIP; `false` evita su publicación y no cambia la consulta. Omitido = `true`; `null` inválido. |
| `pv` | booleano, default `false` | `true` selecciona la variante alternativa de presentación; `false` o ausente usa la modalidad habitual. `null` equivale a `false`. En esta entrada, textos como `"true"`, `"1"`, `"si"`, `"yes"`, `"on"` (ignorando mayúsculas/espacios) son verdaderos; otros textos falsos. |

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