# Runbook: workers detrás de NAT con relay (frp)

La central empuja los jobs por HTTP a `ip:puerto` de cada worker. Un worker en
casa (NAT) no es alcanzable; en su lugar abre un túnel saliente hacia un `frps`
junto a la central.

## 1. Arquitectura

```text
casa (NAT)                              central (red docker mrbot-v3-central_default)
bot-worker:8080 <- frpc --7000/TLS--->  frps (alias `relay`) <- central-api http://relay:<puerto>
```

- `frps` vive en la misma red docker que `central-api`, con alias `relay`.
  Solo se publica su puerto de control `7000`; los puertos de túnel
  `20001-20100` NO se publican en el host: solo existen dentro de esa red.
- Cada worker usa `WORKER_ADVERTISED_URL=http://relay:<puerto>` y habla con la
  URL pública de la central (`CENTRAL_URL`) para register/heartbeat/resultados.
- `frps` consulta a `central-api` (`POST /internal/v1/relay/frp-hook`, ops
  `Login`, `NewProxy` y `Ping`) para validar el token por worker y que cada
  `frpc` solo tome su propio puerto. Al validar también el `Ping`, un token
  rotado o un worker dado de baja pierde la sesión viva en <= 75 s
  (`transport.heartbeatTimeout` de `relay/frps.toml`; frpc late cada 30 s).
- El estado en el panel NO sale solo del heartbeat (que viaja por internet): la
  central sondea `http://relay:<puerto>/internal/v1/health` cada 15 s
  (`relay_probe.py`). Un fallo => DEGRADADO, tres seguidos => CAIDO; vuelve a
  SANO con el primer éxito.
- frp fijado en `v0.71.1` (`fatedier/frps` y `fatedier/frpc`, ambos juntos: el protocolo de cable v2 es opt-in y por defecto siguen hablando v1, vigente hasta v0.78).

## 2. Lado central

```bash
cd infra/compose
# En .env.central: FRP_AUTH_TOKEN (openssl rand -hex 32), RELAY_PUBLIC_ADDR
# (host/IP público de la central), RELAY_PUBLIC_PORT=7000. RELAY_HOST y
# RELAY_PORT_RANGE conservan sus valores por defecto (relay, 20001-20100).
docker compose -f central.compose.yaml -f relay.compose.yaml \
  --env-file .env.central up -d
docker compose -f central.compose.yaml -f relay.compose.yaml \
  --env-file .env.central logs frps | tail
```

- Abrir en el firewall/NAT de la central solo `7000/tcp`.
- `relay/frps.toml` apunta el hook a `central-api:8000` (puerto interno del
  contenedor); si cambia, editar `httpPlugins.addr`.
- Si la central corre en otro proyecto compose, fijar `networks.default.name`
  en `relay.compose.yaml` con el nombre real de la red.
- Dar de alta el worker relay en el panel de flota: asigna un puerto
  (`relay:<puerto>`) y genera el `frpc.toml` con su token.

## 3. Lado casa

```bash
cd infra/compose
cp worker-relay.env.example .env.worker
# Completar CENTRAL_URL (pública), WORKER_ADVERTISED_URL=http://relay:<puerto>
# y FRP_AUTH_TOKEN (el mismo de la central).
# Pegar en ./frpc.toml el archivo generado en el panel (debe existir antes de
# levantar; si no, docker crea un directorio con ese nombre). frpc no corre como
# root: el archivo debe ser legible (0644); proteger el directorio con 0700.
chmod 700 . && chmod 644 frpc.toml
docker compose -f worker-relay.compose.yaml --env-file .env.worker up -d
```

- El host de casa debe llegar al registry de imágenes (`api2:5443`) o recibir
  las imágenes con `docker save | gzip -1 | ssh <host> 'gunzip | docker load'`.
- Sin updater en v1. Actualizar a mano:
  `docker compose -f worker-relay.compose.yaml --env-file .env.worker pull &&
  docker compose -f worker-relay.compose.yaml --env-file .env.worker up -d`.

### Varios workers relay en el mismo host

Un directorio por worker (con su `frpc.toml`, `.env.worker` y los dos compose) y
un proyecto compose distinto: `docker compose -p mrbot-relay-w2 -f
worker-relay.compose.yaml --env-file .env.worker up -d`. Cada worker usa el
puerto que le asignó el panel (`WORKER_ADVERTISED_URL=http://relay:<puerto>`).
El tope lo fija `RELAY_PORT_RANGE` de la central (el alta número N+1 devuelve
409). Verificado con 5 instancias a la vez en abp-server.

Los contenedores de frps, frpc y bot-worker rotan sus logs (10 MB x 3 archivos).

## 4. Verificación

```bash
# Casa: el túnel quedó abierto.
docker compose -f worker-relay.compose.yaml --env-file .env.worker logs frpc | grep -i "start proxy success"
# Central: el worker responde por el túnel.
docker compose -f central.compose.yaml -f relay.compose.yaml --env-file .env.central \
  exec central-api python -c "import urllib.request; print(urllib.request.urlopen('http://relay:<puerto>/internal/v1/health', timeout=3).status)"
```

En el panel de flota el worker debe figurar registrado y con heartbeat reciente.

## 5. Problemas frecuentes

| Síntoma | Causa probable |
|---|---|
| frpc: `login to server failed` / `token` | `FRP_AUTH_TOKEN` distinto, o `metadatas.relay_token` rechazado por el hook |
| frpc: `port not allowed` | `remotePort` fuera de 20001-20100 o ya tomado por otro worker |
| Worker relay DEGRADADO/CAIDO con heartbeat reciente | El túnel no responde al sondeo de la central: frpc caído o `localIP`/puerto mal configurados |
| frpc conecta pero el worker queda unhealthy | `WORKER_ADVERTISED_URL` no coincide con `relay:<puerto>`, o bot-worker caído (`localIP = "bot-worker"`) |
| frps no arranca | falta `FRP_AUTH_TOKEN` o `central-api` sin resolver (red distinta) |
| Hook devuelve error | central-api sin migrar/sin `RELAY_*`; ver logs de `central-api` y `frps` |
| frpc no resuelve `bot-worker` | frpc y bot-worker deben estar en el mismo proyecto compose |

## 6. Plan de prueba

Central de pruebas en `api2` + `frps`; worker relay en `abp-server`
(`192.168.0.200`, LAN detrás de NAT, ver `dev-local-lan.md`).

1. Alta del worker en el panel: aparece puerto y `frpc.toml`.
2. Levantar el worker relay en `abp-server`: frpc conecta, hay register y
   heartbeat en la central.
3. Job real (p. ej. CCMA con PDF) forzado a ese worker: llega a `COMPLETO` con
   artefacto descargable.
4. Parar frpc (`docker compose ... stop frpc`): el panel pasa a DEGRADADO en
   ~15 s y a CAIDO en ~45 s, y vuelve a SANO ~15 s después de arrancarlo
   (verificado en api2/abp-server). Un job en curso se reasigna o falla de forma
   limpia, sin colgarse (no verificado con job real: el circuit breaker del
   despacho lo cubre por diseño).
5. Arrancar un `frpc` con el token de OTRO worker pidiendo este puerto: el hook
   lo rechaza y no toma el puerto.
6. Comprobar desde fuera de la central que `20001-20100` no responden
   (`nc -zv <ip-publica> 20001` debe fallar).
7. Rotar el token desde el panel con el túnel arriba: la sesión se corta en
   <= 75 s (verificado: 45-60 s) y los reintentos de frpc con el token viejo
   quedan rechazados (`token de relay inválido`). Pegar el `frpc.toml` nuevo y
   reiniciar frpc; vuelve a SANO en segundos. Tras cambiar `relay/frps.toml`
   hay que reiniciar frps (`docker restart`), `up -d` no recarga el archivo.
8. Dar de baja un worker con el túnel activo y volver a darlo de alta (mismo
   puerto): el nodo vuelve a SANO sin reiniciar su bot-worker; solo hay que
   pegar el `frpc.toml` nuevo y reiniciar frpc.
9. Con 4-5 workers relay, enviar jobs (p. ej. 20 de `consulta_cuit`) con el worker
   manual pausado: los reciben todos los relay (reparto observado 7/7/7/6/5).
