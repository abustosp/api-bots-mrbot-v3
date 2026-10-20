# Carga del Portal IVA

## Propósito, operación y efectos

Importa comprobantes de ventas/compras y/o datos de apertura en el libro IVA del CUIT y período indicados. **Puede borrar todos los registros existentes del libro seleccionado antes de importar los TXT**. También puede cargar aperturas y, bajo una condición específica, cambiar el crédito fiscal computable. No es una consulta inocua. Verifique CUIT, período, archivos y respaldos antes de solicitarlo. Las cargas se procesan por secciones: puede haber cambios en algunas secciones aunque otras fallen.

- **Operación del catálogo:** `cargar`
- **Ruta V3:** `POST /api/v3/bots/carga_portal_iva/cargar`
- La creación es asíncrona y no garantiza que el resultado final sea exitoso. Consulte `GET /api/v3/jobs/{job_id}`.
- Los archivos se cargan previamente con `POST /api/v3/uploads`; use la `object_key` devuelta en el campo `_key` correspondiente.

## Payload

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `representado_cuit` | texto | Sí | ninguno | Alias: `cuit`. Se eliminan espacios, guiones, puntos y barras; deben quedar exactamente 11 dígitos. Omitir, `null`, vacío o `false`: rechazo. | `"20123456786"` |
| `periodo` | texto | Sí | ninguno | Formato `AAAAMM`; mes 01–12; no se admite período futuro al validar. Omitir, `null`, vacío o `false`: rechazo. | `"202401"` |
| `denominacion` | texto | No | `""` | Máximo 128 caracteres. Omitir: queda vacío. `null` o `false`: rechazo. Cadena vacía permitida; no altera la carga. | `"EMPRESA DE EJEMPLO"` |
| `operaciones_ng_o_e` | booleano | No | `false` | Omitir: `false`. `null` o vacío: rechazo. `false`: desactivado. | `false` |
| `prorrateo_global` | booleano | No | `false` | Omitir: `false`. `null` o vacío: rechazo. `true` puede hacer que, tras cargar con éxito el CSV de crédito fiscal, se actualice el importe global computable. | `false` |
| `prorrateo_asignacion_directa` | booleano | No | `false` | Omitir: `false`. `null` o vacío: rechazo. `true`: declara esa modalidad al preparar el período. | `false` |
| `prorrateo_ambos` | booleano | No | `false` | Omitir: `false`. `null` o vacío: rechazo. `true`: declara ambas modalidades al preparar el período. | `false` |
| `importacion_definitiva_bienes` | booleano | No | `false` | Omitir: `false`. `null` o vacío: rechazo. `true`: marca esa opción del período. | `false` |
| `importacion_servicios` | booleano | No | `false` | Omitir: `false`. `null` o vacío: rechazo. `true`: marca esa opción del período. | `false` |
| `regimen_turiva` | booleano | No | `false` | Omitir: `false`. `null` o vacío: rechazo. `true`: marca esa opción del período. | `false` |
| `bienes_usados` | booleano | No | `false` | Omitir: `false`. `null` o vacío: rechazo. `true`: marca esa opción del período. | `false` |
| `ninguna_anteriores` | booleano | No | `true` | Omitir: `true`. `null` o vacío: rechazo. `false`: indica que no se selecciona esa alternativa; use las opciones aplicables. | `true` |
| `liv_cbte_key` | texto | No | ninguno | Clave del TXT de comprobantes de ventas. Omitir, `null` o `""`: no se envía archivo. `false`: rechazo. Si se usa, requiere también `liv_alicuota_key`. | `"550e8400-e29b-41d4-a716-446655440000/carga_portal_iva/20123456786/liv_cbte.txt"` |
| `liv_alicuota_key` | texto | No | ninguno | Clave del TXT de alícuotas de ventas. Omitir, `null` o `""`: no se envía archivo. `false`: rechazo. Si se usa, requiere también `liv_cbte_key`. | `"550e8400-e29b-41d4-a716-446655440000/carga_portal_iva/20123456786/liv_alicuota.txt"` |
| `lic_cbte_key` | texto | No | ninguno | Clave del TXT de comprobantes de compras. Omitir, `null` o `""`: no se envía archivo. `false`: rechazo. Si se usa, requiere también `lic_alicuota_key`. | `"550e8400-e29b-41d4-a716-446655440000/carga_portal_iva/20123456786/lic_cbte.txt"` |
| `lic_alicuota_key` | texto | No | ninguno | Clave del TXT de alícuotas de compras. Omitir, `null` o `""`: no se envía archivo. `false`: rechazo. Si se usa, requiere también `lic_cbte_key`. | `"550e8400-e29b-41d4-a716-446655440000/carga_portal_iva/20123456786/lic_alicuota.txt"` |
| `csv_cf_key` | texto | No | ninguno | Clave del CSV de apertura de crédito fiscal. Omitir, `null` o `""`: no se envía archivo. `false`: rechazo. Por sí solo cumple la alternativa de CSV de apertura. | `"550e8400-e29b-41d4-a716-446655440000/carga_portal_iva/20123456786/csv_cf.csv"` |
| `csv_cf_restitucion_key` | texto | No | ninguno | Clave del CSV de restitución de crédito fiscal. Omitir, `null` o `""`: no se envía archivo. `false`: rechazo. | `"550e8400-e29b-41d4-a716-446655440000/carga_portal_iva/20123456786/csv_cf_restitucion.csv"` |
| `csv_df_key` | texto | No | ninguno | Clave del CSV de apertura de débito fiscal. Omitir, `null` o `""`: no se envía archivo. `false`: rechazo. | `"550e8400-e29b-41d4-a716-446655440000/carga_portal_iva/20123456786/csv_df.csv"` |
| `csv_df_restitucion_key` | texto | No | ninguno | Clave del CSV de restitución de débito fiscal. Omitir, `null` o `""`: no se envía archivo. `false`: rechazo. | `"550e8400-e29b-41d4-a716-446655440000/carga_portal_iva/20123456786/csv_df_restitucion.csv"` |

Debe incluir al menos un par completo de TXT (ventas o compras) o un CSV de apertura. Un par TXT incompleto se rechaza. No se aceptan archivos inline en Base64. Cada referencia debe apuntar a un archivo no vacío, con extensión TXT/CSV según el campo y tamaño máximo de 20 MiB.

## Credenciales

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `credentials.cuit_representante` | texto | Sí | ninguno | CUIT de la cuenta fiscal que inicia sesión. Si falta, el acceso no puede completarse. `null` o `""` se tratan como ausentes. `false`: tipo inválido. | `"20123456786"` |
| `credentials.clave` | texto | Sí | ninguno | Clave fiscal. Requerida para completar el acceso. `null` o `""` se tratan como ausentes. `false`: tipo inválido. | `"CLAVE_FICTICIA_NO_USAR"` |

Se aceptan nombres compatibles para las credenciales de CUIT y clave, además de `clave_encriptada` cuando el valor cifrado sea válido para el servicio. No envíe credenciales ficticias a un entorno real.

## Ejemplo completo ficticio

Las claves de archivos son marcadores de ejemplo: deben reemplazarse por claves emitidas al subir archivos autorizados. El CUIT pasa el control de dígito verificador, pero no identifica aquí a una entidad real. La clave no es válida.

```json
{
  "payload": {
    "representado_cuit": "20123456786",
    "periodo": "202401",
    "denominacion": "EMPRESA DE EJEMPLO",
    "operaciones_ng_o_e": false,
    "prorrateo_global": false,
    "prorrateo_asignacion_directa": false,
    "prorrateo_ambos": false,
    "importacion_definitiva_bienes": false,
    "importacion_servicios": false,
    "regimen_turiva": false,
    "bienes_usados": false,
    "ninguna_anteriores": true,
    "liv_cbte_key": "550e8400-e29b-41d4-a716-446655440000/carga_portal_iva/20123456786/liv_cbte.txt",
    "liv_alicuota_key": "550e8400-e29b-41d4-a716-446655440000/carga_portal_iva/20123456786/liv_alicuota.txt"
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "CLAVE_FICTICIA_NO_USAR"
  }
}
```

## Resultado, archivos y resultados parciales

`data` tiene este envelope estable:

| Ruta | Tipo | Cuándo aparece |
|---|---|---|
| `data.operacion` | Texto (`"cargar"`) | Siempre que haya resultado. |
| `data.representado_cuit` | Texto, 11 dígitos | Siempre. |
| `data.periodo` | Texto `AAAAMM` | Siempre. |
| `data.secciones` | Objeto mapa `texto → texto` | Siempre. Las claves aparecen sólo para secciones solicitadas. |
| `data.secciones.ventas_txt` | Texto de estado | Sólo si se envió el par TXT de ventas. Éxito indica `ventas: importado (intento N)`; fallo empieza con `error:`. |
| `data.secciones.compras_txt` | Texto de estado | Sólo si se envió el par TXT de compras. Éxito indica `compras: importado (intento N)`; fallo empieza con `error:`. |
| `data.secciones.CF`, `CF Restitucion`, `DF`, `DF Restitucion` | Texto de estado | Sólo para el CSV de apertura correspondiente. Éxito indica `<sección>: cargado`; fallo empieza con `error:`. |
| `data.secciones.credito_fiscal_compras` | Texto de estado | Sólo después de CSV CF cargado y con `prorrateo_global=true`; resultado `ok`, `sin_modal` o error. |
| `data.periodos_seleccionables` | Lista de textos | Sólo si la pantalla devuelve períodos seleccionables. |
| `data.total_credito_fiscal_computable_cf` | Texto | Sólo si se envió `csv_cf_key`; total interpretado del CSV. |

La envoltura del job indica resultado `OK`, `PARCIAL` o `ERROR`. En parcialidad se conservan estados por sección y las operaciones que sí alcanzaron el portal no se revierten. Este bot no emite archivo de salida en `files`.
