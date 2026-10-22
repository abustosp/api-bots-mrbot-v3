# Certificación Primaria de Granos

## Propósito y operación

Descarga los certificados electrónicos de depósito de granos que el representado tiene **como depositante** en el servicio "Liquidación Primaria de Granos" de ARCA (Certificación Electrónica de Granos > Consulta de Certificados de Depósito). Devuelve una planilla XLSX con la grilla consultada y un PDF por certificado. No crea ni modifica certificados.

Ruta V3: `POST /api/v3/bots/certificacion_primaria_granos/consultar`. Ruta compatible: `POST /api/v3/certificacion_primaria_granos/consulta`. Use el sobre `{"payload": {...}, "credentials": {...}}`; la solicitud queda aceptada (`202`) y se consulta con `GET /api/v3/jobs/{job_id}`.

Para otros tipos de consulta (depositario, emisor, corredor) o para liquidaciones, use [`liquidacion_granos`](liquidacion_granos.md).

## Payload

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `representado_cuit` | texto | Sí | ninguno | CUIT del representado (11 dígitos; se quitan espacios, guiones, puntos y `/`). Alias: `cuit`, `cuit_representado`. | `"20123456786"` |
| `representado_nombre` | texto | Sí | ninguno | Nombre con el que se elige al representado en el portal (1 a 128 caracteres). Alias: `denominacion`. El orden de las palabras y los acentos no importan. | `"Gimenez Andres Nicolas"` |
| `fecha_desde` | texto | Sí | ninguno | Inicio del rango, `dd/mm/aaaa`. Alias: `desde`. | `"01/08/2025"` |
| `fecha_hasta` | texto | Sí | ninguno | Fin inclusive, `dd/mm/aaaa`, igual o posterior a `fecha_desde`. Alias: `hasta`. | `"31/10/2025"` |
| `subir_archivos` | booleano | No | `true` | Adjunta la planilla y los PDF al resultado (`files[]`). Alias: `minio_upload`. | `true` |

Los campos desconocidos se rechazan. Las credenciales (`cuit_representante`, `clave`, `clave_encriptada`, mismos alias que `liquidacion_granos`) van en `credentials`, no en `payload`.

## Ejemplo ficticio

```json
{
  "payload": {
    "representado_cuit": "20123456786",
    "representado_nombre": "Apellido Nombre",
    "fecha_desde": "01/08/2025",
    "fecha_hasta": "31/10/2025"
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "Clave-Ficticia-No-Usar-2026"
  }
}
```

## Resultado

`result` es `OK` al completar la consulta, haya o no certificados. Cualquier falla de acceso o consulta termina el trabajo con error.

| Ruta | Descripción |
|---|---|
| `data.tipo_consulta` | Siempre `Certificados como Depositante`. |
| `data.cantidad_certificados` | Certificados encontrados y descargados. |
| `data.planilla` | Nombre de la planilla XLSX. |
| `data.certificados` | Nombres de los PDF (`CD - D - deposito - <cuit> - SIN_CUIT_VENDEDOR - <coe>.pdf`). |
| `files[]` | Planilla y PDF publicados; vacía con `subir_archivos: false`. |
