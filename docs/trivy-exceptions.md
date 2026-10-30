# Excepciones documentadas de Trivy

Hallazgos del workflow `trivy-scan` que se aceptan conscientemente, con su
justificación. Revisar en cada subida de bases o cambios en los Dockerfiles
involucrados.

## EX-001: `worker-updater` corre como root (DS-0002)

- Dónde: `services/worker-updater/Dockerfile` (sin instrucción `USER`).
- Por qué se acepta: el updater administra el Docker del host (monta
  `/var/run/docker.sock`, lee credenciales de `/root/.docker/config.json` y
  hace `chown` del env file del compose). El acceso al socket equivale a root
  del host de todos modos: un usuario no-root sería teatro, no seguridad.
- Mitigaciones reales: `cap_drop: [ALL]` + `no-new-privileges:true` en
  `infra/compose/worker.compose.yaml`, allowlist de repositorios
  (`UPDATER_ALLOWED_REPOS`) y `HEALTHCHECK` de proceso en la imagen.
- Reevaluar si: el updater deja de necesitar el socket o las credenciales
  de `/root/.docker`.

## EX-002: `ENV TLS_*` en central-api marcados como secretos (DS-0031)

- Dónde: `services/central-api/Dockerfile` (bloque `ENV TLS_*_FILE`).
- Por qué es falso positivo: los valores son **rutas** (`/certs/*.pem`),
  no material secreto. Las claves y certificados se montan en `/certs` en
  tiempo de ejecución (ver `infra/certs` y compose) y nunca se hornean en
  la imagen.
- Reevaluar si: se agrega un `ENV` con un valor realmente secreto.
  En ese caso, moverlo a Docker secret (`*_FILE`) como el resto.
