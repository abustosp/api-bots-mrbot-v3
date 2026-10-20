# Generación de VEP desde CCMA

**Propósito.** Consultar obligaciones de capital e intereses de CCMA, seleccionar filas según filtros y generar un VEP. No ejecuta el pago.

## Operación y ruta V3

Catálogo: `vep_ccma`, operación `generar`.

- Canónica: `POST /api/v3/bots/vep_ccma/generar`.
- Alias V3: `POST /api/v3/vep-ccma/generar`.

Enviar `{ "payload": { ... }, "credentials": { ... } }`; consultar estado/resultado en `/api/v3/jobs/{job_id}`. El alias de compatibilidad recibe campos en cuerpo plano; no mezclar con el sobre canónico. Convenciones compartidas de credenciales: [API pública](../api.md).

## Credenciales

Requiere clave fiscal y CUIT representante para iniciar sesión; véase [API pública](../api.md) para las formas de envío, alias y alternativa cifrada. La clave debe ser texto no vacío; omitir/null/vacío impide ejecutar. El CUIT de sesión se envía como `credentials.cuit_representante` (`cuit_inicio_sesion` alias). El CUIT representado de payload es obligatorio y no toma valor por omisión.

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `clave` | texto | Sí | ninguno | Texto no vacío requerido para ejecutar. Alias equivalentes: `clave_representante`, `contrasena`; `clave_encriptada` es la alternativa cifrada. Ausente, `null` o vacío no autentica; `false` no es texto válido. | `"CLAVE_FISCAL_FICTICIA"` |
| `cuit_representante` | texto | Sí | ninguno | CUIT de sesión requerido por el acceso. Alias: `cuit_inicio_sesion`. Omitir, `null` o vacío no aporta CUIT. | `"20123456786"` |

## Campos del payload

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `representado_cuit` | texto | Sí | ninguno | CUIT de 11 dígitos; elimina espacios, guiones, puntos y `/`. Alias: `cuit`, `cuit_representado`. Vacío o `null` inválidos. Algunas formas antiguas pueden ser consumidas por la normalización central antes de la validación, por lo que no se garantiza que una discrepancia entre alias y nombre canónico siempre se rechace. Envíe solo el nombre canónico. | `"20-12345678-6"` |
| `medio_pago` | texto | No | `"internet_banking"` | Valores admitidos: `link`, `pago_mis_cuentas`, `internet_banking`, `xn_group`, `qr`, normalizados a minúsculas. Vacío, `null` u otro valor rechazan. | `"link"` |
| `filtro_impuestos` | lista de objetos | No | `null` | Filtra filas de capital. Claves: `periodo`, `impuesto`, `concepto`, `subconcepto`, `categoria`; claves desconocidas y valores `null` se descartan; objetos vacíos se descartan. Un escalar exige igualdad exacta; una lista significa cualquiera de sus valores; las claves de cada objeto se combinan con AND y objetos distintos con OR. La cadena vacía se conserva como comodín. Omitir, `null` o `[]` termina sin filtro, lo que significa seleccionar todas las filas de capital habilitadas. | `[{"periodo": "01/2026", "categoria": "A"}]` |
| `filtro_intereses` | lista de objetos | No | `null` | Igual que `filtro_impuestos` para intereses, con claves `periodo`, `impuesto`, `concepto`, `subconcepto` o alias `subcpto`; sin `categoria`. `null`, omitir o `[]` significa sin filtro. | `[{"subcpto": "51"}]` |
| `seleccionar_impuestos` | booleano | No | `true` | Habilita filas de capital; `false` excluye capital. Omitir o `null` usa `true`; el `null` se elimina y vuelve al default. | `true` |
| `seleccionar_intereses` | booleano | No | `true` | Habilita intereses; `false` los excluye. Omitir o `null` usa `true`. | `true` |
| `generar_volante` | booleano | No | `true` | `false` solo consulta y selecciona obligaciones, sin crear VEP, PDF ni QR. Omitir o `null` usa `true`. | `true` |
| `incluir_json` | booleano | No | `true` | `false` omite los arreglos `impuestos` e `intereses`; no omite conteos ni `volante` si se generó. Omitir usa `true`; `null` inválido. | `true` |
| `subir_pdf` | booleano | No | `true` | Publica el PDF y, si existe, el PNG del QR. `false` no los publica, pero la generación y la descarga siguen. Omitir o `null` usa `true`. Los valores se convierten a booleano, por lo que una cadena no vacía `"false"` puede interpretarse como verdadera. Use el nombre canónico. | `true` |

Debe activarse `seleccionar_impuestos` o `seleccionar_intereses` y dejar al menos una salida (`incluir_json=true` o `subir_pdf=true`). No se aceptan otros campos salvo credenciales/proxy históricos, que se descartan. Si se selecciona una categoría pero los filtros excluyen todas las filas, la generación falla por selección vacía. Use nombres canónicos para evitar ambigüedades.

## Ejemplo ficticio válido

```json
{
  "payload": {
    "representado_cuit": "20-12345678-6",
    "medio_pago": "link",
    "filtro_impuestos": [{"periodo": "01/2026", "categoria": "A"}],
    "filtro_intereses": [{"subcpto": "51"}],
    "seleccionar_impuestos": true,
    "seleccionar_intereses": true,
    "generar_volante": true,
    "incluir_json": true,
    "subir_pdf": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "CLAVE_FISCAL_FICTICIA"
  }
}
```

## Efecto y resultado

**La generación del volante crea un VEP y tiene efecto fiscal; el pago no se efectúa.** El resultado con JSON incluye `operacion`, CUIT, `medio_pago`, `total_impuestos`, `total_intereses`, `total_seleccionado`, filas `volante` (cuando se genera) y, con `incluir_json=true`, las listas `impuestos`/`intereses`. Publicación de PDF añade `sha256` y `size_bytes`; QR disponible añade `nombre_qr` y opcional `sha256_qr`. Los archivos son PDF y opcional PNG.

En la respuesta de `GET /api/v3/jobs/{job_id}`, `result` indica el estado final, `data` contiene los conteos y las salidas disponibles y `files` enumera los archivos publicados. `files` es una lista vacía cuando no hay archivos publicados.

| Ruta | Tipo | Condición de presencia | Descripción | Ejemplo |
|---|---|---|---|---|
| `result` | texto | Al finalizar el trabajo | `OK` si concluye la operación; `ERROR` si falla una fase. No se devuelve un resultado `PARCIAL`. | `"OK"` |
| `data.operacion`, `data.representado_cuit`, `data.medio_pago` | texto | En un resultado de operación disponible | Operación, CUIT consultado y medio elegido. | `{"operacion":"generar","representado_cuit":"20123456786","medio_pago":"link"}` |
| `data.total_impuestos`, `data.total_intereses`, `data.total_seleccionado` | enteros | En un resultado de operación disponible | Cantidades de obligaciones consultadas por categoría y seleccionadas. Si se genera el volante, `total_seleccionado` coincide con el número de filas de `volante`. | `{"total_impuestos":2,"total_intereses":1,"total_seleccionado":2}` |
| `data.volante` | lista de objetos | Solo si `generar_volante=true` | Filas seleccionadas que forman el VEP, con `tipo`, período, impuesto, concepto, subconcepto, categoría e importe. No aparece si solo se consultan obligaciones. | `[{"tipo":"capital","periodo":"01/2026","impuesto":"FICTICIO","concepto":"1","subconcepto":"0","categoria":"A","importe":100.0,"checkbox_index":1}]` |
| `data.impuestos`, `data.intereses` | listas de objetos | Con `incluir_json=true`; se omiten si es `false` | Obligaciones de capital e intereses consultadas para las categorías habilitadas; cada lista puede ser `[]`. | `[]` |
| `data.sha256`, `data.size_bytes` | texto, entero | Si `generar_volante=true`, `subir_pdf=true` y la publicación del PDF termina | Huella y tamaño del PDF del volante publicado. | `{"sha256":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","size_bytes":24576}` |
| `data.nombre_qr` | texto | Si se genera el volante y el portal devuelve un QR | Nombre del PNG QR. | `"20123456786 - VEP CCMA - QR.png"` |
| `data.sha256_qr` | texto | Si hay QR y su publicación termina con `subir_pdf=true` | Huella del PNG QR publicado. | `"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"` |
| `files[]` | lista de archivos | Siempre en la respuesta del trabajo; contiene el PDF y, si está disponible, el QR cuando se publican | Referencias públicas de los archivos. Sin publicaciones, el valor es `[]`. | `[{"name":"20123456786 - VEP CCMA.pdf","url":"","filename":"20123456786 - VEP CCMA.pdf","size":24576,"size_bytes":24576}]` |

Con `incluir_json=false` se omiten `impuestos` e `intereses`, pero se conservan los conteos y `volante` si se generó. Con `generar_volante=false` no se genera PDF ni QR. El click final no se reintenta si su confirmación queda ambigua; un fallo posterior de descarga o publicación no prueba que el VEP no se haya generado.
