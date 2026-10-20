# Generación de VEP desde archivo

**Propósito.** Enviar un archivo TXT de obligaciones al servicio de pagos, generar un VEP para el lote e indicar el medio de pago. No ejecuta el pago.

## Operación y ruta V3

Catálogo: `vep_archivo`, operación `generar`.

- Canónica: `POST /api/v3/bots/vep_archivo/generar`.
- Alias V3: `POST /api/v3/vep/carga` y `POST /api/v3/vep_archivo/carga`; ambos reciben cuerpo plano de compatibilidad, no el sobre canónico.

Cuerpo canónico: `{ "payload": { ... }, "credentials": { ... } }`. La referencia de archivo se obtiene con `POST /api/v3/uploads`; consultar ejecución en `/api/v3/jobs/{job_id}`.

## Credenciales

Se requiere clave fiscal y CUIT representante; ver [API pública](../api.md) para nombres equivalentes y credencial cifrada. `credentials.clave` debe ser texto no vacío; omisión/null/vacío impide ejecutar. El CUIT de sesión debe estar disponible en `credentials.cuit_representante` (`cuit_inicio_sesion` es alias). El campo opcional `payload.representado_cuit` indica cuenta objetivo y, si se omite/null, se usa el CUIT representante. No enviar claves dentro del payload.

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `clave` | texto | Sí | ninguno | Texto no vacío requerido al ejecutar. Alias: `clave_representante`, `contrasena`; alternativa `clave_encriptada`. Omitir, `null` o `""` no autentica; `false` no es texto válido. | `"CLAVE_FISCAL_FICTICIA"` |
| `cuit_representante` | texto | Sí | ninguno | CUIT de la sesión, requerido para iniciar sesión. Alias: `cuit_inicio_sesion`. Omitir, `null` o vacío no aporta identidad. | `"20123456786"` |

## Campos del payload

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `archivo_key` | texto | Sí | ninguno | Referencia `object_key` de un TXT previamente cargado; el catálogo puede mostrar el alias histórico `archivo`. Vacío o `null` rechazan. Debe ser `.txt`, UTF-8, no vacío, máximo 7.000.000 bytes; primera línea de cabecera `01`, seguida de 1–600 líneas de registro `02`. | `"uploads/demo/vep-lote-ficticio.txt"` |
| `representado_cuit` | texto | No | `null` | CUIT de 11 dígitos; admite separadores comunes. Alias: `cuit`, `cuit_inicio_sesion`. Omitir o `null` usa el CUIT representante; vacío inválido. | `"20-12345678-6"` |
| `medio_pago` | texto | No | `"internet_banking"` | Uno de `link`, `pago_mis_cuentas`, `internet_banking`, `xn_group`, `qr`; se normaliza a minúsculas. `null`, vacío y otros valores se rechazan. | `"internet_banking"` |
| `incluir_json` | booleano | No | `true` | `false` reduce `data` a solo `{detalle: ...}`; no cancela el VEP ni la descarga PDF. Omitir o `null` deja el default `true`; vacío o tipo no booleano inválidos. | `true` |
| `subir_pdf` | booleano | No | `true` | Controla la publicación del PDF y, si aplica, del QR. `false` evita la publicación pero no la generación ni la descarga. Omitir equivale a `true`; `null` equivale a omitir. | `true` |
| `eliminar_descargas` | booleano | No | `true` | Se acepta por compatibilidad, pero no cambia el resultado: los archivos temporales se eliminan al terminar en ambos casos. `null` equivale a omitir; `false` no conserva archivos. | `true` |

Se exige al menos una salida: JSON (`incluir_json=true`) o publicación PDF (`subir_pdf=true`). Los secretos/proxy históricos en payload se descartan. En operaciones canónicas use `archivo_key`, no el nombre temporal local; para controlar publicación use el nombre canónico `subir_pdf`.

## Ejemplo ficticio válido

```json
{
  "payload": {
    "archivo_key": "uploads/demo/vep-lote-ficticio.txt",
    "representado_cuit": "20-12345678-6",
    "medio_pago": "internet_banking",
    "incluir_json": true,
    "subir_pdf": true,
    "eliminar_descargas": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "CLAVE_FISCAL_FICTICIA"
  }
}
```

El ejemplo supone que la referencia corresponde a un archivo con estructura válida, no a un archivo real.

## Efecto y resultado

**Generar el VEP es un efecto fiscal.** El portal recibe el archivo, crea el volante y selecciona el medio de pago. No se realiza el pago. Un click final ambiguo no se reintenta porque podría duplicar la generación; un error posterior no permite inferir que el portal no haya creado el VEP.

En la respuesta de `GET /api/v3/jobs/{job_id}`, `result` indica el estado final, `data` describe el VEP y `files` enumera los archivos publicados. `files` es una lista vacía cuando no hay archivos publicados.

| Ruta | Tipo | Condición de presencia | Descripción | Ejemplo |
|---|---|---|---|---|
| `result` | texto o `null` | Al finalizar el trabajo | `OK` si se genera el VEP y concluyen las descargas/publicaciones solicitadas; `ERROR` si falla una fase. No se devuelve un resultado `PARCIAL`. | `"OK"` |
| `data.detalle` | texto | En todo resultado de operación disponible, también con `incluir_json=false` | Nombre del PDF de detalle generado. | `"20123456786 - VEP - internet_banking.pdf"` |
| `data.operacion`, `data.representado_cuit`, `data.medio_pago` | texto | Solo con `incluir_json=true` (predeterminado) | Operación, CUIT y medio de pago elegido. | `{"operacion":"generar","representado_cuit":"20123456786","medio_pago":"qr"}` |
| `data.registros` | entero | Solo con `incluir_json=true` | Cantidad de registros del archivo de entrada. | `2` |
| `data.lote` | texto | Solo con `incluir_json=true` | Identificador de lote informado por el portal. | `"LOTE-DEMO-001"` |
| `data.sha256`, `data.size_bytes` | texto, entero | Solo con `incluir_json=true`, `subir_pdf=true` y publicación del PDF exitosa | Huella y tamaño del PDF publicado. | `{"sha256":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","size_bytes":24576}` |
| `data.nombre_qr` | texto | Solo con `incluir_json=true`, `medio_pago="qr"` y QR disponible | Nombre del PNG QR descargado. | `"20123456786 - VEP - qr - QR.png"` |
| `data.sha256_qr` | texto | Solo con `incluir_json=true`, `subir_pdf=true` y publicación exitosa del QR disponible | Huella del PNG QR publicado. | `"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"` |
| `files[]` | lista de archivos | Siempre en la respuesta del trabajo; contiene el PDF y, si está disponible, el QR cuando se publican | Referencias públicas de los archivos. Sin publicaciones, el valor es `[]`. | `[{"name":"20123456786 - VEP - internet_banking.pdf","url":"","filename":"20123456786 - VEP - internet_banking.pdf","size":24576,"size_bytes":24576}]` |

Con `incluir_json=false`, `data` contiene `detalle` y el sobre común `schema_version`; no incluye los otros campos ni las huellas aunque los archivos sí se publiquen y aparezcan en `files`. El QR solo se descarga para el medio `qr` y puede no estar disponible. Un fallo posterior a la generación puede producir `ERROR` aunque el VEP ya exista.
