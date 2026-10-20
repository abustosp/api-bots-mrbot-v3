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

## Resultado y parciales

`result` es el estado `OK`, `PARCIAL` o `ERROR`. `data` contiene:

| Clave | Tipo / condición |
|---|---|
| `operacion` | string, `presentar`. |
| `archivos_presentados` | entero: cantidad de archivos procesados con resultado, incluidos los que fallaron. |
| `total_archivos`, `total_exitosos`, `total_errores` | enteros; `total_exitosos` cuenta las presentaciones exitosas. |
| `detalle` | lista de objetos, solo con `incluir_json=true`; cada uno tiene `archivo` y `estado` strings, `constancia` string o null, y `sha256` string/`size_bytes` entero si se publicó. |

Las constancias exitosas se publican en `files[]` solo si `subir_constancia=true`. Si unos archivos se presentan y otros fallan, `result=PARCIAL`; si todos fallan, `result=ERROR`. Un error de transporte tampoco prueba que no haya habido efecto: si la conexión se corta después de confirmar, comprobar el portal antes de reintentar.

El catálogo en `GET /api/v3/bots` publica `costo_creditos` como metadata informativa. La creación actual reserva una unidad de cuota por trabajo; consultar disponibilidad y límites en la API, no interpretar la metadata como cargo efectivo.
