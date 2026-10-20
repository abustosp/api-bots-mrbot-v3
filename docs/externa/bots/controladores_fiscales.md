# Controladores fiscales

Presenta archivos de controladores fiscales ante el servicio de ARCA y puede devolver las constancias producidas. **Presentar genera un efecto oficial externo**. Una ejecución con varios archivos puede presentar algunos y fallar en otros, por lo que un resultado parcial no debe reenviarse completo sin verificar previamente el estado de cada archivo en el portal.

## Ruta V3 y catálogo

Ruta canónica: `POST /api/v3/bots/controladores_fiscales/presentar`. Cuerpo: `{"payload": {...}, "credentials": {...}}`. Los archivos de entrada se cargan antes mediante `POST /api/v3/uploads`; en `payload.archivos` se envía cada `object_key` devuelto por la carga. El validador efectivo de la operación determina los formatos y valores admitidos; el schema de catálogo es descriptivo.

## Campos de `payload`

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `archivos` | lista de textos | Sí | ninguno | Lista de 1 a 50 referencias a archivos cargados previamente. Cada archivo cargado debe tener nombre simple con extensión `.pem` o `.zip` (sin distinguir mayúsculas). Se rechazan rutas, nombres vacíos, `.` o `..`, nombres con `/` o `\` y nombres de más de 255 caracteres. Los duplicados se deduplican conservando la primera aparición. No admite una cadena en lugar de lista. `null` o lista vacía inválidos. | `["uploads/ejemplo/archivo.pem", "uploads/ejemplo/paquete.zip"]` |
| `subir_constancia` | booleano | No | `true` | `false` suprime la publicación de constancias en `files[]`, pero **no evita la presentación**. Omitido aplica `true`; el `null` explícito se convierte a `false` en la normalización efectiva. | `true` |
| `incluir_json` | booleano | No | `true` | `false` omite el detalle por archivo en `data`, no los contadores globales ni las presentaciones. Omitido aplica `true`; `null` explícito se rechaza. | `true` |

Los campos desconocidos se rechazan. Los booleanos deben enviarse como `true`/`false`; no use strings. El sufijo permitido se comprueba sobre el nombre del archivo cargado, además de los límites generales de carga.

## Credenciales

El objeto `credentials` puede omitirse en el envelope, pero se necesita sesión fiscal para presentar. La identidad y la contraseña no tienen default; si faltan, normalmente falla la autenticación.

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `cuit_representante` | texto | Condicional | ninguno | Identidad de la sesión fiscal; requerida si no se envía `cuit_inicio_sesion`. Alias: `cuit_inicio_sesion`. Si se omite, es `null` o vacía, no queda identidad y falla la autenticación. `false` y otros tipos no textuales se rechazan. Si se envían ambos, deben coincidir. | `"20123456786"` |
| `cuit_inicio_sesion` | texto | Condicional | ninguno | Alias de `cuit_representante`; requerido si no se envía `cuit_representante` y aporta la misma identidad. Si se omite, es `null` o vacío, no queda identidad y falla la autenticación. `false` y otros tipos no textuales se rechazan. Si se envían ambos, deben coincidir. | `"20123456786"` |
| `clave` | texto | Condicional | ninguno | Contraseña de la sesión fiscal, salvo que se aporte `clave_encriptada`. Alias: `clave_representante`, `contrasena`. Omitir, `null` o vacío equivale a no aportar esa alternativa. `false` y otros tipos no textuales se rechazan. Si se envían varios aliases, deben coincidir. | `"CLAVE-FICTICIA-NO-UTILIZABLE"` |
| `clave_representante` | texto | Condicional | ninguno | Alias de `clave`; aporta la misma contraseña, salvo que se aporte `clave_encriptada`. Omitir, `null` o vacío equivale a no aportar esa alternativa. `false` y otros tipos no textuales se rechazan. Si se envían varios aliases, deben coincidir. | `"CLAVE-FICTICIA-NO-UTILIZABLE"` |
| `contrasena` | texto | Condicional | ninguno | Alias de `clave`; aporta la misma contraseña, salvo que se aporte `clave_encriptada`. Omitir, `null` o vacío equivale a no aportar esa alternativa. `false` y otros tipos no textuales se rechazan. Si se envían varios aliases, deben coincidir. | `"CLAVE-FICTICIA-NO-UTILIZABLE"` |
| `clave_encriptada` | texto | No | ninguno | Alternativa cifrada de la contraseña. Omitido, `null` o vacío se ignora. `false` y otros tipos no textuales se rechazan. Si se informa, debe poder descifrarse; si también hay texto claro, ambos deben coincidir. | `"Q0xBVkVfRklDVElDSUFfTk9fVVNFRw=="` |

Nunca usar credenciales reales en ejemplos.

## Ejemplo ficticio completo

Primero se cargan dos archivos de prueba y se sustituyen estos ejemplos por los `object_key` reales devueltos por esa carga. Las claves ilustrativas conservan la extensión final que valida el servicio:

```json
{
  "payload": {
    "archivos": ["uploads/ejemplo/archivo.pem", "uploads/ejemplo/paquete.zip"],
    "subir_constancia": true,
    "incluir_json": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "CLAVE-FICTICIA-NO-UTILIZABLE"
  }
}
```

## Resultado, parciales y archivos

Las tablas describen los campos de la respuesta pública del trabajo. Los ejemplos son JSON ficticio, ilustrativo y no ejecutado. En un resultado `ERROR`, `data` puede ser `null`.

| Ruta | Tipo | Condición de presencia | Descripción | Ejemplo |
|---|---|---|---|---|
| `result` | texto o `null` | Siempre que el trabajo tenga resultado final. | `OK` si no hubo fallos por archivo, `PARCIAL` si al menos uno se presentó y otro falló, `ERROR` si fallaron todos. | `"PARCIAL"` |
| `data.operacion` | texto | Cuando `data` está disponible. | Nombre de operación. | `"presentar"` |
| `data.archivos_presentados` | entero | Cuando `data` está disponible. | Cantidad de archivos cuyo procesamiento produjo un registro de resultado, incluidos los fallidos. | `2` |
| `data.total_archivos` | entero | Cuando `data` está disponible. | Cantidad de archivos del lote después de eliminar duplicados. | `2` |
| `data.total_exitosos` | entero | Cuando `data` está disponible. | Cantidad de presentaciones exitosas. | `1` |
| `data.total_errores` | entero | Cuando `data` está disponible. | Cantidad de archivos con estado de error. | `1` |
| `data.detalle` | lista de objetos | Solo con `incluir_json=true`; si es `false`, la propiedad se omite. En un resultado con `data` incluye al menos un registro. | Un registro por archivo procesado. Los errores de archivo no detienen el resto del lote. | `[{"archivo":"archivo.pem","estado":"estado-ficticio-del-servicio","constancia":"constancia_archivo.pdf","sha256":"0000000000000000000000000000000000000000000000000000000000000000","size_bytes":500},{"archivo":"paquete.zip","estado":"error\u007cdiagnostico-ficticio","constancia":null}]` |
| `data.detalle[].archivo` | texto | En cada registro de `detalle`. | Nombre del archivo presentado. | `"archivo.pem"` |
| `data.detalle[].estado` | texto | En cada registro de `detalle`. | Estado informado por el servicio para una presentación exitosa; el valor comienza con el prefijo de error del servicio cuando falla ese archivo. El contenido específico puede variar. | `"estado-ficticio-del-servicio"` |
| `data.detalle[].constancia` | texto o null | En cada registro de `detalle`. | Nombre de la constancia exitosa; `null` cuando la presentación de ese archivo falló. | `null` |
| `data.detalle[].sha256` | texto | Solo en presentaciones exitosas con `subir_constancia=true` y publicación completada. Si no se sube la constancia, se omite. | Huella de la constancia publicada. | `"0000000000000000000000000000000000000000000000000000000000000000"` |
| `data.detalle[].size_bytes` | entero | Solo en presentaciones exitosas con `subir_constancia=true` y publicación completada. Si no se sube la constancia, se omite. | Tamaño de la constancia publicada, en bytes. | `500` |
| `files` | lista de archivos | Siempre en la respuesta del trabajo. Incluye constancias exitosas publicadas; puede ser `[]`. | Adjuntos del trabajo. `subir_constancia=false` deja la lista vacía, pero no cambia la presentación. | `[{"name":"constancia-ejemplo.pdf","url":"","size":500,"artifact_id":"0190f0c0-7f5b-7b2e-9f7e-123456789abd","filename":"constancia-ejemplo.pdf","content_type":"application/pdf","size_bytes":500,"sha256":"0000000000000000000000000000000000000000000000000000000000000000","download_url":"/api/v3/jobs/0190f0c0-7f5b-7b2e-9f7e-123456789abc/artifacts/0190f0c0-7f5b-7b2e-9f7e-123456789abd/download","url_expires_in":null}]` |

Los estados por archivo y los contadores permiten distinguir el lote parcial. La respuesta no incluye una propiedad independiente de advertencias.
