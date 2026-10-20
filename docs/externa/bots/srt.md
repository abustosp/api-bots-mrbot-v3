# Consulta de alícuotas SRT

**Propósito.** Consultar alícuotas ART por lote de CUIT, hasta 50 elementos, devolviendo el estado y los datos tabulares disponibles para cada CUIT.

## Operación y ruta V3

Catálogo: `srt`, operación `consultar_alicuotas`.

- Canónica: `POST /api/v3/bots/srt/consultar_alicuotas`.
- Alias V3: `POST /api/v3/srt/alicuotas/consulta`.

Enviar `{ "payload": { ... }, "credentials": { ... } }`. Consultar el resultado con `GET /api/v3/jobs/{job_id}`.

## Credenciales

Se requiere clave fiscal y CUIT representante para iniciar la sesión; las convenciones completas están en [API pública](../api.md). `clave` es texto no vacío, aliases `clave_representante` y `contrasena` deben coincidir; se admite clave cifrada `clave_encriptada`. Omitir/null/vacío no permite ejecución. `cuit_representante` (alias `cuit_inicio_sesion`) no tiene default y se requiere para autenticación.

## Campos del payload

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `cuits_consulta` | lista de textos | Sí | ninguno | Lista de 1–50 CUIT. Cada valor se normaliza quitando espacios, guiones, puntos y `/`, y debe resultar en 11 dígitos. Los duplicados se eliminan conservando el primer orden. Omitir, `null`, lista vacía o elemento inválido rechaza. | `["20-12345678-6","27-11122233-4"]` |
| `incluir_json` | booleano | No | `true` | `false` omite `consultas` del JSON inline, no cambia las consultas. Omitir equivale a `true`; `null` inválido. | `true` |
| `subir_json` | booleano | No | `true` | `true` publica el consolidado JSON; `false` no lo publica. Omitir equivale a `true`; `null` inválido. | `true` |

Se requiere al menos una salida (`incluir_json=true` o `subir_json=true`). No se aceptan otros campos del payload. El catálogo de entrada omite erróneamente `cuits_consulta` de la lista `required`, pero la validación efectiva sí la exige; envíela siempre.

## Ejemplo ficticio válido

```json
{
  "payload": {
    "cuits_consulta": ["20-12345678-6", "27-11122233-4"],
    "incluir_json": true,
    "subir_json": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "CLAVE_FISCAL_FICTICIA"
  }
}
```

## Resultado y resultados parciales

`data` siempre informa `operacion`, `cantidad`, `consultas_ok` y `consultas_error`. Si `incluir_json=true`, `consultas` es una lista con `cuit`, `status`, `message` y `data`. `status` es `OK`, `SIN_AFILIACION_VIGENTE` o `ERROR`; la ausencia de afiliación vigente se considera consulta exitosa, no error. Si se publica el archivo consolidado también se devuelven `sha256` y `size_bytes`.

El lote continúa después de errores individuales; el resultado se marca `PARCIAL` y cada CUIT fallido queda como `ERROR`. Aunque no se publique ni se incluya JSON, se prepara igualmente el consolidado durante la ejecución; sin una salida disponible se solicita fallar por validación. El archivo se limita actualmente recortando el texto al primer MiB, lo que puede dejar JSON incompleto para lotes voluminosos; no dependa de que el consolidado truncado sea parseable. Esta consulta de datos no modifica obligaciones.
