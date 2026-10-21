# bots_logger

Copia **sin modificar** de las versiones `*_logger.py` de `bots_dev` (y de `shared/`) que el
worker puede ejecutar en modo logger de script (`logger: true` + `logger_script: true`).
No son plugins ni tocan la salida de los bots normales: se ejecutan como subproceso con
`PYTHONPATH` apuntando a esta carpeta, y su carpeta `temp/{bot}_{ts}/` se vuelca al tar.gz.
Mantener sincronizadas con `bots_dev` (ver `bots_dev/AGENTS.md`).
