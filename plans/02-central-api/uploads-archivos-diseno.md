# Carga de archivos en V3: bucket temporal `uploads`

Estado al 2026-10-10. Resultado de la prueba teórica contra el bucket real y diseño por sección.

## 1. Mecanismo común (ya implementado)

```
cliente ──POST /api/v3/uploads {bot, operacion, cuit_representado, filename, size_bytes}
        ◄── {object_key, bucket:"uploads", upload_url (PUT SigV4), expires_at}
cliente ──PUT upload_url (bytes)──► bucket uploads
cliente ──POST job {archivo_key | archivos:[object_key]}
central: resolver_archivos (dueño + bot + vigencia) → job guarda solo claves
despacho: payload con nombres simples + sobre sellado `input_files[{campo,nombre,url GET}]`
worker : descarga a work_dir (o a *_b64), expande ZIP, valida y ejecuta
```

- Clave: `{usuario}/{bot}/{cuit}/{archivo original}`. El prefijo de usuario es un agregado al pedido original (`{bot}/{cuit}/{archivo}`) para que dos cuentas no se pisen. Cambiarlo es una línea (`clave_upload`).
- Reutilización: el ticket (`upload_tickets`) queda vigente `UPLOADS_RETENTION_SECONDS` (24 h). Varios jobs pueden referenciar la misma clave.
- Un `PUT` repetido sobre la misma clave reemplaza el objeto. El ticket se actualiza (upsert) solo si es del mismo usuario.

## 2. Prueba teórica (storage real, `tools/teorico_uploads.py`)

Cada caso: ticket, PUT prefirmado, resolver, armar sobre, GET prefirmado como el worker, comparar bytes.

| Caso | Campo | Resultado |
|---|---|---|
| controladores_fiscales PEM | `archivos` | OK |
| controladores_fiscales ZIP (2 PEM) | `archivos` | OK, expande a `F8010.001.pem` y `F8011.001.pem` |
| vep_archivo TXT | `archivo_key` | OK, bytes idénticos |
| carga_portal_iva liv_cbte, liv_alicuota, lic_cbte, lic_alicuota | `*_key` | OK (4) |
| carga_portal_iva csv_cf, csv_cf_restitucion, csv_df, csv_df_restitucion | `*_key` | OK (4) |
| Aislamiento: otro usuario referencia la clave | | Rechazado |

11 de 11 subidas y descargas correctas. Limitaciones de la prueba: usa contenido de ejemplo (no pasa por ARCA), el worker se simuló con las mismas funciones de descarga y no con un job real, y los 11 objetos de prueba quedan bajo el prefijo del usuario de prueba, porque `storage.py` no firma `DELETE`.

## 3. Inventario de secciones con carga de archivos (V1)

| # | Sección V1 | Archivo(s) | V3 hoy | Estado |
|---|---|---|---|---|
| 1 | `POST /controladores-fiscales/carga` | `files[]` PEM o ZIP | `archivos` por object_key | Hecho |
| 2 | `POST /vep/carga` | `archivo_txt` | `archivo_key` o `archivo_b64` | Hecho |
| 3 | `POST /portal_iva/carga` | 8 archivos (TXT y CSV) | `*_key` o `*_b64` | Hecho |
| 4 | `POST /vep/zip` | `archivo_excel` | multipart/b64/raw (`file_inputs.py`) | Funciona, ver 4.1 |
| 5 | `POST /procesar_pem` y `/utilidades/pem/convertir` | `file` .pem | multipart/b64/raw | Funciona, ver 4.1 |
| 6 | `POST /rcel/procesar_pdf` y `/utilidades/rcel/extraer-texto` | `file` .pdf | multipart/b64/raw | Funciona, ver 4.1 |
| 7 | `POST /f2002/extraer`, `/f731/extraer`, `/f931/extraer` | `file` .pdf | multipart/b64/raw | Funciona, ver 4.1 |

Las secciones 4 a 7 no son bots: la central procesa los bytes en la misma petición. No pasan por worker ni job.

## 4. Diseño de modificaciones

### 4.1 Utilidades síncronas (4 a 7): opcional, baja prioridad
Hoy leen el cuerpo completo en memoria en el proceso central (`uploaded_file_from_request`, sin `python-multipart`). Con un `MAX_UTILITY_BYTES` acotado es aceptable.

Si se quiere uniformidad:
- Aceptar además `archivo_key` (object_key de `/uploads`). La central baja el objeto con GET prefirmado, lo procesa y responde.
- Agregar `uploaded_file_from_request(..., key_field="archivo_key")` con `resolver_archivos` y un límite de tamaño.
- No requiere tocar worker. Esfuerzo bajo, valor bajo: solo conviene si hay clientes con archivos mayores al tope.

### 4.2 Bots con archivos (1 a 3): ajustes pendientes
1. **Alias multipart de V1:** `/controladores-fiscales/carga`, `/vep/carga` y `/portal_iva/carga` siguen rechazando multipart con archivos con un mensaje que indica usar `/uploads`. Es intencional, pero los clientes V1 que mandaban multipart deben migrar. Opción de compatibilidad: que la central acepte el multipart, suba ella misma el archivo a `uploads` y continúe con la clave. Evita romper clientes V1 al costo de pasar los bytes por la central (usar solo para archivos pequeños).
2. **Esquema histórico:** `archivo_key` y `*_key` no figuran en el esquema documentado de los alias. Se aceptan porque el cuerpo admite campos extra. Falta documentarlos en OpenAPI y en `public_bot_*_schema`.
3. **Campo `cuit_representado` en `/uploads`:** para `carga_portal_iva` y `controladores_fiscales` el cliente debe pasar el CUIT correcto al pedir el ticket. Hoy no se valida que coincida con el CUIT del job. Propuesta: en `submit_job`, si la clave contiene un CUIT distinto de `representado_cuit`, responder 422.
4. **Límites por tipo:** validar extensión y `content_type` por bot al emitir el ticket (`.pem/.zip`, `.txt`, `.csv`). Hoy se aceptan hasta `UPLOAD_MAX_BYTES` (50 MB) sin chequeo de extensión.
5. **Confirmación de subida:** el ticket se marca `confirmed=true` al emitirlo, aunque el objeto no se haya subido. Un job con clave no subida falla en el worker (`TargetUnavailableError`). Propuesta: en `resolver_archivos`, hacer un `HEAD` prefirmado y rechazar con 422 antes de encolar (requiere firmar `HEAD`).
6. **Retención y limpieza:** configurar expiración en el bucket (lifecycle de 1 día) como red de seguridad. Mientras tanto, un job periódico que borre los objetos con ticket vencido. Requiere firmar `DELETE` en `storage.py`.
7. **Reintentos:** la URL GET dura 15 minutos y se genera en cada despacho, por lo que un reintento o re-asignación obtiene una URL nueva. Si el ticket venció entre reintentos, el job falla. Propuesta: no vencer el ticket mientras haya jobs no terminales que lo referencian.

### 4.3 Otros bots
Ningún otro bot de V1 recibe archivos del cliente. `aportes_en_linea` devuelve `archivo_historico_b64` como salida, no como entrada.

## 5. Orden sugerido
1. Punto 4.2.5 (HEAD antes de encolar) y 4.2.4 (extensión por bot): mayor valor, evitan jobs que fallan tarde.
2. Punto 4.2.3 (coherencia CUIT de la clave y del job).
3. Punto 4.2.6 (limpieza) y 4.2.7 (retención ligada a jobs).
4. Punto 4.2.1 (multipart de compatibilidad) solo si hay clientes V1 que no puedan migrar.
5. Punto 4.1 solo bajo demanda.
