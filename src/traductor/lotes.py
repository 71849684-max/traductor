"""Agrupacion de parrafos en lotes (AGENTS.md seccion 9).

Reglas:
* Nunca se parte un parrafo: la unidad minima es el parrafo.
* Un lote no mezcla secciones distintas.
* El titulo de una seccion viaja con el primer lote de esa seccion.
* Se respetan ``max_caracteres`` y ``max_parrafos``; si un parrafo solo ya
  supera el limite, forma su propio lote (no se recorta).
"""

from __future__ import annotations

from dataclasses import dataclass, field

MAX_CARACTERES = 4000
MAX_PARRAFOS = 12


@dataclass
class Item:
    """Un parrafo listo para traducir."""

    id: str
    texto: str                      # serializado con etiquetas y marcadores
    seccion: str = "(portada)"
    titulo: bool = False
    traducible: bool = True
    protegido: bool = False

    @property
    def caracteres(self) -> int:
        return len(self.texto)


@dataclass
class Lote:
    id: str
    seccion: str
    items: list[Item] = field(default_factory=list)

    @property
    def caracteres(self) -> int:
        return sum(i.caracteres for i in self.items)

    @property
    def ids(self) -> list[str]:
        return [i.id for i in self.items]

    def __len__(self) -> int:
        return len(self.items)


def agrupar(items, max_caracteres: int = MAX_CARACTERES,
            max_parrafos: int = MAX_PARRAFOS) -> list[Lote]:
    lotes: list[Lote] = []
    actual: Lote | None = None

    def cerrar():
        nonlocal actual
        if actual is not None and actual.items:
            lotes.append(actual)
        actual = None

    def abrir(seccion: str) -> Lote:
        return Lote(id=f"L{len(lotes) + 1:03d}", seccion=seccion)

    for it in items:
        if not it.traducible or it.protegido or not it.texto.strip():
            continue
        if actual is None:
            actual = abrir(it.seccion)
        # cambio de seccion: no se mezclan
        if it.seccion != actual.seccion:
            cerrar()
            actual = abrir(it.seccion)
        # un titulo encabeza su propio lote
        if it.titulo and actual.items:
            cerrar()
            actual = abrir(it.seccion)
        # limites: el parrafo nunca se parte
        if actual.items and (actual.caracteres + it.caracteres > max_caracteres
                             or len(actual) + 1 > max_parrafos):
            cerrar()
            actual = abrir(it.seccion)
        actual.items.append(it)
        if len(actual) >= max_parrafos or actual.caracteres >= max_caracteres:
            cerrar()
    cerrar()
    # renumerar por si algun lote se creo vacio
    for k, lote in enumerate(lotes, 1):
        lote.id = f"L{k:03d}"
    return lotes


def render(lote: Lote) -> str:
    """Bloques ``<<<ID>>>`` para enviar al modelo."""
    return "\n".join(f"<<<{i.id}>>>\n{i.texto}" for i in lote.items)


def parsear_respuesta(texto: str) -> dict[str, str]:
    """Lee la respuesta ``<<<ID>>>`` y devuelve {id: bloque}."""
    import re

    salida: dict[str, str] = {}
    patron = re.compile(r"<<<([A-Za-z0-9_]+)>>>[ \t]*\n?")
    trozos = list(patron.finditer(texto or ""))
    for k, m in enumerate(trozos):
        inicio = m.end()
        fin = trozos[k + 1].start() if k + 1 < len(trozos) else len(texto)
        salida[m.group(1)] = texto[inicio:fin].strip("\n")
    return salida
