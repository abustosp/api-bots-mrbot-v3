# Generación de VEP desde CCMA

**Propósito.** Consultar obligaciones de capital e intereses de CCMA, seleccionar filas según filtros y generar un VEP. No ejecuta el pago.

## Operación y ruta V3

Catálogo: `vep_ccma`, operación `generar`.

- Canónica: `POST /api/v3/bots/vep_ccma/generar`.
- Alias V3: `POST /api/v3/vep-ccma/generar`.

Enviar `{ "payload": { ... }, "credentials": { ... } }`; consultar estado/resultado en `/api/v3/jobs/{job_id}`. El alias de compatibilidad recibe campos en cuerpo plano; no mezclar con el sobre canónico. Convenciones compartidas de credenciales: [API pública](../api.md).

## Credenciales

Requiere clave fiscal y CUIT representante para iniciar sesión; véase [API pública](../api.md) para las formas de envío, alias y alternativa cifrada. La clave debe ser texto no vacío; omitir/null/vacío impide ejecutar. El CUIT de sesión se envía como `credentials.cuit_representante` (`cuit_inicio_sesion` alias). El CUIT representado de payload es obligatorio y no toma valor por omisión.

| Campo de credencial | Tipo, requerido, default y tratamiento |
|---|---|
| `clave` | Texto no vacío requerido para ejecutar; `clave_representante`/`contrasena` son alias equivalentes y `clave_encriptada` alternativa cifrada. Ausente/null/`""` no autentica; `false` no es texto válido. |
| `cuit_representante` | Texto de CUIT de sesión, sin default y requerido por el acceso. Alias `cuit_inicio_sesion`. Omitir/null/vacío no aporta CUIT. |

## Campos del payload

| Campo | Tipo, requerido y default | Efecto y tratamiento de omisión/null/vacío/false |
|---|---|---|
| `representado_cuit` | texto requerido; aliases `cuit`, `cuit_representado` | CUIT de 11 dígitos; elimina espacios, guiones, puntos y `/`. Vacío/null inválidos. Algunas formas antiguas pueden ser consumidas por la normalización central antes de la validación, por lo que no se garantiza que una discrepancia entre alias y nombre canónico siempre se rechace. Envíe solo el nombre canónico. |
| `medio_pago` | texto, default `internet_banking` | Valores: `link`, `pago_mis_cuentas`, `internet_banking`, `xn_group`, `qr`, normalizados a minúsculas. Vacío/null/otro valor rechazan. |
| `filtro_impuestos` | lista nullable de objetos, default `null` | Filtra filas de capital. Claves: `periodo`, `impuesto`, `concepto`, `subconcepto`, `categoria`; claves desconocidas y valores `null` se descartan; objetos vacíos se descartan. Escalar exige igualdad exacta; lista significa cualquiera de sus valores; las claves de cada objeto se combinan con AND y objetos distintos con OR. Cadena `""` se conserva como comodín. Omitir/null/`[]` termina sin filtro, que significa seleccionar todas las filas de capital habilitadas. |
| `filtro_intereses` | lista nullable de objetos, default `null` | Igual que anterior para intereses, con claves `periodo`, `impuesto`, `concepto`, `subconcepto` o alias `subcpto`; sin `categoria`. `null`/omitir/`[]` significa sin filtro. |
| `seleccionar_impuestos` | booleano, default `true` | Habilita filas de capital; `false` excluye capital. Omitir/null usa `true`; `null` se elimina y vuelve al default. |
| `seleccionar_intereses` | booleano, default `true` | Habilita intereses; `false` los excluye. Omitir/null usa `true`. |
| `generar_volante` | booleano, default `true` | `false` solo consulta y selecciona obligaciones, sin crear VEP/PDF/QR. Omitir/null usa `true`. |
| `incluir_json` | booleano, default `true` | `false` omite los arreglos `impuestos` e `intereses`; no omite conteos ni `volante` si se generó. Omitir usa `true`; `null` inválido. |
| `subir_pdf` | booleano, default `true` | Publica el PDF y, si existe, PNG QR. `false` no los publica, pero la generación/descarga sigue. Omitir/null usa `true`. Los valores se convierten a booleano, por lo que una cadena no vacía `"false"` puede interpretarse como verdadera. Use el nombre canónico. |

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

El click final no se reintenta si su confirmación queda ambigua. El error de descarga/carga posterior no prueba que el VEP no se haya generado. `generar_volante=false` permite inspeccionar obligaciones sin ese efecto. `incluir_json=false` no vacía el resultado completo: aún quedan conteos y filas del volante generado.
