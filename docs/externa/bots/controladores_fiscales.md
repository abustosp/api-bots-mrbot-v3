# Controladores fiscales

Presenta archivos de controladores fiscales ante el servicio de ARCA y puede devolver las constancias producidas. **Presentar genera un efecto oficial externo**. Una ejecución con varios archivos puede presentar algunos y fallar en otros, por lo que un resultado parcial no debe reenviarse completo sin verificar previamente el estado de cada archivo en el portal.

## Ruta V3 y catálogo

`POST /api/v3/bots/controladores_fiscales/presentar` (2 créditos). Cuerpo canónico: `{"payload": {...}, "credentials": {...}}`. Los archivos de entrada se cargan antes mediante `POST /api/v3/uploads`; en `payload.archivos` se envía cada `object_key` devuelto por la carga.

## Campos de `payload`

| Campo | Tipo; requerido/default | Omisión, `null`, vacío o `false`; efecto |
|---|---|---|
| `archivos` | lista de strings, requerida, 1–50 referencias | Cada archivo cargado debe tener nombre simple con extensión `.pem` o `.zip` (sin distinguir mayúsculas). Se rechazan rutas, nombres vacíos, `.`/`..`, nombres con `/` o `\\`, nombres de más de 255 caracteres y duplicados. No admite una cadena en lugar de lista. Nulo/lista vacía inválidos. |
| `subir_constancia` | boolean, default `true`; aliases históricos `minio_upload`, `subir_a_minio` | `false` suprime la publicación de constancias en `files[]`, pero **no evita la presentación**. Nulo en la ruta pública aplica default. |
| `incluir_json` | boolean, default `true` | `false` omite el detalle por archivo en `result`, no los contadores globales ni las presentaciones. Nulo público aplica default. |

Si se incluyen aliases de carga, sus valores deben coincidir. En el adaptador de compatibilidad antiguo algunos booleanos se convertían por verdad/falsedad del valor, así que se recomienda enviar booleanos JSON estrictos (`true`/`false`). Los campos desconocidos se rechazan. El sufijo permitido se comprueba al validar la operación, además de los límites generales de carga.

## Credenciales

Se requiere `credentials.cuit_representante` y `credentials.clave` para la sesión fiscal. La CUIT debe tener 11 dígitos. Se aceptan los nombres heredados `clave_representante`, `contrasena` o la alternativa cifrada `clave_encriptada`. Si falta la sesión o la clave está vacía/nula, la presentación no se ejecuta. Nunca usar credenciales reales en ejemplos.

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

`result` incluye `archivos_presentados`, `total_archivos`, `total_exitosos` y `total_errores`. Con `incluir_json=true`, `detalle[]` informa por archivo estado, nombre de constancia y, cuando corresponde, hash/tamaño. Las constancias exitosas se publican en `files[]` solo si `subir_constancia=true`. Si unos archivos se presentan y otros fallan, el trabajo queda `PARCIAL`; si todos fallan, `ERROR`. Un `ERROR` de transporte tampoco prueba que no haya habido efecto: si la conexión se corta después de la confirmación, comprobar el portal antes de reintentar.
