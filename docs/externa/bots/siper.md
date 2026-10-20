# SIPER

**Propósito.** Consultar la conducta fiscal SIPER del CUIT indicado, capturando la vista de detalle y/o la de categorías. También puede devolver campos leídos del detalle.

## Operación y ruta V3

Catálogo: `siper`, operación `consultar`.

- Canónica: `POST /api/v3/bots/siper/consultar`.
- Alias V3: `POST /api/v3/siper/consulta`.

Cuerpo canónico: `{ "payload": { ... }, "credentials": { ... } }`; el alias de compatibilidad recibe los campos en un cuerpo plano. No mezclar ambas formas. Consultar ejecución aceptada en `/api/v3/jobs/{job_id}`. Convenciones compartidas de credenciales: [API pública](../api.md).

## Credenciales

Requiere clave fiscal y CUIT representante; consulte las convenciones comunes en [API pública](../api.md). Enviar `credentials.clave` y `credentials.cuit_representante`; los campos ausentes, nulos o vacíos no dan acceso. `clave_representante`/`contrasena` son aliases de clave que deben coincidir; se admite alternativa `clave_encriptada`. `cuit_inicio_sesion` es alias del CUIT representante. El CUIT consultado se pasa aparte.

| Campo de credencial | Tipo, requerido, default y tratamiento |
|---|---|
| `clave` | Texto no vacío, requerido para ejecutar. Omitir/null/`""` impide autenticación; `false` no es texto válido. Aliases: `clave_representante`, `contrasena`; alternativa cifrada `clave_encriptada`. |
| `cuit_representante` | Texto de CUIT representante para la sesión; sin default de aplicación. Omitir/null/`""` no aporta CUIT. Alias `cuit_inicio_sesion`. |

## Campos del payload

| Campo | Tipo, requerido y default | Efecto y tratamiento de omisión/null/vacío/false |
|---|---|---|
| `representado_cuit` | texto requerido; alias `cuit` | CUIT de 11 dígitos con separadores comunes eliminados. Vacío/null inválidos. En la ruta canónica envíelo explícitamente dentro de `payload`; el CUIT dentro de `credentials` solo autentica y no es fallback automático para este campo. |
| `incluir_detalle` | booleano, default `true` | Captura la vista del detalle. `false` no la captura. Omitir = `true`; `null` inválido. |
| `incluir_categorias` | booleano, default `true` | Captura la vista de categorías. `false` no la captura. Omitir = `true`; `null` inválido. |
| `incluir_json` | booleano, default `true` | `false` quita la lista `vistas`, pero no elimina necesariamente `campos` del detalle. Omitir = `true`; `null` inválido. |
| `subir` | booleano, default `false` | Controla la publicación de las capturas, no qué vistas se consultan. `false` no publica; `null` inválido. En la API V3, si se omite `subir`, la normalización establece `subir=false`, incluso en ruta canónica. Envíelo explícitamente para publicar capturas. |
| `extraer_campos` | booleano, default `true` | Intenta devolver campos estructurados desde el detalle; `false` evita esa extracción. Omitir = `true`; `null` inválido. |

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
