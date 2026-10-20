# SIPER

**Propósito.** Consultar la conducta fiscal SIPER del CUIT indicado, capturando la vista de detalle y/o la de categorías. También puede devolver campos leídos del detalle.

## Operación y ruta V3

Catálogo: `siper`, operación `consultar`.

- Canónica: `POST /api/v3/bots/siper/consultar`.
- Alias V3: `POST /api/v3/siper/consulta`.

Cuerpo canónico: `{ "payload": { ... }, "credentials": { ... } }`; el alias de compatibilidad recibe los campos en un cuerpo plano. No mezclar ambas formas. Consultar ejecución aceptada en `/api/v3/jobs/{job_id}`. Convenciones compartidas de credenciales: [API pública](../api.md).

## Credenciales

Requiere clave fiscal y CUIT representante; consulte las convenciones comunes en [API pública](../api.md). Enviar `credentials.clave` y `credentials.cuit_representante`; los campos ausentes, nulos o vacíos no dan acceso. `clave_representante`/`contrasena` son aliases de clave que deben coincidir; se admite alternativa `clave_encriptada`. `cuit_inicio_sesion` es alias del CUIT representante. El CUIT consultado se pasa aparte.

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `clave` | texto | Sí | ninguno | Texto no vacío, requerido para ejecutar. Omitir, `null` o `""` impide la autenticación; `false` no es texto válido. Alias: `clave_representante`, `contrasena`; alternativa cifrada `clave_encriptada`. | `"CLAVE_FISCAL_FICTICIA"` |
| `cuit_representante` | texto | Sí | ninguno | CUIT representante para la sesión, sin default de aplicación. Omitir, `null` o `""` no aporta CUIT. Alias: `cuit_inicio_sesion`. | `"20123456786"` |

## Campos del payload

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `representado_cuit` | texto | Sí | ninguno | CUIT de 11 dígitos con separadores comunes eliminados. Alias: `cuit`. Vacío o `null` inválidos. En la ruta canónica debe enviarse explícitamente dentro de `payload`; el CUIT dentro de `credentials` solo autentica y no es fallback automático para este campo. | `"20-12345678-6"` |
| `incluir_detalle` | booleano | No | `true` | Captura la vista del detalle. `false` no la captura. Omitir equivale a `true`; `null` inválido. | `true` |
| `incluir_categorias` | booleano | No | `true` | Captura la vista de categorías. `false` no la captura. Omitir equivale a `true`; `null` inválido. | `true` |
| `incluir_json` | booleano | No | `true` | `false` quita la lista `vistas`, pero no elimina necesariamente `campos` del detalle. Omitir equivale a `true`; `null` inválido. | `true` |
| `subir` | booleano | No | `false` | Controla la publicación de las capturas, no qué vistas se consultan. `false` no publica; `null` inválido. En la API V3, si se omite `subir`, la normalización establece `subir=false`, incluso en ruta canónica. Debe enviarse explícitamente para publicar capturas. | `true` |
| `extraer_campos` | booleano | No | `true` | Intenta devolver campos estructurados desde el detalle; `false` evita esa extracción. Omitir equivale a `true`; `null` inválido. | `true` |

Debe solicitarse al menos una vista y una salida (`incluir_json=true` o `subir=true`). La normalización V3 agrega ambas vistas cuando no se especifican; `incluir_json=true` mantiene una salida de datos cuando la publicación queda en `false` por default efectivo. No se aceptan otros campos. Use `payload.representado_cuit` explícitamente, incluso si `credentials.cuit_representante` está presente. Envíe un solo nombre canónico por campo para evitar ambigüedades.

## Ejemplo ficticio válido

```json
{
  "payload": {
    "representado_cuit": "20-12345678-6",
    "incluir_detalle": true,
    "incluir_categorias": true,
    "incluir_json": true,
    "subir": true,
    "extraer_campos": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "CLAVE_FISCAL_FICTICIA"
  }
}
```

## Resultado y archivos

El objeto `data` incluye `operacion` y `representado_cuit`; `vistas` contiene, por cada captura incluida, `vista` (`detalle`/`categorias`), `archivo`, `ancho` y `alto`, más `sha256` y `size_bytes` cuando se publica. `campos` puede incluir `contribuyente`, `categoria_siper`, `motivo`, `fecha_ultima_categoria_siper` y `estado_tramite_disconformidad`; solo se devuelven valores no vacíos.

Si la captura falla, el trabajo falla. Si una extracción estructurada falla después de guardar/subir capturas, estas pueden conservarse y el resultado será `PARCIAL` con error. Aunque `incluir_detalle=false`, la extracción de `campos` predeterminada sigue consultando el detalle: especifique `extraer_campos=false` para evitarla. Consulta de solo lectura.
