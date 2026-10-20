# SIFERE

**Propósito.** Consultar retenciones y percepciones en SIFERE Web/COMARB para un período, por jurisdicción y consolidados disponibles.

## Operación y ruta V3

Catálogo: `sifere`, operación `consultar`.

- Canónica: `POST /api/v3/bots/sifere/consultar`.
- Alias V3: `POST /api/v3/sifere/consulta`.

Enviar `{ "payload": { ... }, "credentials": { ... } }`. La aceptación devuelve `job_id`; el resultado se consulta en `/api/v3/jobs/{job_id}`.

## Credenciales

Se necesita clave fiscal y CUIT representante. Convenciones de envío, nombres alternativos, clave cifrada y tratamiento de ausencias: [API pública](../api.md). Este bot inicia sesión con la clave y usa el CUIT consultado del payload; la clave no se envía como campo de consulta. Una credencial vacía/no informada no permite iniciar la consulta.

| Campo de credencial | Tipo, requerido, default y tratamiento |
|---|---|
| `clave` | Texto no vacío para ejecutar. `clave_representante` y `contrasena` son aliases; si hay varias deben coincidir. `clave_encriptada` es alternativa cifrada. Omitido/null/vacío deja el job sin credencial ejecutable; `false` no es texto válido. |
| `cuit_representante` | Texto con CUIT de la sesión, sin default de aplicación. `cuit_inicio_sesion` es alias. Omitido/null/vacío se elimina al normalizar; la autenticación requiere un representante disponible. |

## Campos del payload

| Campo | Tipo, requerido y default | Efecto y tratamiento de omisión/null/vacío/false |
|---|---|---|
| `representado_cuit` | texto requerido; aliases `cuit`, `cuit_representado` | CUIT de 11 dígitos; quita espacios, guiones, puntos y `/`. Vacío/null inválidos. |
| `periodo` | texto requerido, seis dígitos `AAAAMM` | Mes 01–12. Vacío/null/separadores inválidos. |
| `representado_nombre` | texto nullable, opcional, default `null`, máximo 256; alias `representado` | Nombre para rótulos de archivo; vacío/null se convierte a ausencia y el nombre usa el CUIT. |
| `jurisdicciones` | lista de enteros nullable, default `null` | `null`/omitir selecciona todas las jurisdicciones 901–924. Lista vacía no consulta SIRCAR por jurisdicción. Valores fuera de 901–924 se eliminan silenciosamente; los restantes se deduplican y ordenan. |
| `incluir_json` | booleano, default `true` | `false` omite `jurisdicciones` y `consolidados` de `data`, no impide la consulta ni descargas. `null` inválido. |
| `subir` | booleano, default `true`; alias `carga_minio` | `true` publica reportes, `false` no. Omitir = `true`; `null` inválido. |
| `consolidados` | booleano, default `true` | Solicita además SIRCREB y SIRTAC; `false` los desactiva. Omitir = `true`; `null` inválido. |

No se aceptan campos adicionales excepto los cuatro nombres de sesión/clave/proxy que se descartan por compatibilidad. Se exige al menos una salida (`incluir_json=true` o `subir=true`). Aunque `jurisdicciones=[]`, `consolidados=true` mantiene la consulta consolidada.

## Ejemplo ficticio válido

```json
{
  "payload": {
    "representado_cuit": "20-12345678-6",
    "periodo": "202601",
    "representado_nombre": "Comercial Ficticia SA",
    "jurisdicciones": [901, 902],
    "incluir_json": true,
    "subir": true,
    "consolidados": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "CLAVE_FISCAL_FICTICIA"
  }
}
```

## Resultado y archivos

`data` contiene `operacion`, `representado_cuit`, `periodo`; cuando `incluir_json=true`, `jurisdicciones` es una lista de entradas con `jurisdiccion` (entero), `metodo` (`retencion`/`percepcion`), `etiqueta` y `archivos`. Cada archivo tiene `archivo` (nombre), `tipo` y, si se publica, `sha256` y `size_bytes`. `consolidados` lista los nombres de archivos adicionales y metadatos de publicación. SIRCAR puede ofrecer XLSX y PDF; los consolidados corresponden a informes y tablas SIRCREB/SIRTAC. Los reportes obtenidos se conservan si otra consulta falla: el resultado entonces es `PARCIAL` con errores no fatales. Error al subir un artefacto puede terminar el job pese a descargas previas.