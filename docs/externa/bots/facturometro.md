# Facturómetro

Lee del servicio de Monotributo el monto facturado, el tope y la categoría informada para un contribuyente. Es una consulta, no modifica la situación fiscal.

## Ruta V3 y catálogo

`POST /api/v3/bots/facturometro/consultar` (1 crédito). El alias de compatibilidad `/facturometro/consulta` apunta a esta operación. En el endpoint público V3 `representado_cuit` es requerido y debe tener 11 dígitos sin separadores, aunque el servicio también conoce el CUIT de la sesión como alternativa para su selección interna.

## Campos de `payload`

| Campo | Tipo; requerido/default | Omisión, `null`, vacío o `false`; efecto |
|---|---|---|
| `representado_cuit` | string, requerido en la ruta V3, exactamente 11 dígitos; aliases de compatibilidad `cuit`, `cuit_representado` | Identifica el CUIT consultado. Vacío/nulo no cumplen el requisito público. El servicio puede tomar el CUIT de sesión como alternativa, pero la solicitud pública V3 igualmente exige este campo. |
| `incluir_captura` | boolean, default `true`; aliases `carga_minio`, `subir_captura` | Se acepta en ejecución aunque no figure en el esquema público publicado. `false` evita la captura PNG y su entrada en `files[]`; no cambia los datos consultados. `null` aplica el default en la ruta pública. |
| `cuit_representante` | CUIT opcional de sesión, admitido por el esquema de ejecución; aliases `cuit_inicio_sesion`, `cuit_login` | La sesión real se define en `credentials`; no usar este campo para enviar secretos. Un valor vacío/nulo se omite. |
| `proxy_request`, `headless` | boolean nullable opcional, legado | No controlan el servicio ni aportan un efecto del contrato actual. |

Los campos ajenos al contrato se rechazan al procesar la solicitud. La publicación de la captura puede no producir archivo si el servicio no muestra el panel, aunque los datos se hayan consultado.

## Credenciales

La ejecución requiere sesión fiscal: `credentials.cuit_representante` (11 dígitos) y `credentials.clave`. Se aceptan `clave_representante` o `clave_encriptada` como nombres/alternativa heredados. Si faltan credenciales, la ejecución no inicia. No usar credenciales reales en el ejemplo.

## Ejemplo ficticio

Ruta: `POST /api/v3/bots/facturometro/consultar`.

```json
{
  "payload": {
    "representado_cuit": "20123456786",
    "incluir_captura": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "CLAVE-FICTICIA-NO-UTILIZABLE"
  }
}
```

## Resultado

`result` informa `operacion`, `representado_cuit`, `monto`, `tope` y `categoria`; los tres valores leídos pueden ser strings o nulos si el servicio no los publica. Si se solicita y obtiene captura, `captura` incluye nombre, hash y tamaño, y el PNG aparece en `files[]`. `screenshot_url` puede contener la clave del archivo, no debe tratarse como una URL firmada. Si no hay panel se devuelve una advertencia y no hay artefacto. Un servicio sin lectura disponible produce error, no valores inferidos.
