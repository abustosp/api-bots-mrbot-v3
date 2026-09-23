# Certificados locales para mTLS central<->worker (solo desarrollo)

Este directorio contiene la PKI **local** de desarrollo. Ningún material
generado aquí se versiona (ver [`.gitignore`](.gitignore)): cada entorno
ejecuta el generador o usa su secret manager. Producción usa CA separadas
gestionadas por secret manager, nunca estos ficheros.

## Generar

```bash
./infra/certs/gen-certs.sh
# o en otro directorio (p. ej. /tmp para pruebas):
./infra/certs/gen-certs.sh /tmp/mrbot-certs
```

El script crea una CA autofirmada y cuatro identidades firmadas por ella,
todas con `SAN` para `central-api`, `bot-worker`, `localhost` y
`127.0.0.1`:

| Fichero | Rol | CN esperado por el par |
|---|---|---|
| `ca.pem` | CA local (solo verifica, se monta en ambos) | sin CN, solo firma |
| `central-server.pem/.key` | servidor TLS de central-api | `central-api` |
| `worker-server.pem/.key` | servidor TLS de bot-worker | `bot-worker` |
| `central-client.pem/.key` | cliente de la central ante workers | `MrBotCentral` |
| `worker-client.pem/.key` | cliente del worker ante la central | `worker-local-01` |

Las claves quedan con modo `600`. El script imprime las huellas SHA-256
(útiles para pinning en documentación; no son secretos).

## Montaje en compose

El compose base monta `../certs:/certs:ro` en central y worker y expone
las rutas por entorno (`TLS_CA_FILE`, `TLS_CERT_FILE`, `TLS_KEY_FILE`,
`TLS_CLIENT_CERT_FILE`, `TLS_CLIENT_KEY_FILE`, `TLS_EXPECTED_PEER_CN`).
TLS queda **optativo**: los servicios arrancan en HTTP salvo que se aplique
la superposición [`../compose/docker-compose.mtls.yml`](../compose/docker-compose.mtls.yml),
que activa los flags `--ssl-*` de uvicorn con verificación mutua.

## Rotación

Regenerar y recrear contenedores basta en desarrollo:

```bash
./infra/certs/gen-certs.sh && docker compose up -d --force-recreate
```

Cadencia de referencia (plan 06 §6.3): identidades de worker por despliegue
o máximo 30 días; en producción la rotación la hace el secret manager con
drenaje del worker viejo antes de revocar.

## Verificación del handshake

Con los módulos `central_api.security.tls` y `bot_worker.security.tls`
(ambos stdlib-only), cualquier servidor que use `build_server_context` con
`require_client=True` rechaza el handshake sin certificado de cliente o con
CA desconocida, y lo acepta con el certificado correcto. Eso se comprueba
sin Docker con un servidor `ssl` real y clientes con/sin certificado.
