"""Sesion efimera propia de AGIP (ClaveCiudad), independiente de ARCA."""

from __future__ import annotations

import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from bot_worker.bots.errors import CredentialsRejectedError, TargetUnavailableError

# Mensajes de rechazo de ClaveCiudad. Nunca se copian al diagnóstico: solo
# eligen un código fijo de diagnóstico.
_AGIP_SIN_CUENTA_RE = re.compile(r"no existe|cuenta no registrada|sin cuenta", re.I)

_RESULT_EXCEL_BUTTONS = (
    "#tablaRetenciones_botonExcel",
    "#tablaPercepciones_botonExcel",
    "#tablaRetencionesBancarias_botonExcel",
    "#tablaPercepcionesAduaneras_botonExcel",
    "#tablaRetencionesSIRTAC_botonExcel",
)

_SECTIONS = (
    {
        "tab_text": "Retenciones",
        "files": (
            ("#tablaRetenciones_botonExcel", "RET", "xls"),
            ("#tablaRetenciones_botonTxt", "RET", "txt"),
            ("#tablaRetenciones_botonTxtSifere", "RET SIFERE", "txt"),
        ),
    },
    {
        "tab_text": "Percepciones",
        "files": (
            ("#tablaPercepciones_botonExcel", "PER", "xls"),
            ("#tablaPercepciones_botonTxt", "PER", "txt"),
            ("#tablaPercepciones_botonTxtSifere", "PER SIFERE", "txt"),
        ),
    },
    {
        "tab_text": "Retenciones Bancarias",
        "files": (
            ("#tablaRetencionesBancarias_botonExcel", "RET bancarias", "xls"),
            ("#tablaRetencionesBancarias_botonTxt", "RET bancarias", "txt"),
            ("#tablaRetencionesBancarias_botonTxtSifere", "RET bancarias SIFERE", "txt"),
        ),
    },
    {
        "tab_text": "Percepciones Aduaneras",
        "files": (
            ("#tablaPercepcionesAduaneras_botonExcel", "PER ADUANERAS", "xls"),
            ("#tablaPercepcionesAduaneras_botonTxt", "PER ADUANERAS", "txt"),
            ("#tablaPercepcionesAduaneras_botonTxtSifere", "PER ADUANERAS SIFERE", "txt"),
        ),
    },
    {
        "tab_text": "Retenciones SIRTAC",
        "files": (
            ("#tablaRetencionesSIRTAC_botonExcel", "RET SIRTAC", "xls"),
            ("#tablaRetencionesSIRTAC_botonTxt", "RET SIRTAC", "txt"),
            ("#tablaRetencionesSIRTAC_botonTxtSifere", "RET SIRTAC SIFERE", "txt"),
        ),
    },
)


def _codigo_rechazo_login(estado: dict[str, Any]) -> str:
    """Clasifica el rechazo de ClaveCiudad con un código fijo, sin texto del sitio.

    El portal usa ``#error-email`` cuando el CUIL no tiene cuenta y
    ``#error-password`` cuando la clave no coincide; distinguirlos permite
    saber si hay que dar de alta la cuenta o corregir la clave.
    """
    error_email = str(estado.get("errorEmail") or "")
    error_password = str(estado.get("errorPassword") or "")
    if _AGIP_SIN_CUENTA_RE.search(error_email):
        return "agip_account_not_found"
    if error_password:
        return "agip_password_rejected"
    if error_email:
        return "agip_login_rejected"
    return "agip_representados_missing"


def _periodo_valido(periodo: str) -> bool:
    match = re.fullmatch(r"(\d{2})/(\d{4})", (periodo or "").strip())
    return bool(match and 1 <= int(match.group(1)) <= 12)


def _nombre_archivo(cuit: str, tipo: str, desde: str, hasta: str, extension: str) -> str:
    """Nombre seguro para las descargas temporales que se empaquetan."""
    cuit = re.sub(r"\D", "", cuit or "")
    tipo = re.sub(r"[\\/:*?\"<>|]+", "", tipo).strip()
    desde_compacto = re.sub(r"\D", "", desde)
    hasta_compacto = re.sub(r"\D", "", hasta)
    return f"{cuit[-1:]} - {cuit} - {tipo} - {desde_compacto} - {hasta_compacto}.{extension}"


def _empaquetar_descargas(archivos: list[Path], destino: Path) -> None:
    """Agrupa todas las salidas AGIP V2 en un artefacto transportable."""
    if not archivos:
        raise TargetUnavailableError(
            "AGIP no produjo archivos de retenciones/percepciones para el período",
            diagnostic_code="agip_download_missing",
        )
    destino.parent.mkdir(parents=True, exist_ok=True)
    cantidad = 0
    with zipfile.ZipFile(destino, "w", compression=zipfile.ZIP_DEFLATED) as paquete:
        for archivo in archivos:
            if archivo.is_file() and archivo.stat().st_size:
                paquete.write(archivo, arcname=archivo.name)
                cantidad += 1
    if cantidad == 0 or not destino.is_file() or destino.stat().st_size == 0:
        raise TargetUnavailableError(
            "AGIP devolvió descargas vacías",
            diagnostic_code="agip_download_empty",
        )


class AgipSession:
    """Login de ClaveCiudad y consulta de los reportes Ret/Per AGIP."""

    def __init__(self, credentials: Any, *, page: Any) -> None:
        self._credentials = credentials
        self._page = page
        self._logged_in = False
        self._representado = ""

    async def login(self, url: str) -> None:
        """Abre la portada AGIP; el login de usuario se completa en ingresar."""
        await self._page.goto(url, wait_until="domcontentloaded")

    async def ingresar(self, usuario: str, cuit_representado: str) -> None:
        """Autentica en ClaveCiudad, selecciona CUIT y abre Gestión-AR."""
        clave = str(getattr(self._credentials, "clave", "") or "")
        usuario = str(usuario or "").strip()
        cuit = re.sub(r"\D", "", str(cuit_representado or ""))
        if not usuario or not clave:
            raise CredentialsRejectedError("AGIP requiere usuario y clave")
        if len(cuit) != 11:
            raise TargetUnavailableError(
                "CUIT representado inválido para AGIP",
                diagnostic_code="agip_represented_cuit_invalid",
            )

        await self._open_login_gateway()
        await self._open_email_login()
        await self._page.locator("#email").fill(usuario)
        await self._page.locator("#password-text-field").fill(clave)
        await self._submit_login()
        await self._select_contributor(cuit)
        await self._open_service(cuit)
        self._representado = cuit
        self._logged_in = True

    async def _open_login_gateway(self) -> None:
        try:
            bloqueada = await self._page.evaluate(
                "() => (document.body?.innerText || '').includes('Web Page Blocked!')"
            )
            if bloqueada:
                raise TargetUnavailableError(
                    "AGIP bloqueó el acceso del worker en su página de seguridad",
                    diagnostic_code="agip_waf_blocked",
                )
            await self._page.get_by_role("button", name="Iniciar sesión").click(
                # La navegación la confirma el wait_for_function de abajo: en
                # el portal medido el clic tardaba ~26s sólo por esperar el
                # settle de la navegación y rozaba el timeout por defecto.
                no_wait_after=True,
            )
            await self._page.wait_for_function(
                "() => location.href.includes('login.buenosaires.gob.ar') "
                "&& Boolean(document.querySelector('#zocial-mail, #email'))",
                timeout=60_000,
            )
        except TargetUnavailableError:
            raise
        except Exception as exc:
            try:
                bloqueada = await self._page.evaluate(
                    "() => (document.body?.innerText || '').includes('Web Page Blocked!')"
                )
            except Exception:
                bloqueada = False
            if bloqueada:
                raise TargetUnavailableError(
                    "AGIP bloqueó el acceso del worker en su página de seguridad",
                    diagnostic_code="agip_waf_blocked",
                ) from exc
            raise TargetUnavailableError(
                "No se pudo abrir el acceso de ClaveCiudad",
                diagnostic_code="agip_login_gateway_unavailable",
            ) from exc

    async def _open_email_login(self) -> None:
        try:
            await self._page.wait_for_function(
                "() => Boolean(document.querySelector('#zocial-mail, #email'))",
                timeout=30_000,
            )
            email = self._page.locator("#email")
            if await email.count():
                return
            boton = self._page.locator("#zocial-mail")
            if await boton.count():
                await boton.click()
                await self._page.wait_for_function(
                    "() => Boolean(document.querySelector('#email'))",
                    timeout=30_000,
                )
                return
        except Exception as exc:
            raise TargetUnavailableError(
                "No se pudo abrir el acceso con usuario de ClaveCiudad",
                diagnostic_code="agip_email_login_unavailable",
            ) from exc
        raise TargetUnavailableError(
            "No se encontró el formulario de usuario de ClaveCiudad",
            diagnostic_code="agip_email_login_unavailable",
        )

    async def _submit_login(self) -> None:
        try:
            await self._page.get_by_role("button", name="Iniciar sesión").click(
                no_wait_after=True
            )
            await self._page.wait_for_function(
                """() => document.querySelector('select#cuit_representado') !== null
                || ['#error-email', '#error-password'].some(selector => {
                    const error = document.querySelector(selector);
                    return Boolean(error && error.textContent.trim());
                })""",
                timeout=20_000,
            )
        except Exception as exc:
            raise TargetUnavailableError(
                "ClaveCiudad no completó el inicio de sesión",
                diagnostic_code="agip_login_timeout",
            ) from exc
        estado = await self._page.evaluate(
            """() => ({
                representados: Boolean(document.querySelector('select#cuit_representado')),
                errorEmail: (document.querySelector('#error-email')?.textContent || '').trim(),
                errorPassword: (document.querySelector('#error-password')?.textContent || '').trim()
            })"""
        )
        if (
            estado.get("errorEmail")
            or estado.get("errorPassword")
            or not estado.get("representados")
        ):
            raise CredentialsRejectedError(
                "AGIP rechazó el usuario o la clave",
                diagnostic_code=_codigo_rechazo_login(estado),
            )

    async def _select_contributor(self, cuit: str) -> None:
        selector = self._page.locator("select#cuit_representado").first
        try:
            await selector.wait_for(state="visible", timeout=30_000)
            await selector.select_option(value=cuit)
            await self._page.evaluate(
                """target => {
                    const select = document.querySelector('select#cuit_representado');
                    if (!select) throw new Error('representado ausente');
                    select.value = target;
                    select.dispatchEvent(new Event('input', { bubbles: true }));
                    select.dispatchEvent(new Event('change', { bubbles: true }));
                    select.dispatchEvent(new Event('blur', { bubbles: true }));
                    if (typeof window.aplicaciones_rapido_ajax === 'function') {
                        window.aplicaciones_rapido_ajax();
                    }
                }""",
                cuit,
            )
            servicio = f'#aplicaciones a[onclick*="ir_servicio(43,{cuit})"]'
            await self._page.wait_for_function(
                """({target, selector}) => {
                    const select = document.querySelector('select#cuit_representado');
                    const link = document.querySelector(selector);
                    return Boolean(select && select.value === target && link);
                }""",
                arg={"target": cuit, "selector": servicio},
                timeout=60_000,
            )
        except Exception as exc:
            raise TargetUnavailableError(
                "No se pudo seleccionar el CUIT representado en AGIP",
                diagnostic_code="agip_represented_cuit_not_selectable",
            ) from exc

    async def _open_service(self, cuit: str) -> None:
        selector = f'#aplicaciones a[onclick*="ir_servicio(43,{cuit})"]'
        try:
            await self._page.wait_for_selector(selector, state="attached", timeout=60_000)
            await self._page.evaluate(
                """selector => {
                    const aplicaciones = document.querySelector('#aplicaciones');
                    const link = document.querySelector(selector);
                    if (!link) throw new Error('servicio ausente');
                    if (aplicaciones) {
                        aplicaciones.style.display = 'block';
                        aplicaciones.style.opacity = '1';
                    }
                    link.click();
                }""",
                selector,
            )
            await self._page.wait_for_selector("#fechaDesdeCo", state="attached", timeout=60_000)
        except Exception as exc:
            raise TargetUnavailableError(
                "No se pudo abrir Gestión-AR Agentes de Recaudación",
                diagnostic_code="agip_service_unavailable",
            ) from exc

    async def _set_periodo(self, selector: str, periodo: str, field: str) -> None:
        if not _periodo_valido(periodo):
            raise ValueError(f"Período AGIP inválido para {field}")
        month, year = (int(part) for part in periodo.split("/"))
        await self._page.wait_for_selector(selector, state="attached", timeout=30_000)
        result = await self._page.evaluate(
            """({selector, value, month, year}) => {
                const el = document.querySelector(selector);
                if (!el) throw new Error('campo de período ausente');
                const $ = window.jQuery || window.$;
                if (!$) throw new Error('datepicker no disponible');
                const $el = $(el);
                const dateValue = new Date(year, month - 1, 1);
                el.removeAttribute('readonly');
                el.removeAttribute('disabled');
                if (typeof $el.datepicker === 'function') {
                    try { $el.datepicker('update', value); } catch (_) {}
                    try { $el.datepicker('setDate', dateValue); } catch (_) {}
                    try { $el.datepicker('hide'); } catch (_) {}
                }
                el.value = value;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new Event('blur', { bubbles: true }));
                return {actual: el.value || null};
            }""",
            {"selector": selector, "value": periodo, "month": month, "year": year},
        )
        if result.get("actual") != periodo:
            raise TargetUnavailableError(
                f"AGIP no conservó el período {field}",
                diagnostic_code="agip_period_filter_failed",
            )

    async def _set_periodos(self, desde: str, hasta: str) -> None:
        await self._set_periodo("#fechaDesdeCo", desde, "desde")
        await self._set_periodo("#fechaHastaCo", hasta, "hasta")
        valores = await self._page.evaluate(
            """() => ({
                desde: document.querySelector('#fechaDesdeCo')?.value ?? null,
                hasta: document.querySelector('#fechaHastaCo')?.value ?? null
            })"""
        )
        if valores.get("desde") != desde or valores.get("hasta") != hasta:
            raise TargetUnavailableError(
                "AGIP no mantuvo el rango de períodos solicitado",
                diagnostic_code="agip_period_filter_failed",
            )

    async def _esperar_resultados(self) -> str:
        try:
            handle = await self._page.wait_for_function(
                """selectors => {
                    const visible = el => {
                        if (!el) return false;
                        const rect = el.getBoundingClientRect();
                        return rect.width > 0 && rect.height > 0;
                    };
                    if (selectors.some(selector => visible(document.querySelector(selector)))) {
                        return 'con_datos';
                    }
                    const modal = document.querySelector('#myModalBody');
                    if (
                        modal && visible(modal)
                        && (modal.textContent || '').includes('No hay datos')
                    ) {
                        return 'sin_datos';
                    }
                    return null;
                }""",
                arg=list(_RESULT_EXCEL_BUTTONS),
                timeout=60_000,
            )
            return await handle.json_value()
        except Exception as exc:
            raise TargetUnavailableError(
                "AGIP no mostró resultados ni aviso de período sin datos",
                diagnostic_code="agip_results_timeout",
            ) from exc

    async def _descargar(self, selector: str, destino: Path) -> bool:
        boton = self._page.locator(selector)
        try:
            if not await boton.count() or not await boton.first.is_visible():
                return False
            destino.parent.mkdir(parents=True, exist_ok=True)
            async with self._page.expect_download(timeout=60_000) as info:
                await boton.first.click(force=True)
            archivo = await info.value
            await archivo.save_as(str(destino))
            return destino.is_file() and destino.stat().st_size > 0
        except Exception:
            return False

    async def consultar_retper(self, desde_mmyyyy: str, hasta_mmyyyy: str, destino: Path) -> None:
        """Consulta el período y guarda en destino ZIP con todas las salidas AGIP."""
        if not self._logged_in:
            raise TargetUnavailableError(
                "La sesión AGIP no está autenticada",
                diagnostic_code="agip_session_not_logged_in",
            )
        if not _periodo_valido(desde_mmyyyy) or not _periodo_valido(hasta_mmyyyy):
            raise ValueError("Los períodos deben tener formato MM/AAAA")
        if tuple(int(x) for x in desde_mmyyyy.split("/"))[::-1] > tuple(
            int(x) for x in hasta_mmyyyy.split("/")
        )[::-1]:
            raise ValueError("El período desde no puede ser posterior al período hasta")

        await self._set_periodos(desde_mmyyyy, hasta_mmyyyy)
        await self._page.get_by_role("button", name="Buscar").click()
        estado = await self._esperar_resultados()
        if estado == "sin_datos":
            raise TargetUnavailableError(
                f"AGIP informó que no hay datos para {desde_mmyyyy}-{hasta_mmyyyy}",
                diagnostic_code="agip_no_data",
            )
        if estado != "con_datos":
            raise TargetUnavailableError(
                "AGIP devolvió un estado de resultados no reconocido",
                diagnostic_code="agip_results_unrecognized",
            )

        cuit = self._representado
        descargados: list[Path] = []
        destino.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="agip_", dir=destino.parent) as temp_dir:
            directorio = Path(temp_dir)
            for seccion in _SECTIONS:
                tab = self._page.locator("#divConsultado").get_by_text(
                    seccion["tab_text"], exact=True
                )
                try:
                    if not await tab.count() or not await tab.first.is_visible():
                        continue
                    await tab.first.click()
                    await self._page.wait_for_timeout(1_000)
                except Exception:
                    continue
                for selector, tipo, extension in seccion["files"]:
                    nombre = _nombre_archivo(
                        cuit, tipo, desde_mmyyyy, hasta_mmyyyy, extension
                    )
                    archivo = directorio / nombre
                    if await self._descargar(selector, archivo):
                        descargados.append(archivo)
            _empaquetar_descargas(descargados, Path(destino))

    async def close(self) -> None:
        """El BrowserFactory cierra el contexto; aquí se sueltan referencias."""
        self._credentials = None
        self._page = None
