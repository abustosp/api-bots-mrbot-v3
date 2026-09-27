"""reCAPTCHA v2 de portales del organismo (port de ``app/utils/capmonster.py``).

El runtime resuelve con el proveedor configurado; el token se inyecta en el
formulario y se dispara el callback para que el portal habilite el submit.
"""
from __future__ import annotations

import re
from typing import Any

from bot_worker.bots.errors import CaptchaUnsolvableError

_SITEKEY_EN_ATRIBUTO = (
    "[data-sitekey]",
    ".g-recaptcha[data-sitekey]",
    "div[data-sitekey]",
)

_JS_SITEKEY = """
() => {
    const nodo = document.querySelector('[data-sitekey]');
    if (nodo && nodo.getAttribute('data-sitekey')) return nodo.getAttribute('data-sitekey');
    const marco = Array.from(document.querySelectorAll('iframe'))
        .map(f => f.getAttribute('src') || '')
        .find(src => src.includes('/recaptcha/'));
    if (marco) {
        const m = marco.match(/[?&]k=([^&]+)/);
        if (m) return decodeURIComponent(m[1]);
    }
    return '';
}
"""

_JS_INYECTAR = """
(token) => {
    const areas = document.querySelectorAll('textarea[name="g-recaptcha-response"], #g-recaptcha-response');
    areas.forEach(a => { a.value = token; a.innerHTML = token; a.style.display = 'block'; });
    const formularios = document.querySelectorAll('form');
    formularios.forEach(f => {
        const oculto = f.querySelector('textarea[name="g-recaptcha-response"]');
        if (oculto && !oculto.value) { oculto.value = token; }
    });
    if (typeof window.___grecaptcha_cfg !== 'undefined') {
        try {
            const clientes = window.___grecaptcha_cfg.clients || {};
            Object.keys(clientes).forEach(k => {
                const cliente = clientes[k] || {};
                Object.keys(cliente).forEach(kk => {
                    const posible = cliente[kk];
                    if (posible && typeof posible.callback === 'function') posible.callback(token);
                });
            });
        } catch (e) { /* callback no disponible */ }
    }
    return true;
}
"""


async def detectar_sitekey(page: Any) -> str:
    """Sitekey del reCAPTCHA presente en la página, o cadena vacía."""
    for selector in _SITEKEY_EN_ATRIBUTO:
        try:
            nodo = page.locator(selector)
            if await nodo.count() == 0:
                continue
            valor = await nodo.first.get_attribute("data-sitekey")
            if valor:
                return str(valor).strip()
        except Exception:
            continue
    try:
        valor = await page.evaluate(_JS_SITEKEY)
        if valor:
            return str(valor).strip()
    except Exception:
        pass
    # Último recurso: leer el src del iframe de reCAPTCHA.
    try:
        src = await page.locator("iframe[src*='/recaptcha/']").first.get_attribute("src")
        if src:
            encontrado = re.search(r"[?&]k=([^&]+)", str(src))
            if encontrado:
                return encontrado.group(1)
    except Exception:
        pass
    return ""


async def resolver(captcha: Any, *, sitekey: str, url: str, timeout_s: int = 120) -> str:
    """Pide el token al proveedor configurado."""
    if captcha is None or not getattr(captcha, "enabled", False):
        raise CaptchaUnsolvableError("captcha presente y sin proveedor configurado")
    solucionar = getattr(captcha, "solve_recaptcha", None)
    if solucionar is None:
        raise CaptchaUnsolvableError("el proveedor configurado no resuelve reCAPTCHA")
    return await solucionar(sitekey=sitekey, url=url, timeout_seconds=timeout_s)


async def inyectar_token(page: Any, token: str) -> None:
    """Escribe el token en el textarea oculto y dispara el callback."""
    try:
        await page.evaluate(_JS_INYECTAR, token)
    except Exception as exc:  # noqa: BLE001 - no propagar texto del sitio
        raise CaptchaUnsolvableError("no se pudo inyectar el token del captcha") from exc
