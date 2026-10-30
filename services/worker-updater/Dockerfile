# Updater de host del bot-worker (botón "Actualizar" del panel central).
# Docker CLI + plugin compose sobre la imagen oficial, más Python (stdlib).
# Build desde la raíz del monorepo:
#   docker build -f services/worker-updater/Dockerfile -t mrbot-worker-updater:dev .
FROM docker:28.5.2-cli
RUN apk add --no-cache python3
COPY services/worker-updater/updater.py /app/updater.py
WORKDIR /app
# Intencionalmente SIN `USER`: el updater administra el Docker del host
# (docker.sock, credenciales en /root/.docker y chown del env file del compose;
# ver docs/trivy-exceptions.md EX-001). El socket equivale a root del host de
# todos modos, así que un usuario no-root sería teatro, no seguridad.
# Mitigaciones reales: cap_drop ALL + no-new-privileges (compose) y allowlist
# de repositorios de imágenes (UPDATER_ALLOWED_REPOS).
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD-SHELL ps | grep -q '[u]pdater.py'
ENTRYPOINT ["python3", "-u", "/app/updater.py"]
