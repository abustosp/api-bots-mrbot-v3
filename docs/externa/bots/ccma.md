# CCMA: consulta de cuentas corrientes

## Propósito, operación y efectos

Consulta saldos y estados de obligaciones del CUIT representado. Puede pedir movimientos y un PDF como salidas adicionales. Es una operación de lectura: no paga, compensa ni modifica saldos.

- **Operación del catálogo:** `consultar`
- **Ruta V3:** `POST /api/v3/bots/ccma/consultar`
- La solicitud se procesa en forma asíncrona; consulte `GET /api/v3/jobs/{job_id}` para resultado y archivos.

## Payload

| Campo | Tipo, requerido y default | Omitir / `null` / vacío / `false` |
|---|---|---|
| `representado_cuit` | Texto, requerido. Alias: `cuit`, `cuit_representado`. | Omitir, `null`, vacío o `false`: rechazo. V3 exige 11 dígitos sin separadores. Si se proporcionan varios alias, deben coincidir. |
| `periodo_desde` | Texto, opcional, default `04/2000`, formato `MM/AAAA`. | Omitir o `null`: conserva el default. Vacío, formato incorrecto o mes fuera de 01–12: rechazo. `false`: rechazo. |
| `incluir_movimientos` | Booleano, opcional, default `false`. Alias: `movimientos`. | Omitir: no solicita movimientos. `null` o vacío: rechazo. `false`: no incluir. |
| `incluir_pdf` | Booleano, opcional, default `false`. Alias: `pdf`. | Omitir: no solicita PDF. `null` o vacío: rechazo. `false`: no incluir. `true`: también activa movimientos, aun si `incluir_movimientos` se omitió o es `false`. |
| `subir` | Booleano, opcional, default `true`. Alias: `carga_minio`, `minio_upload`, `archivo_historico_minio`. | Omitir: publica artefactos generados. `null` o vacío: rechazo. `false`: no publica archivos descargables ni agrega sus huellas/tamaños. |

## Credenciales

| Campo | Tipo y necesidad efectiva | Omitir / `null` / vacío / `false` |
|---|---|---|
| `credentials.cuit_representante` | Texto; cuenta fiscal con la que se inicia sesión. | Sin valor no puede realizarse el acceso. `null` o `""` se consideran ausentes. `false`: tipo inválido. |
| `credentials.clave` | Texto; clave fiscal. | Requerida para completar el acceso. `null` o `""` se consideran ausentes. `false`: tipo inválido. |

También se aceptan los nombres compatibles de credenciales como `cuit_inicio_sesion`, `cuit_login`, `clave_representante` o `contrasena`; la forma cifrada requiere un valor válido para el servicio. CUIT y clave de acceso pueden ser distintos del CUIT consultado cuando hay representación.

## Ejemplo completo ficticio

El CUIT es ilustrativo y pasa el control de dígito verificador; no identifica aquí a una entidad real. La clave es ficticia y no permite iniciar sesión.

```json
{
  "payload": {
    "representado_cuit": "20123456786",
    "periodo_desde": "04/2000",
    "incluir_movimientos": true,
    "incluir_pdf": false,
    "subir": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "CLAVE_FICTICIA_NO_USAR"
  }
}
```

## Resultado, archivos y parciales

El resultado usa este envelope estable en `data`; los importes leídos del portal se devuelven como texto:

| Ruta | Tipo | Cuándo aparece |
|---|---|---|
| `data.operacion` | Texto (`"consultar"`) | Siempre. |
| `data.representado_cuit` | Texto | Siempre. |
| `data.periodo_desde` | Texto `MM/AAAA` | Siempre, con default `04/2000` si se omitió. |
| `data.cuit` | Texto | Siempre; CUIT devuelto por el resumen del portal, normalmente igual al representado. |
| `data.periodo` | Texto | Siempre; período que muestra el resumen del portal. |
| `data.deuda_capital`, `data.deuda_accesorios`, `data.total_deuda` | Texto | Siempre si el resumen principal se obtuvo. |
| `data.credito_capital`, `data.credito_accesorios`, `data.total_a_favor` | Texto | Siempre si el resumen principal se obtuvo. |
| `data.movimientos` | Lista de objetos; cada propiedad es texto: `periodo`, `impuesto`, `concepto`, `subconcepto`, `descripcion`, `fecha_movimiento`, `debe`, `haber`. | Sólo cuando `incluir_movimientos=true` o se fuerza por PDF. |
| `data.movimientos_corte` | Texto | Con movimientos solicitados; indica la causa de corte o fin de lectura. |
| `data.movimientos_completos` | Booleano | Sólo se incluye como `false` ante ciertos cortes; si se omite, no se emitió marca explícita `true`. |
| `data.archivo` | Texto, nombre PDF | Si se genera PDF y `subir=false`. |
| `data.sha256` | Texto hexadecimal | Si se genera PDF y `subir=true`. |
| `data.size_bytes` | Entero | Si se genera PDF y `subir=true`. |

El `files` del job contiene el PDF sólo si se publicó. Los movimientos tienen límites de recorrido de 100 páginas y 500 registros. Un corte de lectura se comunica en `data` y el resultado del plugin sigue siendo `OK`, no se convierte por sí solo en `PARCIAL`; los datos faltantes pueden deberse a que el portal o el límite impidieron completar el recorrido.