# Updater de host del bot-worker (botón "Actualizar" del panel central).
# Docker CLI + plugin compose sobre la imagen oficial, más Python (stdlib).
# Build desde la raíz del monorepo:
#   docker build -f services/worker-updater/Dockerfile -t mrbot-worker-updater:dev .
FROM docker:28-cli
RUN apk add --no-cache python3
COPY services/worker-updater/updater.py /app/updater.py
WORKDIR /app
ENTRYPOINT ["python3", "-u", "/app/updater.py"]
