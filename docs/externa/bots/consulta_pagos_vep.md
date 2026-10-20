# Consulta de pagos VEP

Consulta en ARCA/SETI pagos y VEP asociados a un CUIT representado para un período en meses. Es una consulta de solo lectura. Puede publicar el CSV resultante como archivo.

## Ruta V3 y catálogo

`POST /api/v3/bots/consulta_pagos_vep/consultar` (1 crédito). Cuerpo canónico: `{"payload": {...}, "credentials": {...}}`.

## Campos de `payload`

| Campo | Tipo; requerido/default | Omisión, `null`, vacío o `false`; efecto |
|---|---|---|
| `representado_cuit` | string, requerido; aliases `cuit`, `cuit_representado` | Se quitan separadores y se requieren 11 dígitos. Si se mandan varios aliases, deben representar el mismo CUIT. Nulo/vacío inválido. |
| `periodo` | string de 1–3 dígitos; default `"72"` | Cantidad de meses. Debe ser numérico y mayor que cero (`"000"` se rechaza; `"001"` vale). Omitido o nulo usa 72 meses. |
| `incluir_json` | boolean; default `true` | `false` elimina la muestra de filas del resultado, no el resumen. Nulo público aplica el default. |
| `subir_csv` | boolean; default `true`; aliases `minio_upload`, `subir_a_minio` | `false` evita publicar archivo en `files[]`, pero el CSV se sigue obteniendo para procesar filas. Nulo público aplica el default. Alias de carga contradictorios se rechazan. |

Campos adicionales son rechazados. Los booleanos deben enviarse como booleanos, no como cadenas.

## Credenciales

Se requiere `credentials.cuit_representante` (CUIT de inicio de sesión, 11 dígitos) y `credentials.clave` (contraseña fiscal). También se reconoce `clave_representante` o `clave_encriptada` como nombre heredado/alternativa de credencial. La CUIT de sesión puede diferir de `representado_cuit`. Credencial omitida, vacía o nula no permite iniciar la consulta. No documentar ni enviar valores reales en ejemplos.

## Ejemplo ficticio

```json
{
  "payload": {
    "representado_cuit": "20-12345678-6",
    "periodo": "72",
    "incluir_json": true,
    "subir_csv": true
  },
  "credentials": {
    "cuit_representante": "20-12345678-6",
    "clave": "CLAVE-FICTICIA-NO-UTILIZABLE"
  }
}
```

## Resultado

`result` informa `operacion`, CUIT representado, período, cantidad de filas y nombre del CSV. Puede incluir hash, tamaño y `muestra` (primeras filas) cuando `incluir_json=true`. Con `subir_csv=true`, el CSV se referencia en `files[]`; con `false`, no se entrega un enlace al archivo aunque el portal todavía haya generado/descargado el CSV durante el procesamiento. No se documenta una salida parcial: los errores de sesión, portal o exportación hacen fallar la operación.

El período se aplica en SETI, en la consulta de pagos/VEP. No crea ni paga un VEP.
