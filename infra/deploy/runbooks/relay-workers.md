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

## 7. Worker relay que sale solo por un wifi 4G

Caso: el host tiene salida por cable (`eno1`) y también conectado al wifi de un
módem 4G (MiFi); el worker debe salir **solo** por el 4G (IP pública distinta de
la del cable). Instancia actual: `ana-hp` (`192.168.0.100`), wifi `4G-MIFI-8ACA`,
alias `wifi-4g-8aca`, nodo `relay:20003` en la central de `conciliabot2`.

### Cómo funciona

- El worker, `frpc` y un auxiliar corren en una red docker dedicada `wifi-net`
  (`172.30.0.0/24`). Una regla de ruteo por origen manda esa subred a la tabla 100,
  cuya ruta por defecto es la del wifi. El resto del host y los demás contenedores
  siguen por el cable.
- Sin `sudo` sin contraseña: el servicio `wifi-route` (`network_mode: host`,
  `NET_ADMIN`, `restart: unless-stopped`) reaplica regla y ruta cada 20 s, así que
  sobrevive a reinicios y a reconexiones del wifi (el gateway se relee en cada vuelta).
- Si el wifi cae, la tabla 100 tiene `blackhole default`: el worker queda sin red,
  no cae al cable. Es intencional.
- No usar macvlan sobre la interfaz wifi: los AP/MiFi rechazan MACs adicionales.
- Solo cambia el origen del tráfico: el protocolo es el mismo (frpc saliente a
  `RELAY_PUBLIC_ADDR:7000` y el worker a `CENTRAL_URL`). La IP pública del 4G suele
  rotar (CGNAT); no importa porque el túnel es saliente.

### Alta de un host nuevo

1. Alta en el panel de la **central real** (o `POST /admin/workers/relay` con
   `{"alias": "..."}` y el `ADMIN_TOKEN`): devuelve puerto, token y `frpc.toml`. Si
   se da 409 es que se agotó `RELAY_PORT_RANGE`. Ojo: `central-testing` (api2) y la
   central real (conciliabot2) son distintas; confirmar a cuál se apunta.
2. En el host, ver nombre de la interfaz y subred libre:
   `nmcli -t -f DEVICE,TYPE,STATE,CONNECTION dev` (la `wifi` conectada al SSID del
   módem). Crear la red (la subred no debe pisar nada de `ip route`):
   `docker network create --subnet 172.30.0.0/24 --opt com.docker.network.bridge.name=br-wifi wifi-net`
3. Copiar a `~/mrbot-relay-worker-wifi/` (modo 700): `worker.compose.yaml`,
   `worker-relay.compose.yaml`, `worker-relay.wifi.compose.yaml`, `frpc.toml` (0644)
   y `.env.worker` (0600) con `CENTRAL_URL` pública, `WORKER_ADVERTISED_URL=http://relay:<puerto>`,
   `FRP_AUTH_TOKEN`, `WORKER_IMAGE`/`APP_VERSION` vigentes y `WORKER_CONCURRENCY=5`.
   Si la interfaz no es `wlo1` o la subred no es `172.30.0.0/24`, agregar
   `WIFI_IFACE` / `WIFI_SUBNET` al `.env.worker`.
4. Imagen: si el host no tiene login del registry, copiarla de otro host de la
   **misma LAN** (`ssh A 'docker save IMG | gzip -1' | ssh B 'gunzip | docker load'`);
   hacerlo desde un host remoto a través de internet es del orden de KB/s. Bajar también
   `fatedier/frpc:v0.71.1`.
5. Levantar:
   `docker compose -p mrbot-relay-wifi -f worker-relay.compose.yaml -f worker-relay.wifi.compose.yaml --env-file .env.worker up -d`

### Verificación

```bash
# El worker sale por el 4G (IP distinta de la del cable: curl https://api.ipify.org en el host).
docker exec mrbot-relay-wifi-bot-worker-1 python -c "import urllib.request;print(urllib.request.urlopen('https://api.ipify.org',timeout=10).read().decode())"
docker logs mrbot-relay-wifi-frpc-1 2>&1 | grep -E "login to server success|start proxy success"
docker inspect mrbot-relay-wifi-bot-worker-1 --format '{{.Args}}' | grep -o 'concurrency [0-9]*'
ip rule | grep lookup\ 100; ip route show table 100   # default via <gw> dev <wifi> + blackhole
```

En el panel el nodo debe figurar `SANO`. Prueba de corte: desconectar el wifi del
host; el nodo pasa a DEGRADADO/CAIDO y no debe aparecer tráfico por el cable.

### Trampas

- Al cambiar la subred/interfaz hay que recrear `wifi-route` y, si cambia la subred,
  la red `wifi-net` (hay que bajar el stack antes).
- Quitar la regla vieja a mano si se cambia de subred: `ip rule del from <subred> lookup 100`.
