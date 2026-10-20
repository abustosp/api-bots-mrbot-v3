# Carga del Portal IVA

## Propósito, operación y efectos

Importa comprobantes de ventas/compras y/o datos de apertura en el libro IVA del CUIT y período indicados. **Puede borrar todos los registros existentes del libro seleccionado antes de importar los TXT**. También puede cargar aperturas y, bajo una condición específica, cambiar el crédito fiscal computable. No es una consulta inocua. Verifique CUIT, período, archivos y respaldos antes de solicitarlo. Las cargas se procesan por secciones: puede haber cambios en algunas secciones aunque otras fallen.

- **Operación del catálogo:** `cargar`
- **Ruta V3:** `POST /api/v3/bots/carga_portal_iva/cargar`
- La creación es asíncrona y no garantiza que el resultado final sea exitoso. Consulte `GET /api/v3/jobs/{job_id}`.
- Los archivos se cargan previamente con `POST /api/v3/uploads`; use la `object_key` devuelta en el campo `_key` correspondiente.

## Payload

| Campo | Tipo, requerido y default | Omitir / `null` / vacío / `false` |
|---|---|---|
| `representado_cuit` | Texto, requerido. Alias: `cuit`. | Omitir, `null`, vacío o `false`: rechazo. Se eliminan espacios, guiones, puntos y barras; deben quedar exactamente 11 dígitos. |
| `periodo` | Texto, requerido, formato `AAAAMM`. | Omitir, `null`, vacío o `false`: rechazo. Mes 01–12; no se admite período futuro al validar. |
| `denominacion` | Texto, opcional, default `""`, máximo 128 caracteres. | Omitir: queda vacío. `null` o `false`: rechazo. Cadena vacía permitida; no altera la carga. |
| `operaciones_ng_o_e` | Booleano, default `false`. | Omitir: `false`. `null` o vacío: rechazo. `false`: desactivado. |
| `prorrateo_global` | Booleano, default `false`. | Omitir: `false`. `null` o vacío: rechazo. `true` puede hacer que, tras cargar con éxito el CSV de crédito fiscal, se actualice el importe global computable. |
| `prorrateo_asignacion_directa` | Booleano, default `false`. | Omitir: `false`. `null` o vacío: rechazo. `true`: declara esa modalidad al preparar el período. |
| `prorrateo_ambos` | Booleano, default `false`. | Omitir: `false`. `null` o vacío: rechazo. `true`: declara ambas modalidades al preparar el período. |
| `importacion_definitiva_bienes` | Booleano, default `false`. | Omitir: `false`. `null` o vacío: rechazo. `true`: marca esa opción del período. |
| `importacion_servicios` | Booleano, default `false`. | Omitir: `false`. `null` o vacío: rechazo. `true`: marca esa opción del período. |
| `regimen_turiva` | Booleano, default `false`. | Omitir: `false`. `null` o vacío: rechazo. `true`: marca esa opción del período. |
| `bienes_usados` | Booleano, default `false`. | Omitir: `false`. `null` o vacío: rechazo. `true`: marca esa opción del período. |
| `ninguna_anteriores` | Booleano, default `true`. | Omitir: `true`. `null` o vacío: rechazo. `false`: indica que no se selecciona esa alternativa; use las opciones aplicables. |
| `liv_cbte_key` | Texto, opcional: clave del TXT de comprobantes de ventas. | Omitir, `null` o `""`: no se envía archivo. `false`: rechazo. Si se usa, requiere también `liv_alicuota_key`. |
| `liv_alicuota_key` | Texto, opcional: clave del TXT de alícuotas de ventas. | Omitir, `null` o `""`: no se envía archivo. `false`: rechazo. Si se usa, requiere también `liv_cbte_key`. |
| `lic_cbte_key` | Texto, opcional: clave del TXT de comprobantes de compras. | Omitir, `null` o `""`: no se envía archivo. `false`: rechazo. Si se usa, requiere también `lic_alicuota_key`. |
| `lic_alicuota_key` | Texto, opcional: clave del TXT de alícuotas de compras. | Omitir, `null` o `""`: no se envía archivo. `false`: rechazo. Si se usa, requiere también `lic_cbte_key`. |
| `csv_cf_key` | Texto, opcional: clave del CSV de apertura de crédito fiscal. | Omitir, `null` o `""`: no se envía archivo. `false`: rechazo. Por sí solo cumple la alternativa de CSV de apertura. |
| `csv_cf_restitucion_key` | Texto, opcional: clave del CSV de restitución de crédito fiscal. | Omitir, `null` o `""`: no se envía archivo. `false`: rechazo. |
| `csv_df_key` | Texto, opcional: clave del CSV de apertura de débito fiscal. | Omitir, `null` o `""`: no se envía archivo. `false`: rechazo. |
| `csv_df_restitucion_key` | Texto, opcional: clave del CSV de restitución de débito fiscal. | Omitir, `null` o `""`: no se envía archivo. `false`: rechazo. |

Debe incluir al menos un par completo de TXT (ventas o compras) o un CSV de apertura. Un par TXT incompleto se rechaza. No se aceptan archivos inline en Base64. Cada referencia debe apuntar a un archivo no vacío, con extensión TXT/CSV según el campo y tamaño máximo de 20 MiB.

## Credenciales

| Campo | Tipo y necesidad efectiva | Omitir / `null` / vacío / `false` |
|---|---|---|
| `credentials.cuit_representante` | Texto; CUIT de la cuenta fiscal que inicia sesión. | Si falta, el acceso no puede completarse. `null` o `""` se tratan como ausentes. `false`: tipo inválido. |
| `credentials.clave` | Texto; clave fiscal. | Requerida para completar el acceso. `null` o `""` se tratan como ausentes. `false`: tipo inválido. |

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