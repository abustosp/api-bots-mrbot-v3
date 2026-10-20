# Libro de Sueldos Digital

Descarga los PDF del Libro de Sueldos Digital correspondientes a las liquidaciones encontradas en un rango mensual. Es una consulta/descarga de información existente, no presenta liquidaciones ni modifica el portal.

## Ruta V3 y catálogo

`POST /api/v3/bots/libro_sueldos_digital/descargar` (2 créditos). El alias de compatibilidad `/libro-sueldos-digital/consulta` se dirige a esa operación. En el contrato V3 los períodos son `AAAAMM`, mes válido `01`–`12`; rango inclusivo y no invertido.

## Campos de `payload`

| Campo | Tipo; requerido/default | Omisión, `null`, vacío o `false`; efecto |
|---|---|---|
| `periodo_desde`, `periodo_hasta` | strings requeridos `AAAAMM` | Mes de seis dígitos, mes 01–12. Nulo/vacío inválido; fecha inicial posterior a final se rechaza. El rango se genera mes a mes, con tope interno de 1200 meses. |
| `representado_cuit` | string opcional, default nulo; aliases `cuit`, `cuit_representado` en ejecución | Si se omite, se usa el CUIT de sesión. Si se informa, se verifica contra el empleador seleccionado. Vacío/null se interpreta como no informado. En la ruta V3 se envía como 11 dígitos sin separadores. |
| `incluir_json` | boolean, default `true` | `false` omite el detalle por liquidación; deja contadores y listas de períodos. |
| `subir_archivos` | boolean, default `true`; alias `carga_minio` | `false` no publica los PDF en `files[]`; el portal todavía se consulta y los archivos se descargan durante el trabajo. |
| `cuit_representante`, `cuit_inicio_sesion`, `cuit_login` | CUIT de sesión/contexto heredado, normalmente en `credentials` | No se necesita repetir en `payload` si la sesión va en `credentials`. Vacío/nulo se omite. |
| `proxy_request`, `headless` | boolean nullable opcional, legado | No cambian la selección de portal ni el resultado del cliente. |

El esquema de ejecución tolera campos adicionales por compatibilidad; se recomienda enviar solo campos documentados. La operación la fija la ruta V3.

## Credenciales

Se requieren `credentials.cuit_representante` (11 dígitos) y `credentials.clave`. Se admiten los nombres heredados `clave_representante` o `clave_encriptada`. Sin sesión válida no se consulta. El portal verifica el CUIT del empleador seleccionado y corta si no coincide con el solicitado. No usar credenciales reales en el ejemplo.

## Ejemplo ficticio

Ruta: `POST /api/v3/bots/libro_sueldos_digital/descargar`.

```json
{
  "payload": {
    "representado_cuit": "20123456786",
    "periodo_desde": "202601",
    "periodo_hasta": "202603",
    "incluir_json": true,
    "subir_archivos": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "CLAVE-FICTICIA-NO-UTILIZABLE"
  }
}
```

## Resultado y parciales

`result` informa CUIT empleador, rango, `periodos_solicitados`, `periodos_encontrados`, cantidad de liquidaciones encontradas/descargadas y períodos con descargas. Con `incluir_json=true`, `detalle[]` incluye período, liquidación, nombre PDF, y metadatos del archivo publicado; los errores individuales quedan por liquidación. PDF exitosos se publican en `files[]` cuando `subir_archivos=true`.

Sin liquidaciones encontradas, la operación falla. Si todas las descargas encontradas fallan, el estado es `ERROR`; con éxitos y fallos es `PARCIAL`, conservando los archivos exitosos y errores de los demás; si todas terminan bien es `OK`. No reintentar un parcial sin revisar qué liquidaciones ya se obtuvieron.
