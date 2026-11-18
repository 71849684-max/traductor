"""Deteccion de secciones a partir de los titulos (AGENTS.md seccion 6.3).

Un parrafo es titulo si usa un estilo de titulo, o si es corto, no termina en
punto y empieza con numeracion (``1.``, ``2.3.``), o si su texto es una
palabra clave de seccion.
"""

from __future__ import annotations

import re

PALABRAS_CLAVE = {
    "abstract", "resumen", "introduccion", "introducción", "referencias",
    "references", "bibliografia", "bibliografía", "conclusiones",
    "conclusions", "resultados", "results", "discusion", "discusión",
    "metodologia", "metodología", "materials", "agradecimientos",
    "acknowledgments", "palabras clave", "keywords", "objetivo", "objective",
    "trabajos relacionados", "related work", "limitaciones", "limitations",
}

RE_NUMERACION = re.compile(r"^\d+(?:\.\d+)*\.?\s+\S")
RE_REFERENCIAS = re.compile(
    r"^\s*(?:\d+\.?\s*)?(referencias|references|bibliograf[ií]a)\s*:?\s*$",
    re.IGNORECASE,
)


def es_titulo(texto: str, estilo: str = "",
              estilos_titulo: frozenset[str] = frozenset()) -> bool:
    t = (texto or "").strip()
    if not t:
        return False
    if estilo and estilo in estilos_titulo:
        return True
    if estilo and estilo.lower().startswith(("heading", "titulo", "title", "título")):
        return True
    if len(t) < 120 and not t.endswith(".") and RE_NUMERACION.match(t):
        return True
    cabeza = t.rstrip(":")
    for separador in (" - ", " — ", ": "):
        if separador in cabeza:
            cabeza = cabeza.split(separador, 1)[0]
            break
    if cabeza.strip().lower() in PALABRAS_CLAVE:
        return True
    return False


def es_titulo_referencias(texto: str) -> bool:
    return bool(RE_REFERENCIAS.match(texto or ""))


def asignar(parrafos, estilos_titulo: frozenset[str] = frozenset()) -> None:
    """Anota ``p.extra['seccion']`` y ``p.extra['protegido']`` in situ."""
    actual = "(portada)"
    en_referencias = False
    for p in parrafos:
        if p.contenedor in ("cabecera", "pie"):
            p.extra["seccion"] = f"({p.contenedor})"
            p.extra["titulo"] = False
            p.extra["protegido"] = False
            continue
        texto = p.texto
        if es_titulo(texto, p.estilo, estilos_titulo):
            actual = texto.strip()
            en_referencias = es_titulo_referencias(texto)
            p.extra["titulo"] = True
            # El título de la sección sí se traduce (AGENTS.md §7); solo las
            # entradas de la bibliografía quedan protegidas.
            protegido = False
        else:
            p.extra["titulo"] = False
            protegido = en_referencias
        p.extra["seccion"] = actual
        p.extra["protegido"] = protegido
