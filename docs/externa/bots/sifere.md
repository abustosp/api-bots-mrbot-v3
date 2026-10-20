# SIFERE

**Propósito.** Consultar retenciones y percepciones en SIFERE Web/COMARB para un período, por jurisdicción y consolidados disponibles.

## Operación y ruta V3

Catálogo: `sifere`, operación `consultar`.

- Canónica: `POST /api/v3/bots/sifere/consultar`.
- Alias V3: `POST /api/v3/sifere/consulta`.

Enviar `{ "payload": { ... }, "credentials": { ... } }`. La aceptación devuelve `job_id`; el resultado se consulta en `/api/v3/jobs/{job_id}`.

## Credenciales

Se necesita clave fiscal y CUIT representante. Convenciones de envío, nombres alternativos, clave cifrada y tratamiento de ausencias: [API pública](../api.md). Este bot inicia sesión con la clave y usa el CUIT consultado del payload; la clave no se envía como campo de consulta. Una credencial vacía/no informada no permite iniciar la consulta.

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `clave` | texto | Sí | ninguno | Texto no vacío para ejecutar. Alias: `clave_representante`, `contrasena`; si hay varias deben coincidir. `clave_encriptada` es alternativa cifrada. Omitido, `null` o vacío deja el job sin credencial ejecutable; `false` no es texto válido. | `"CLAVE_FISCAL_FICTICIA"` |
| `cuit_representante` | texto | Sí | ninguno | CUIT de la sesión, sin default de aplicación. Alias: `cuit_inicio_sesion`. Omitido, `null` o vacío se elimina al normalizar; la autenticación requiere un representante disponible. | `"20123456786"` |

## Campos del payload

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `representado_cuit` | texto | Sí | ninguno | CUIT de 11 dígitos; quita espacios, guiones, puntos y `/`. Alias: `cuit`, `cuit_representado`. Vacío o `null` inválidos. | `"20-12345678-6"` |
| `periodo` | texto | Sí | ninguno | Seis dígitos `AAAAMM`, mes 01–12. Vacío, `null` o separadores inválidos se rechazan. | `"202601"` |
| `representado_nombre` | texto | No | `null` | Máximo 256 caracteres; alias `representado`. Nombre para rótulos de archivo; vacío o `null` se convierte a ausencia y el nombre usa el CUIT. | `"Comercial Ficticia SA"` |
| `jurisdicciones` | lista de enteros | No | `null` | `null` u omitir selecciona todas las jurisdicciones 901–924. Lista vacía no consulta SIRCAR por jurisdicción. Valores fuera de 901–924 se eliminan silenciosamente; los restantes se deduplican y ordenan. | `[901, 902]` |
| `incluir_json` | booleano | No | `true` | `false` omite `jurisdicciones` y `consolidados` de `data`, no impide la consulta ni las descargas. `null` inválido. | `true` |
| `subir` | booleano | No | `true` | `true` publica reportes; `false` no. Omitir equivale a `true`; `null` inválido. | `true` |
| `consolidados` | booleano | No | `true` | Solicita además SIRCREB y SIRTAC; `false` los desactiva. Omitir equivale a `true`; `null` inválido. | `true` |

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
