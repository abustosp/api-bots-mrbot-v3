"""Sesion propia de la extranet ATM Misiones, independiente de ARCA."""

from __future__ import annotations

import asyncio
import json
import re
import zipfile
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import quote, quote_plus

from bot_worker.bots.errors import (
    CaptchaUnsolvableError,
    CredentialsRejectedError,
    TargetUnavailableError,
)

RETPER_LOGIN_URL = "https://extranet.atmisiones.gob.ar/Extranet/index.php"
RETPER_CONSULTA_URL = (
    "https://extranet.atmisiones.gob.ar/Extranet/Aplicaciones/consultas_ret_perc.php"
)
RETPER_REPORT_POST_URL = (
    "https://extranet.atmisiones.gob.ar/Framework/Funciones/llamar_report.php"
)
RETPER_REPORT_DOWNLOAD_URL = (
    "https://extranet.atmisiones.gob.ar/Framework/Funciones/reporte.php"
)

# Mensajes de rechazo de la extranet ATM. Nunca se copian al diagnóstico:
# solo eligen un código fijo de diagnóstico.
_MISIONES_USUARIO_CLAVE_INCORRECTOS = (
    "el nombre de usuario o la contraseña introducidos no son correctos",
    "el usuario o la contraseña no son correctos",
    "usuario o contraseña incorrectos",
    "clave o usuario incorrectos",
)
_MISIONES_USUARIO_INCORRECTO = (
    "el nombre de usuario introducido no es correcto",
)


def _codigo_rechazo_login(texto: str) -> str | None:
    """Traduce el mensaje de rechazo de ATM a un código fijo de diagnóstico.

    Distingue usuario inexistente de usuario/clave incorrectos. Devuelve
    ``None`` si el texto no contiene un mensaje conocido de rechazo.
    """
    normalizado = (texto or "").lower()
    if any(m in normalizado for m in _MISIONES_USUARIO_CLAVE_INCORRECTOS):
        return "misiones_login_usuario_o_clave_incorrectos"
    if any(m in normalizado for m in _MISIONES_USUARIO_INCORRECTO):
        return "misiones_login_usuario_incorrecto"
    return None


async def _click_first(candidates: Sequence[Any], timeout_ms: int = 5000) -> bool:
    for locator in candidates:
        try:
            if await locator.count() > 0:
                await locator.first.click(timeout=timeout_ms)
                return True
        except Exception:
            continue
    return False


async def _fill_first(
    candidates: Sequence[Any], value: str, timeout_ms: int = 5000
) -> bool:
    for locator in candidates:
        try:
            if await locator.count() > 0:
                await locator.first.fill(value, timeout=timeout_ms)
                return True
        except Exception:
            continue
    return False


def _periodo_valido(periodo: str) -> bool:
    match = re.fullmatch(r"(\d{4})/(\d{2})", (periodo or "").strip())
    return bool(match and 1 <= int(match.group(2)) <= 12)


def _looks_like_pdf(data: bytes) -> bool:
    return len(data) >= 100 and data.startswith(b"%PDF")


class MisionesSession:
    """Autentica en DGR Misiones y genera los reportes de retenciones."""

    def __init__(
        self,
        credentials: Any,
        *,
        page: Any,
        context: Any,
        captcha_solver: Any = None,
        denominacion: str = "",
        debug_dir: Path | None = None,
    ) -> None:
        self._credentials = credentials
        self._page = page
        self._context = context
        self._captcha_solver = captcha_solver
        self._denominacion = str(denominacion or "").strip()
        self._debug_dir = Path(debug_dir) if debug_dir else None
        self._logged_in = False
        self._desde = ""
        self._hasta = ""

    async def login(self, url: str = RETPER_LOGIN_URL) -> None:
        """Abre el ingreso de la extranet ATM, no el login de ARCA."""
        try:
            await self._page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        except Exception as exc:
            raise TargetUnavailableError(
                "No se pudo abrir la extranet de Misiones",
                diagnostic_code="misiones_login_unavailable",
            ) from exc

    async def ingresar(self) -> None:
        """Inicia sesion ATM con el CUIT y la clave del sobre fiscal."""
        usuario = re.sub(
            r"\D", "", str(getattr(self._credentials, "cuit_representante", "") or "")
        )
        clave = str(getattr(self._credentials, "clave", "") or "")
        if len(usuario) != 11 or not clave.strip():
            raise CredentialsRejectedError("Misiones requiere CUIT y clave fiscal")

        ingreso_abierto = await _click_first(
            [
                self._page.get_by_role(
                    "button", name=re.compile(r"INGRESE\s+CON\s+CLAVE\s+FISCAL", re.I)
                ),
                self._page.locator("button:has-text('INGRESE CON CLAVE FISCAL')"),
            ],
            timeout_ms=10_000,
        )
        if not ingreso_abierto:
            raise TargetUnavailableError(
                "No se encontro el acceso de usuario de Misiones",
                diagnostic_code="misiones_login_form_unavailable",
            )

        usuario_ok = await _fill_first(
            [
                self._page.get_by_role("textbox", name=re.compile(r"Usuario", re.I)),
                self._page.locator("input[name='usuario']"),
                self._page.locator("input[placeholder*='Usuario' i]"),
            ],
            usuario,
            timeout_ms=10_000,
        )
        clave_ok = await _fill_first(
            [
                self._page.locator("input[type='password']"),
                self._page.locator("input[name='contrasena']"),
                self._page.locator("input[placeholder*='Contrase' i]"),
            ],
            clave,
            timeout_ms=10_000,
        )
        if not usuario_ok or not clave_ok:
            raise TargetUnavailableError(
                "No se pudieron completar las credenciales en Misiones",
                diagnostic_code="misiones_login_form_unavailable",
            )

        # La extranet de ATM pide el CAPTCHA *después* del primer envío: el
        # widget se inicializa al enviar el formulario. Resolverlo antes no
        # registra el token en el JS del portal y el primer POST vuelve con el
        # diálogo de error genérico ("usuario incorrecto"), aunque el usuario
        # exista. El flujo del logger histórico es enviar, resolver y reenviar.
        if not await self._enviar_login():
            await self._raise_si_login_rechazado()
            raise TargetUnavailableError(
                "No se pudo enviar el ingreso a Misiones",
                diagnostic_code="misiones_login_submit_unavailable",
            )
        await self._wait_network_idle()

        if await self._resolver_captcha_si_aparece():
            await self._enviar_login()
            await self._wait_network_idle()

        await self._raise_si_login_rechazado()

        if not await self._clickear_en_ambitos(
            lambda ambito: (
                ambito.get_by_role("link", name=re.compile(r"Ingresos\s+Brutos", re.I)),
                ambito.locator("a:has-text('Ingresos Brutos')"),
            ),
            total_ms=16_000,
        ):
            await self._raise_si_login_rechazado()
            await self._volcar_pantalla("misiones_sin_menu_ingresos_brutos")
            raise TargetUnavailableError(
                "No se pudo abrir Ingresos Brutos en Misiones",
                diagnostic_code="misiones_ingresos_brutos_unavailable",
            )
        await self._wait_network_idle()

        if not await self._clickear_en_ambitos(
            lambda ambito: (
                ambito.get_by_role(
                    "link", name=re.compile(r"Consulta\s+de\s+Ret\.?/Perc\.?", re.I)
                ),
                ambito.locator("a:has-text('Consulta de Ret./Perc.')"),
                ambito.locator("a:has-text('Ret./Perc.')"),
            ),
            total_ms=16_000,
        ):
            await self._volcar_pantalla("misiones_sin_menu_consulta_retper")
            raise TargetUnavailableError(
                "No se pudo abrir Consulta de Ret./Perc. en Misiones",
                diagnostic_code="misiones_consulta_unavailable",
            )
        await self._wait_network_idle()
        self._logged_in = True

    async def consultar_retper(
        self,
        *,
        desde_site: str,
        hasta_site: str,
        destino_xlsx: Path,
    ) -> None:
        """Aplica el rango y descarga el Excel de retenciones/percepciones."""
        if not self._logged_in:
            raise TargetUnavailableError(
                "La sesion Misiones no esta autenticada",
                diagnostic_code="misiones_session_not_authenticated",
            )
        desde_site = str(desde_site or "").strip()
        hasta_site = str(hasta_site or "").strip()
        if not _periodo_valido(desde_site) or not _periodo_valido(hasta_site):
            raise ValueError("Periodo Misiones invalido, debe tener formato AAAA/MM")
        if desde_site > hasta_site:
            raise ValueError("El periodo desde no puede superar al periodo hasta")

        # El formulario de consulta vive dentro de un marco de la extranet:
        # buscarlo solo en la página principal deja el paso sin efecto.
        desde_ok = await self._fill_en_ambitos("#periodo_desde", desde_site)
        hasta_ok = await self._fill_en_ambitos("#periodo_hasta", hasta_site)
        if not desde_ok or not hasta_ok:
            raise TargetUnavailableError(
                "No se pudieron cargar los periodos de consulta en Misiones",
                diagnostic_code="misiones_period_fields_unavailable",
            )
        self._desde = desde_site
        self._hasta = hasta_site

        destino_xlsx = Path(destino_xlsx)
        destino_xlsx.parent.mkdir(parents=True, exist_ok=True)
        descargado = False
        for ambito in self._ambitos():
            botones = [
                ambito.get_by_role(
                    "button", name=re.compile(r"Generar\s+Excel", re.I)
                ),
                ambito.locator("button:has-text('Generar Excel')"),
                ambito.locator("input[value*='Excel' i]"),
            ]
            for locator in botones:
                try:
                    if await locator.count() == 0:
                        continue
                    async with self._page.expect_download(
                        timeout=45_000
                    ) as descarga_info:
                        await locator.first.click(timeout=8000)
                    descarga = await descarga_info.value
                    await descarga.save_as(str(destino_xlsx))
                    descargado = True
                    break
                except Exception:
                    continue
            if descargado:
                break
        if not descargado:
            raise TargetUnavailableError(
                "No se pudo descargar el Excel de Misiones",
                diagnostic_code="misiones_xlsx_download_missing",
            )

        if not destino_xlsx.is_file() or destino_xlsx.stat().st_size == 0:
            raise TargetUnavailableError(
                "Misiones devolvio un Excel vacio",
                diagnostic_code="misiones_xlsx_empty",
            )
        if not zipfile.is_zipfile(destino_xlsx):
            raise TargetUnavailableError(
                "Misiones devolvio un Excel invalido",
                diagnostic_code="misiones_xlsx_invalid",
            )

    async def generar_pdf(self, *, destino_pdf: Path) -> None:
        """Genera y descarga el PDF de la consulta actual usando la sesion ATM."""
        if not self._logged_in or not self._desde or not self._hasta:
            raise TargetUnavailableError(
                "No hay una consulta Misiones lista para exportar a PDF",
                diagnostic_code="misiones_pdf_without_query",
            )

        usuario = re.sub(
            r"\D", "", str(getattr(self._credentials, "cuit_representante", "") or "")
        )
        if len(usuario) != 11:
            raise CredentialsRejectedError("Falta el CUIT fiscal de la sesion Misiones")
        # El reporte Oracle espera el período compacto ``AAAAMM``: con
        # ``AAAA/MM`` su trigger ``afterpform`` falla con ORA-01722.
        desde_compacto = self._desde.replace("/", "")
        hasta_compacto = self._hasta.replace("/", "")

        cookies = await self._context.cookies("https://extranet.atmisiones.gob.ar")
        cookie_header = "; ".join(
            f"{item['name']}={item['value']}" for item in cookies if item.get("name")
        )
        try:
            user_agent = await self._page.evaluate("() => navigator.userAgent")
        except Exception:
            user_agent = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        id_session_header = await self._extract_id_session()

        parametros_raw = (
            f"P_CUIT_CONTRIBUYENTE={usuario}&"
            "P_CUIT_AGENTE=null&"
            "P_DATOS_CONTRIB=N&"
            "P_C_REGIMEN=null&"
            f"P_PERIODO_DESDE={desde_compacto}&"
            f"P_PERIODO_HASTA={hasta_compacto}&"
            "P_SUBTRIBUTO=null&"
            "P_ID_CONTRIBUYENTE=null&"
            "P_TITULO_REPORTE=MIS RETENCIONES/PERCEPCIONES&"
            "P_C_TIPO_INFORME=TODO&"
            f"P_D_DENOMINACION={quote(self._denominacion, safe='')}&"
            f"P_USER={usuario}&"
            "P_TIPO_INGRESO=EXTRANET"
        )
        post_data = (
            "c_tipo_report=consultas_ret_perc&"
            f"parametros={quote_plus(parametros_raw, safe='')}&"
            "server_name=extranet.atmisiones.gob.ar&"
            "c_impresion=PDF"
        )
        headers = {
            "User-Agent": user_agent,
            "Accept": "*/*",
            "Accept-Language": "es-ES,en-US;q=0.7,en;q=0.3",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": RETPER_CONSULTA_URL,
            "Origin": "https://extranet.atmisiones.gob.ar",
            "X-Requested-With": "XMLHttpRequest",
            "X-Id-Menu": "99999",
            "Pragma": "no-cache",
            "Cache-Control": "no-cache",
        }
        if cookie_header:
            headers["Cookie"] = cookie_header
        if id_session_header:
            headers["IdSession"] = id_session_header

        await self._resolver_captcha_si_aparece()
        respuesta = await self._context.request.post(
            RETPER_REPORT_POST_URL, data=post_data, headers=headers, timeout=60_000
        )
        texto_respuesta = await respuesta.text()
        if respuesta.status == 200 and self._response_looks_like_captcha(texto_respuesta):
            if not await self._resolver_captcha_si_aparece():
                raise CaptchaUnsolvableError("Misiones solicito un CAPTCHA para generar el PDF")
            respuesta = await self._context.request.post(
                RETPER_REPORT_POST_URL, data=post_data, headers=headers, timeout=60_000
            )
            texto_respuesta = await respuesta.text()
        if respuesta.status != 200:
            raise TargetUnavailableError(
                "Misiones no pudo generar el reporte PDF",
                diagnostic_code="misiones_pdf_generation_failed",
            )

        report_id = self._report_id_session(texto_respuesta)
        if not report_id:
            raise TargetUnavailableError(
                "Misiones no devolvio identificador de reporte PDF",
                diagnostic_code="misiones_pdf_id_missing",
            )
        await asyncio.sleep(5)

        report_url = (
            f"{RETPER_REPORT_DOWNLOAD_URL}?id_sesion={report_id}"
            "&c_impresion=PDF&format=PDF"
        )
        headers_pdf = {
            "User-Agent": user_agent,
            "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
            "Referer": RETPER_CONSULTA_URL,
            "Origin": "https://extranet.atmisiones.gob.ar",
        }
        if cookie_header:
            headers_pdf["Cookie"] = cookie_header
        if id_session_header:
            headers_pdf["IdSession"] = id_session_header
        # El servlet de Oracle Reports responde la primera vez con su propia
        # página de estado mientras arma el reporte: hay que reintentar hasta
        # recibir el PDF real.
        pdf = b""
        for intento in range(6):
            await self._resolver_captcha_si_aparece()
            descarga = await self._context.request.get(
                report_url, headers=headers_pdf, timeout=60_000
            )
            pdf = await descarga.body()
            if descarga.status == 200 and self._response_looks_like_captcha(
                pdf.decode("utf-8", errors="ignore")
            ):
                continue
            if descarga.status == 200 and _looks_like_pdf(pdf):
                break
            await asyncio.sleep(5 if intento < 5 else 0)
        if not _looks_like_pdf(pdf):
            raise TargetUnavailableError(
                "Misiones devolvio un PDF invalido",
                diagnostic_code="misiones_pdf_invalid",
            )

        destino_pdf = Path(destino_pdf)
        destino_pdf.parent.mkdir(parents=True, exist_ok=True)
        destino_pdf.write_bytes(pdf)

    async def _enviar_login(self) -> bool:
        try:
            boton = self._page.locator("#btn_ingresar")
            if await boton.count() > 0:
                # El POST lo sincroniza _wait_network_idle; esperar además el
                # settle de la navegación hacía fallar el envío con un timeout
                # de 10s aunque el portal sí hubiera respondido.
                await boton.click(timeout=10_000, force=True, no_wait_after=True)
                return True
        except Exception:
            pass
        if await _click_first(
            [
                self._page.get_by_role("button", name=re.compile(r"^Ingresar$", re.I)),
                self._page.locator("button:has-text('Ingresar')"),
                self._page.locator("input[value='Ingresar']"),
            ],
            timeout_ms=10_000,
        ):
            return True
        try:
            return bool(
                await self._page.evaluate(
                    """() => {
                        const boton = document.querySelector('#btn_ingresar');
                        if (boton) { boton.click(); return true; }
                        const submit = document.querySelector("input[name='log_submit']");
                        if (submit) { submit.click(); return true; }
                        return false;
                    }"""
                )
            )
        except Exception:
            return False

    async def _wait_network_idle(self) -> None:
        try:
            await self._page.wait_for_load_state("networkidle", timeout=30_000)
        except Exception:
            # El sitio mantiene peticiones de fondo en algunas pantallas.
            return

    async def _codigo_error_de_login(self) -> str | None:
        """Código fijo de rechazo si el portal mostró un mensaje de login.

        Nunca devuelve el texto del portal: solo el identificador del tipo de
        rechazo (usuario inexistente vs usuario/clave incorrectos).
        """
        try:
            texto = (await self._page.locator("body").inner_text()).strip()
        except Exception:
            texto = ""
        codigo = _codigo_rechazo_login(texto)
        if codigo:
            return codigo
        try:
            # Solo cajas de error explícitas: ``[class*='error']`` daría
            # falsos positivos con nodos ajenos al login (p. ej. el widget
            # de reCAPTCHA usa ``grecaptcha-error``).
            errores = self._page.locator(".alert-danger, .error")
            for indice in range(await errores.count()):
                item = errores.nth(indice)
                if await item.is_visible() and (await item.text_content() or "").strip():
                    return "misiones_login_rejected"
        except Exception:
            return None
        return None

    async def _raise_si_login_rechazado(self) -> None:
        """Convierte un rechazo mostrado por ATM en error de credenciales.

        ATM responde con un diálogo de error y deja la página en el login. Sin
        este chequeo, el fallo siguiente (envío del formulario o menú
        Ingresos Brutos) se reportaría como caída del organismo en lugar de
        credencial rechazada.
        """
        codigo = await self._codigo_error_de_login()
        if codigo:
            raise CredentialsRejectedError(
                "Misiones rechazo el CUIT o la clave fiscal",
                diagnostic_code=codigo,
            )

    async def _hay_error_de_login(self) -> bool:
        return await self._codigo_error_de_login() is not None

    async def _resolver_captcha_si_aparece(self) -> bool:
        if not await self._hay_captcha():
            return False
        if await self._captcha_valido():
            return True
        if self._captcha_solver is None or not getattr(
            self._captcha_solver, "enabled", False
        ):
            raise CaptchaUnsolvableError(
                "Misiones presento un CAPTCHA sin un resolvedor habilitado",
                diagnostic_code="misiones_captcha_sin_resolvedor",
            )
        try:
            sitekey = await self._page.evaluate(
                """() => {
                    const nodo = document.querySelector('[data-sitekey]');
                    if (nodo && nodo.getAttribute('data-sitekey')) {
                        return nodo.getAttribute('data-sitekey');
                    }
                    const frame = document.querySelector("iframe[src*='recaptcha']");
                    if (!frame) return '';
                    try { return new URL(frame.src).searchParams.get('k') || ''; }
                    catch (_) { return ''; }
                }"""
            )
            if not sitekey:
                raise CaptchaUnsolvableError(
                    "No se encontro el sitekey de reCAPTCHA en Misiones",
                    diagnostic_code="misiones_captcha_sin_sitekey",
                )
            try:
                token = await self._captcha_solver.solve_recaptcha(
                    sitekey=str(sitekey), url=self._page.url
                )
            except Exception as exc:
                # El proveedor puede caerse o quedarse sin saldo: se conserva
                # solo el tipo de excepcion, nunca su texto ni la clave.
                raise CaptchaUnsolvableError(
                    f"el proveedor de CAPTCHA no resolvio el desafio ({type(exc).__name__})",
                    diagnostic_code="misiones_captcha_proveedor",
                ) from exc
            inyectado = await self._page.evaluate(
                """token => {
                    let campo = document.querySelector(
                        "textarea#g-recaptcha-response, textarea[name='g-recaptcha-response']"
                    );
                    if (!campo) {
                        campo = document.createElement('textarea');
                        campo.name = 'g-recaptcha-response';
                        campo.id = 'g-recaptcha-response';
                        campo.style.display = 'block';
                        document.body.appendChild(campo);
                    }
                    campo.value = token;
                    campo.innerHTML = token;
                    campo.textContent = token;
                    campo.dispatchEvent(new Event('input', { bubbles: true }));
                    campo.dispatchEvent(new Event('change', { bubbles: true }));

                    try {
                        if (typeof window.grecaptcha === 'undefined' || !window.grecaptcha) {
                            window.grecaptcha = { getResponse: () => token, reset: () => {} };
                        } else {
                            window.__copilot_recaptcha_token = token;
                            window.grecaptcha.getResponse = () => window.__copilot_recaptcha_token || '';
                        }
                    } catch (_) {}

                    for (const nodo of Array.from(document.querySelectorAll('[data-callback]'))) {
                        const nombre = nodo.getAttribute('data-callback');
                        if (!nombre) continue;
                        const callback = nombre.split('.').reduce(
                            (actual, parte) => actual && actual[parte], window
                        );
                        if (typeof callback === 'function') {
                            try { callback(token); } catch (_) {}
                        }
                    }
                    return Boolean(campo.value && campo.value.trim());
                }""",
                token,
            )
            if not inyectado:
                raise CaptchaUnsolvableError(
                    "No se pudo insertar el token reCAPTCHA de Misiones",
                    diagnostic_code="misiones_captcha_inyeccion",
                )
            await asyncio.sleep(1.2)
            return True
        except CaptchaUnsolvableError:
            raise
        except Exception as exc:
            raise CaptchaUnsolvableError(
                f"No se pudo resolver el CAPTCHA de Misiones ({type(exc).__name__})",
                diagnostic_code="misiones_captcha_etapa_desconocida",
            ) from exc

    def _ambitos(self) -> list[Any]:
        """Página y marcos donde puede vivir el menú de la extranet.

        La extranet de ATM arma el menú dentro de ``frame``/``iframe``: buscar
        solo en la página principal deja el clic sin efecto aunque el enlace
        exista, que es lo que reportaba ``misiones_ingresos_brutos_unavailable``.
        """
        ambitos: list[Any] = [self._page]
        for marco in list(getattr(self._page, "frames", []) or []):
            if marco is self._page or marco in ambitos:
                continue
            ambitos.append(marco)
        return ambitos

    async def _clickear_en_ambitos(
        self, construir: Any, *, total_ms: int = 12_000
    ) -> bool:
        """Intenta el clic en la página y en cada marco hasta lograrlo."""
        ambitos = self._ambitos()
        if not ambitos:
            return False
        por_ambito = max(2_000, total_ms // len(ambitos))
        for ambito in ambitos:
            try:
                candidatos = construir(ambito)
            except Exception:
                continue
            if await _click_first(candidatos, timeout_ms=por_ambito):
                return True
        return False

    async def _fill_en_ambitos(
        self, selector: str, valor: str, *, total_ms: int = 15_000
    ) -> bool:
        """Completa un campo buscándolo en la página y en cada marco.

        El formulario de consulta puede montarse después del clic de menú, así
        que se reintenta hasta agotar ``total_ms`` en vez de fallar al primer
        intento.
        """
        limite = asyncio.get_running_loop().time() + total_ms / 1000
        while True:
            for ambito in self._ambitos():
                try:
                    campo = ambito.locator(selector)
                    if await campo.count() == 0:
                        continue
                except Exception:
                    continue
                if await _fill_first([campo], valor, timeout_ms=4_000):
                    return True
            if asyncio.get_running_loop().time() >= limite:
                return False
            await asyncio.sleep(0.5)

    async def _sigue_en_login(self) -> bool:
        """True si la pantalla actual todavía es el formulario de ingreso."""
        try:
            if await self._page.locator("input[name='username']").count():
                return True
        except Exception:
            pass
        try:
            return "index.php" in str(getattr(self._page, "url", ""))
        except Exception:
            return False

    async def _volcar_pantalla(self, nombre: str) -> None:
        """Guarda el HTML y la URL de la pantalla actual para diagnóstico.

        Solo se usa en fallos de navegación del menú: el archivo queda en el
        ``work_dir`` del job (efímero), nunca en logs ni en el resultado, y se
        acota el tamaño para no arrastrar respuestas gigantes del portal.
        """
        if self._debug_dir is None:
            return
        try:
            html = await self._page.content()
        except Exception:
            return
        try:
            self._debug_dir.mkdir(parents=True, exist_ok=True)
            destino = self._debug_dir / f"{nombre}.html"
            destino.write_text(html[:400_000], encoding="utf-8", errors="ignore")
            (self._debug_dir / f"{nombre}.url.txt").write_text(
                str(getattr(self._page, "url", "")), encoding="utf-8"
            )
        except Exception:
            return

    async def _hay_captcha(self) -> bool:
        for selector in (
            "iframe[src*='recaptcha']",
            "iframe[title*='reCAPTCHA']",
            "div.g-recaptcha",
            "[id*='captcha' i], [class*='captcha' i]",
        ):
            try:
                if await self._page.locator(selector).count() > 0:
                    return True
            except Exception:
                continue
        return False

    async def _captcha_valido(self) -> bool:
        try:
            return bool(
                await self._page.evaluate(
                    """() => {
                        try {
                            const campo = document.querySelector(
                                "textarea#g-recaptcha-response, textarea[name='g-recaptcha-response']"
                            );
                            if (campo && campo.value && campo.value.trim()) return true;
                            if (typeof grecaptcha !== 'undefined' && grecaptcha
                                && typeof grecaptcha.getResponse === 'function') {
                                if (grecaptcha.getResponse()) return true;
                            }
                            if (typeof captchaisvalid === 'function') return !!captchaisvalid();
                            return false;
                        } catch (_) { return false; }
                    }"""
                )
            )
        except Exception:
            return False

    async def _extract_id_session(self) -> str:
        try:
            valor = await self._page.evaluate(
                """() => {
                    for (const item of [
                        localStorage.getItem('IdSession'),
                        sessionStorage.getItem('IdSession'),
                        localStorage.getItem('id_session'),
                        sessionStorage.getItem('id_session'),
                        window.IdSession, window.id_session, window.ID_SESSION
                    ]) if (item) return String(item);
                    for (const store of [localStorage, sessionStorage]) {
                        for (let i = 0; i < store.length; i += 1) {
                            const key = store.key(i);
                            if (key && key.toLowerCase().includes('idsession')) {
                                const value = store.getItem(key);
                                if (value) return String(value);
                            }
                        }
                    }
                    const node = document.querySelector(
                        'meta[name="IdSession"], meta[name="id_session"], '
                        + 'input[name="IdSession"], input[name="id_session"]'
                    );
                    return node ? String(node.content || node.value || '') : '';
                }"""
            )
            return str(valor or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _response_looks_like_captcha(payload: str) -> bool:
        texto = (payload or "").lower()
        return any(
            marcador in texto
            for marcador in ("recaptcha", "captcha", "g-recaptcha", "no soy un robot")
        )

    @staticmethod
    def _report_id_session(payload: str) -> str:
        try:
            parsed = json.loads(payload)
            if str(parsed.get("resultado", "")).upper() == "OK":
                valor = parsed.get("id_session") or parsed.get("id_sesion") or ""
                if valor:
                    return str(valor)
        except (TypeError, ValueError):
            pass
        match = re.search(r'id_ses+sion"?\s*[:=]\s*"?([a-zA-Z0-9]+)"?', payload or "")
        return match.group(1) if match else ""
