"""Captura de evidencias para la variante logger.

- Crea ./temp/{bot}_{timestamp}/{logs,html,screenshots,har,descargas}.
- En cada paso guarda screenshot + HTML con prefijo numerico incremental.
- Log por paso en archivo dedicado.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Protocol

_SAFE = re.compile(r"[^A-Za-z0-9_-]+")


def _safe(name: str) -> str:
    return _SAFE.sub("_", name).strip("_") or "step"


class _DebugLike(Protocol):
    capturas_dir: Path
    descargas_dir: Path
    har_path: Optional[Path]

    def log(self, msg: str, *args: Any) -> None: ...
    async def capture(self, page: Any, step: str) -> None: ...


class NullDebug:
    """No-op para la variante clean. Mantiene la misma interfaz."""

    capturas_dir = Path("./descargas")
    descargas_dir = Path("./descargas")
    har_path = None

    def log(self, msg: str, *args: Any) -> None:
        return

    async def capture(self, page: Any, step: str) -> None:
        return


class DebugCapture:
    def __init__(self, bot: str, base_dir: str = "temp") -> None:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = Path(base_dir) / f"{bot}_{run_id}"
        self.logs_dir = self.run_dir / "logs"
        self.html_dir = self.run_dir / "html"
        self.screenshots_dir = self.run_dir / "screenshots"
        self.har_dir = self.run_dir / "har"
        self.descargas_dir = self.run_dir / "descargas"
        for d in (self.logs_dir, self.html_dir, self.screenshots_dir,
                  self.har_dir, self.descargas_dir):
            d.mkdir(parents=True, exist_ok=True)

        logger = logging.getLogger(f"{bot}.{run_id}")
        logger.setLevel(logging.INFO)
        if not logger.handlers:
            fh = logging.FileHandler(self.logs_dir / f"{bot}.log", encoding="utf-8")
            fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(fh)
        logger.propagate = False
        self._logger = logger

        self._step = 0
        self.capturas_dir = self.run_dir
        self.har_path = self.har_dir / f"{bot}.har"
        self.log("run_dir=%s", self.run_dir)

    def log(self, msg: str, *args: Any) -> None:
        self._logger.info(msg, *args)

    async def capture(self, page: Any, step: str) -> None:
        if page is None:
            self.log("capture_skipped step=%s reason=page_none", step)
            return
        self._step += 1
        prefix = f"{self._step:03d}_{_safe(step)}"
        try:
            url = getattr(page, "url", "")
        except Exception:
            url = ""
        self.log("step=%s name=%s url=%s", self._step, step, url)

        png = self.screenshots_dir / f"{prefix}.png"
        html = self.html_dir / f"{prefix}.html"
        try:
            await page.screenshot(path=str(png), full_page=True)
        except Exception:
            self.log("screenshot_failed step=%s", step)
        try:
            html.write_text(await page.content(), encoding="utf-8")
        except Exception:
            self.log("html_failed step=%s", step)
