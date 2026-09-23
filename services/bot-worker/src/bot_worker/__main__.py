"""Entrypoint del worker: ``python -m bot_worker --central-url ...``."""

from bot_worker.main import run

if __name__ == "__main__":
    run()
