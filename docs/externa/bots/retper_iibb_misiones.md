# Retenciones y percepciones IIBB de Misiones

**Propósito.** Consultar retenciones/percepciones de Ingresos Brutos de ATM Misiones para un CUIT y período, generando reporte de hoja de cálculo y PDF.

## Operación y ruta V3

Catálogo: `retper_iibb_misiones`, operación `consultar`.

- Canónica: `POST /api/v3/bots/retper_iibb_misiones/consultar`.
- Alias V3: `POST /api/v3/retenciones_percepciones_iibb/misiones/consulta`.

Enviar `{ "payload": { ... }, "credentials": { ... } }`. Una aceptación devuelve `job_id`; consultar resultado en `/api/v3/jobs/{job_id}`.

## Credenciales

La extranet exige CUIT representante y clave fiscal: `credentials.cuit_representante` y `credentials.clave`. `clave_representante` y `contrasena` son alternativas equivalentes a la clave si coinciden; también se acepta `clave_encriptada` cifrada Base64. El alias `cuit_inicio_sesion` identifica el CUIT de sesión. Si no se envía una clave no vacía, el trabajo no se puede ejecutar; si falta CUIT de sesión, la autenticación tampoco puede completarse. El CUIT consultado debe enviarse como `payload.representado_cuit`. `credentials.cuit_representante` autentica y no completa ese campo automáticamente. El alias `cuit_representante` solo puede servir como dato consultado si llega dentro del payload antes de separar credenciales.

## Campos del payload

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `representado_cuit` | texto | Sí | ninguno | CUIT de 11 dígitos; se quitan espacios, guiones, puntos y `/`. Alias: `cuit`, `cuit_representante`, `cuit_representado`. Vacío o `null` inválidos. El alias `cuit_representante` tiene además significado histórico como CUIT a consultar. | `"20-12345678-6"` |
| `denominacion` | texto | Sí | ninguno | Nombre de 1–256 caracteres utilizado para la consulta y los reportes. Alias: `representado_nombre`. Vacío o `null` inválidos. | `"Contribuyente Ficticio SRL"` |
| `periodo_desde` | texto | Sí | ninguno | Exactamente seis dígitos `AAAAMM`, mes 01–12. No admite separadores. Alias: `desde`. Vacío, `null` o formato inválido se rechazan. | `"202601"` |
| `periodo_hasta` | texto | Sí | ninguno | Exactamente seis dígitos `AAAAMM`, mes 01–12 y no anterior a `periodo_desde`. Alias: `hasta`. | `"202603"` |
| `incluir_json` | booleano | No | `true` | Se valida, pero no suprime actualmente los datos JSON que se devuelven. `false` no altera esa salida; omitido equivale a `true`; `null` inválido. | `true` |
| `subir_archivo` | booleano | No | `true` | `true` publica XLSX y PDF; `false` evita publicarlos. Omitido equivale a `true`; `null` inválido. | `true` |

No se aceptan campos adicionales. Los campos históricos de secreto/sesión/proxy (`clave`, `clave_representante`, `clave_encriptada`, `cuit_inicio_sesion`, `proxy`, `proxy_request`) dentro del payload se eliminan y no sustituyen `credentials`. Se necesita al menos una salida según validación (`incluir_json=true` o `subir_archivo=true`).

## Ejemplo ficticio válido

```json
{
  "payload": {
    "representado_cuit": "20-12345678-6",
    "denominacion": "Contribuyente Ficticio SRL",
    "periodo_desde": "202601",
    "periodo_hasta": "202603",
    "incluir_json": true,
    "subir_archivo": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "CLAVE_FISCAL_FICTICIA"
  }
}
```

## Resultado, archivos y límites

El resultado indica operación, CUIT, rango y nombres del XLSX y PDF; si se publican, se adjuntan sus huellas y tamaños. El XLSX se valida como archivo no vacío y el PDF como documento PDF. El campo `incluir_json` no elimina la respuesta estructurada pese a su nombre. Si cualquiera de las fases obligatorias de generación falla, no hay resultado parcial; un fallo al publicar puede ocurrir después de que el portal haya generado ambos reportes. El trámite consulta información, no liquida ni paga obligaciones.
