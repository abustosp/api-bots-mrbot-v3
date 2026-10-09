"""Fakes mínimos de Playwright para los tests de portales (sin pytest-asyncio).

``Pagina`` resuelve locators por una clave textual (``role:button:Nombre``,
``text:Texto``, ``loc:selector``, ``title:Titulo``). Cada clave se registra con
un ``Elemento`` que puede ser visible/ausente, dinámico (callables), fallar en
el N-ésimo uso de una acción y disparar callbacks. Una clave no registrada se
comporta como un elemento ausente (``count() == 0``, acciones con timeout).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable


def _val(valor: Any) -> Any:
    return valor() if callable(valor) else valor


def _texto_de(patron: Any) -> str:
    if isinstance(patron, re.Pattern):
        return patron.pattern.replace("\\", "")
    return str(patron)


class Elemento:
    def __init__(
        self,
        *,
        visible: Any = True,
        count: Any = None,
        texto: Any = "",
        attrs: dict[str, str] | None = None,
        on: dict[str, Callable[..., Any]] | None = None,
        fallar: dict[str, set[int]] | None = None,
        marcado: bool = False,
    ) -> None:
        self.visible = visible
        self._count = count
        self.texto = texto
        self.attrs = attrs or {}
        self.on = on or {}
        self.fallar = fallar or {}
        self.marcado = marcado
        self.usos: dict[str, int] = {}

    def count(self) -> int:
        if self._count is not None:
            return int(_val(self._count))
        return 1


class Loc:
    def __init__(self, pagina: "Pagina", clave: str) -> None:
        self._pagina = pagina
        self._clave = clave

    # -- navegación (todo resuelve a la misma clave) --------------------
    @property
    def first(self) -> "Loc":
        return self

    @property
    def last(self) -> "Loc":
        return self

    def nth(self, _: int) -> "Loc":
        return self

    def locator(self, selector: str, **_: Any) -> "Loc":
        return Loc(self._pagina, f"{self._clave}>{selector}")

    def _el(self) -> Elemento | None:
        return self._pagina.registro.get(self._clave)

    # -- lectura --------------------------------------------------------
    async def count(self) -> int:
        el = self._el()
        return el.count() if el is not None else 0

    async def is_visible(self, **_: Any) -> bool:
        el = self._el()
        return bool(el is not None and _val(el.visible))

    async def inner_text(self, **_: Any) -> str:
        el = self._el()
        if el is None:
            raise TimeoutError(self._clave)
        return str(_val(el.texto))

    async def text_content(self, **_: Any) -> str:
        return await self.inner_text()

    async def get_attribute(self, nombre: str, **_: Any) -> str | None:
        el = self._el()
        return None if el is None else el.attrs.get(nombre)

    async def is_checked(self, **_: Any) -> bool:
        el = self._el()
        return bool(el is not None and el.marcado)

    async def element_handle(self, **_: Any) -> None:
        return None

    async def wait_for(self, state: str = "visible", **_: Any) -> None:
        el = self._el()
        if el is None or el.count() == 0:
            raise TimeoutError(f"wait_for {self._clave}")
        if state == "visible" and not _val(el.visible):
            raise TimeoutError(f"wait_for visible {self._clave}")

    # -- acciones -------------------------------------------------------
    async def _accion(self, nombre: str, *args: Any, exige_visible: bool = True) -> None:
        el = self._el()
        if el is None or el.count() == 0 or (exige_visible and not _val(el.visible)):
            self._pagina.intentos.append((nombre, self._clave))
            raise TimeoutError(f"{nombre} {self._clave}")
        el.usos[nombre] = el.usos.get(nombre, 0) + 1
        self._pagina.intentos.append((nombre, self._clave))
        if el.usos[nombre] in el.fallar.get(nombre, set()):
            raise TimeoutError(f"{nombre} #{el.usos[nombre]} {self._clave}")
        self._pagina.eventos.append((nombre, self._clave, *args))
        callback = el.on.get(nombre)
        if callback is not None:
            callback(*args)

    async def click(self, **_: Any) -> None:
        await self._accion("click")

    async def fill(self, valor: str, **_: Any) -> None:
        await self._accion("fill", valor)

    async def press(self, tecla: str, **_: Any) -> None:
        await self._accion("press", tecla)

    async def check(self, **_: Any) -> None:
        await self._accion("check")
        el = self._el()
        if el is not None:
            el.marcado = True

    async def select_option(self, valor: Any = None, **_: Any) -> None:
        await self._accion("select_option", valor)

    async def set_input_files(self, ruta: str, **_: Any) -> None:
        await self._accion("set_input_files", Path(ruta).name, exige_visible=False)

    async def scroll_into_view_if_needed(self, **_: Any) -> None:
        return None


class Descarga:
    suggested_filename = "descarga.pdf"

    def __init__(self, datos: bytes) -> None:
        self._datos = datos

    async def save_as(self, destino: str) -> None:
        Path(destino).write_bytes(self._datos)


class _EventoDescarga:
    def __init__(self, pagina: "Pagina") -> None:
        self._pagina = pagina
        self._pagina.descarga_pendiente = None

    async def __aenter__(self) -> "_EventoDescarga":
        return self

    async def __aexit__(self, *_: Any) -> bool:
        return False

    @property
    def value(self) -> Any:
        async def obtener() -> Descarga:
            if self._pagina.descarga_pendiente is None:
                raise TimeoutError("sin descarga")
            return Descarga(self._pagina.descarga_pendiente)

        return obtener()


class _EventoPopup:
    def __init__(self, pagina: "Pagina") -> None:
        self._pagina = pagina

    async def __aenter__(self) -> "_EventoPopup":
        return self

    async def __aexit__(self, *_: Any) -> bool:
        return False

    @property
    def value(self) -> Any:
        async def obtener() -> "Pagina":
            if self._pagina.popup is None:
                raise TimeoutError("sin popup")
            return self._pagina.popup

        return obtener()


class Pagina:
    def __init__(self, url: str = "https://seti.afip.gob.ar/") -> None:
        self.url = url
        self.registro: dict[str, Elemento] = {}
        self.eventos: list[tuple[Any, ...]] = []
        self.intentos: list[tuple[str, str]] = []
        self.descarga_pendiente: bytes | None = None
        self.popup: Pagina | None = None
        self.cerrada = False
        self.evaluaciones: list[tuple[str, Any]] = []
        self.evaluar: Callable[[str, Any], Any] = lambda script, arg: None

    def poner(self, clave: str, elemento: Elemento | None = None, **kwargs: Any) -> Elemento:
        elemento = elemento or Elemento(**kwargs)
        self.registro[clave] = elemento
        return elemento

    def descargar_al_click(self, datos: bytes) -> Callable[..., None]:
        def accion(*_: Any) -> None:
            self.descarga_pendiente = datos

        return accion

    def clicks(self, clave: str) -> int:
        return sum(1 for e in self.eventos if e[0] == "click" and e[1] == clave)

    # -- API de Playwright ----------------------------------------------
    def get_by_role(self, rol: str, *, name: Any = None, **_: Any) -> Loc:
        return Loc(self, f"role:{rol}:{_texto_de(name)}")

    def get_by_text(self, texto: Any, **_: Any) -> Loc:
        return Loc(self, f"text:{_texto_de(texto)}")

    def get_by_title(self, titulo: Any, **_: Any) -> Loc:
        return Loc(self, f"title:{_texto_de(titulo)}")

    def locator(self, selector: str, **_: Any) -> Loc:
        return Loc(self, f"loc:{selector}")

    async def goto(self, url: str, **_: Any) -> None:
        self.url = url
        self.eventos.append(("goto", url))

    async def wait_for_load_state(self, *_: Any, **__: Any) -> None:
        return None

    async def wait_for_timeout(self, *_: Any, **__: Any) -> None:
        return None

    async def wait_for_selector(self, *_: Any, **__: Any) -> None:
        return None

    async def evaluate(self, script: str, arg: Any = None) -> Any:
        self.evaluaciones.append((script, arg))
        return self.evaluar(script, arg)

    def expect_download(self, **_: Any) -> _EventoDescarga:
        return _EventoDescarga(self)

    def expect_popup(self, **_: Any) -> _EventoPopup:
        return _EventoPopup(self)

    async def close(self) -> None:
        self.cerrada = True
