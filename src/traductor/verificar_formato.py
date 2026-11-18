"""Verificacion automatica de fidelidad de formato (AGENTS.md seccion 8.5).

Compara el original con el traducido y falla si el *esqueleto* cambio:

* se vacian todos los ``w:t`` (es lo unico que puede cambiar),
* se eliminan los ``w:lang`` (unica diferencia permitida),
* se normaliza la particion de runs fusionando runs adyacentes con el mismo
  ``rPr`` y los mismos hijos no textuales,
* se compara el XML canonico resultante.

Ademas exige que ``styles.xml``, ``numbering.xml``, ``settings.xml``, el tema,
las relaciones y ``media/`` sean **byte a byte** iguales, y que no cambien los
recuentos de parrafos, tablas, filas, celdas, imagenes, campos o secciones.
"""

from __future__ import annotations

import copy
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from .docx_io import q

RE_PARTE_TEXTO = re.compile(
    r"^word/(document|header\d*|footer\d*|footnotes|endnotes)\.xml$"
)
PARTES_PERMITIDAS = {"docProps/core.xml"}


def _firma_run(r) -> tuple[bytes, tuple[bytes, ...]]:
    rpr = r.find(q("w:rPr"))
    hijos = tuple(etree.tostring(c, method="c14n")
                  for c in r if c.tag != q("w:rPr"))
    return (etree.tostring(rpr, method="c14n") if rpr is not None else b"", hijos)


def _colapsar_wt(r) -> None:
    """Deja un unico w:t vacio por run."""
    ts = [c for c in r if c.tag == q("w:t")]
    if not ts:
        return
    for extra in ts[1:]:
        r.remove(extra)


def _fusionar_runs(p) -> None:
    ultimo = None
    for ch in list(p):
        if (ch.tag == q("w:r") and ultimo is not None and ultimo.tag == q("w:r")
                and _firma_run(ultimo) == _firma_run(ch)):
            p.remove(ch)
        else:
            ultimo = ch


def normalizar(raiz):
    arbol = copy.deepcopy(raiz)
    for t in list(arbol.iter(q("w:t"))):
        t.text = None
        t.attrib.clear()
    for lang in list(arbol.iter(q("w:lang"))):
        lang.getparent().remove(lang)
    # w:proofErr son rangos del corrector ortografico ligados a offsets de
    # caracteres: no son formato y no sobreviven a una traduccion.
    for pe in list(arbol.iter(q("w:proofErr"))):
        pe.getparent().remove(pe)
    for p in list(arbol.iter(q("w:p"))):
        for r in p.findall(q("w:r")):
            _colapsar_wt(r)
        _fusionar_runs(p)
    return arbol


def _c14n(raiz) -> bytes:
    return etree.tostring(normalizar(raiz), method="c14n")


def _recuentos(raiz) -> Counter:
    c = Counter()
    for tag, etiqueta in (
        ("w:p", "parrafos"), ("w:tbl", "tablas"), ("w:tr", "filas"),
        ("w:tc", "celdas"), ("w:drawing", "imagenes"), ("w:pict", "pict"),
        ("w:hyperlink", "hipervinculos"), ("w:fldChar", "fldChar"),
        ("w:instrText", "instrText"), ("w:sectPr", "secciones"),
        ("w:bookmarkStart", "marcadores"), ("w:txbxContent", "cuadros_texto"),
        ("m:oMath", "ecuaciones"), ("w:sdt", "sdt"),
    ):
        c[etiqueta] = len(list(raiz.iter(q(tag))))
    return c


@dataclass
class Resultado:
    diferencias: list[str] = field(default_factory=list)
    recuentos: dict[str, Counter] = field(default_factory=dict)
    informativas: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.diferencias

    def resumen(self) -> str:
        if self.ok:
            return "Fidelidad de formato: OK"
        return "Fidelidad de formato: FALLA\n" + "\n".join(
            f"  - {d}" for d in self.diferencias
        )


def _leer(ruta: str | Path) -> dict[str, bytes]:
    with zipfile.ZipFile(ruta) as z:
        return {i.filename: z.read(i.filename) for i in z.infolist() if not i.is_dir()}


def comparar(original: str | Path, traducido: str | Path) -> list[str]:
    a = _leer(original)
    b = _leer(traducido)
    dif: list[str] = []

    solo_a = sorted(set(a) - set(b))
    solo_b = sorted(set(b) - set(a))
    if solo_a:
        dif.append(f"partes ausentes en el traducido: {', '.join(solo_a)}")
    if solo_b:
        dif.append(f"partes nuevas en el traducido: {', '.join(solo_b)}")

    for parte in sorted(set(a) & set(b)):
        if parte in PARTES_PERMITIDAS:
            continue
        if RE_PARTE_TEXTO.match(parte):
            if _c14n(etree.fromstring(a[parte])) != _c14n(etree.fromstring(b[parte])):
                dif.append(f"esqueleto XML distinto (salvo w:t/w:lang): {parte}")
        elif a[parte] != b[parte]:
            dif.append(f"parte modificada byte a byte: {parte}")

    for parte in sorted(p for p in a if RE_PARTE_TEXTO.match(p)):
        if parte not in b:
            continue
        ca = _recuentos(etree.fromstring(a[parte]))
        cb = _recuentos(etree.fromstring(b[parte]))
        for clave in ca:
            if ca[clave] != cb.get(clave, 0):
                dif.append(
                    f"{parte}: {clave} {ca[clave]} -> {cb.get(clave, 0)}"
                )
    return dif


def comparar_detallado(original, traducido) -> Resultado:
    res = Resultado(diferencias=comparar(original, traducido))
    a = _leer(original)
    for parte in sorted(p for p in a if RE_PARTE_TEXTO.match(p)):
        res.recuentos[parte] = _recuentos(etree.fromstring(a[parte]))
    if "docProps/core.xml" in a:
        res.informativas.append("docProps/core.xml puede cambiar solo en dc:title")
    return res
