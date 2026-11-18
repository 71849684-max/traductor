"""Acceso al paquete .docx sin destruir el formato.

Principio (AGENTS.md seccion 8): el documento traducido NO se genera desde
cero. Se abre una copia del original, se localizan los nodos y solo se
sustituye el contenido de los ``w:t``. Todo lo que no se toca se vuelve a
escribir *byte a byte*: ``styles.xml``, ``numbering.xml``, ``settings.xml``,
``media/``, etc. se copian del zip original sin pasar por lxml.

Este modulo solo localiza y expone nodos; no crea parrafos, tablas ni
secciones.
"""

from __future__ import annotations

import re
import zipfile
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
}
P = {k: "{%s}" % v for k, v in NS.items()}
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

PARTES_TEXTO_FIJAS = ("word/document.xml",)
RE_HEADER = re.compile(r"^word/(header|footer)(\d*)\.xml$")

# elementos de ancho cero que viven como hijos directos del parrafo
CONTENEDORES_PROTEGIDOS = (
    "w:hyperlink", "w:sdt", "w:smartTag", "w:ins", "w:del", "w:moveFrom",
    "w:moveTo", "w:fldSimple", "w:customXml", "w:dir", "w:bdo",
)
BARRERAS = (
    "w:proofErr", "w:bookmarkStart", "w:bookmarkEnd", "w:commentRangeStart",
    "w:commentRangeEnd", "w:permStart", "w:permEnd", "w:moveFromRangeStart",
    "w:moveFromRangeEnd", "w:moveToRangeStart", "w:moveToRangeEnd",
)


def q(tag: str) -> str:
    """'w:p' -> '{namespace}p'."""
    pre, local = tag.split(":")
    return P[pre] + local


def nombre(el) -> str:
    return etree.QName(el).localname


def es_run_texto(r) -> bool:
    """Un run es traducible si solo contiene texto y elementos de ancho cero
    previos al texto (``w:tab``, ``w:lastRenderedPageBreak``...)."""
    hijos = [c for c in r if c.tag != q("w:rPr")]
    if not hijos:
        return False
    visto_texto = False
    for h in hijos:
        n = nombre(h)
        if n == "t":
            visto_texto = True
        elif n in ("tab", "lastRenderedPageBreak", "noBreakHyphen", "softHyphen"):
            if visto_texto:
                return False
        else:
            return False
    return visto_texto


def texto_de_run(r) -> str:
    return "".join(t.text or "" for t in r if t.tag == q("w:t"))


def texto_de_parrafo(p) -> str:
    return "".join(t.text or "" for t in p.iter(q("w:t")))


def estilo_de_parrafo(p) -> str:
    ps = p.find(q("w:pPr") + "/" + q("w:pStyle"))
    return (ps.get(q("w:val")) if ps is not None else "") or ""


@dataclass
class Parrafo:
    """Referencia a un parrafo real del paquete (no se clona)."""

    id: str
    elemento: object
    parte: str
    contenedor: str            # cuerpo | tabla | cuadro-texto | cabecera | pie
    estilo: str = ""
    tabla: int = 0
    fila: int = 0
    columna: int = 0
    orden: int = 0
    extra: dict = field(default_factory=dict)

    @property
    def texto(self) -> str:
        return texto_de_parrafo(self.elemento)


class Documento:
    """Paquete .docx abierto en memoria."""

    def __init__(self, ruta: str | Path):
        self.ruta = Path(ruta)
        if not self.ruta.exists():
            raise FileNotFoundError(self.ruta)
        with zipfile.ZipFile(self.ruta) as z:
            self.partes: OrderedDict[str, bytes] = OrderedDict(
                (i.filename, z.read(i.filename))
                for i in z.infolist()
                if not i.is_dir()
            )
            self._infos = {i.filename: i for i in z.infolist() if not i.is_dir()}
        self._arboles: dict[str, object] = {}
        self._sucios: set[str] = set()

    # -- acceso --------------------------------------------------------
    def xml(self, parte: str):
        if parte not in self._arboles:
            self._arboles[parte] = etree.fromstring(self.partes[parte])
        return self._arboles[parte]

    def marcar_sucio(self, parte: str) -> None:
        self._sucios.add(parte)

    @property
    def partes_modificadas(self) -> frozenset[str]:
        return frozenset(self._sucios)

    def guardar(self, destino: str | Path) -> Path:
        """Escribe el paquete. Las partes no modificadas se copian tal cual
        y se conservan la compresion y las fechas de cada entrada."""
        destino = Path(destino)
        if destino.resolve() == self.ruta.resolve():
            raise ValueError("no se sobrescribe el archivo original")
        with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
            for parte, datos in self.partes.items():
                if parte in self._sucios:
                    arbol = self._arboles[parte]
                    datos = etree.tostring(
                        arbol, xml_declaration=True, encoding="UTF-8",
                        standalone=True,
                    )
                original = self._infos.get(parte)
                if original is None:
                    z.writestr(parte, datos)
                    continue
                info = zipfile.ZipInfo(parte, date_time=original.date_time)
                info.compress_type = original.compress_type
                info.external_attr = original.external_attr
                info.internal_attr = original.internal_attr
                info.create_system = original.create_system
                z.writestr(info, datos)
        return destino

    # -- partes con texto ---------------------------------------------
    def partes_con_texto(self) -> list[str]:
        extras = sorted(
            n for n in self.partes
            if RE_HEADER.match(n) and n.endswith(".xml")
        )
        return [p for p in PARTES_TEXTO_FIJAS if p in self.partes] + extras

    def relaciones(self, parte: str) -> dict[str, tuple[str, str]]:
        carpeta = str(Path(parte).parent).replace("\\", "/")
        rels = f"{carpeta}/_rels/{Path(parte).name}.rels".lstrip("/")
        raiz = etree.fromstring(self.partes[rels]) if rels in self.partes else None
        salida: dict[str, tuple[str, str]] = {}
        if raiz is None:
            return salida
        for rel in raiz:
            salida[rel.get("Id")] = (
                rel.get("Type", "").rsplit("/", 1)[-1],
                rel.get("Target", ""),
            )
        return salida

    # -- recorrido -----------------------------------------------------
    def parrafos(self) -> list[Parrafo]:
        """Todos los parrafos en orden: cuerpo y tablas, luego cabeceras/pies."""
        salida: list[Parrafo] = []
        orden = 0
        for parte in self.partes_con_texto():
            raiz = self.xml(parte)
            prefijo = self._prefijo(parte)
            tablas = list(raiz.iter(q("w:tbl")))
            idx_tabla = {id(t): i for i, t in enumerate(tablas)}
            contador: Counter[str] = Counter()
            for p in raiz.iter(q("w:p")):
                orden += 1
                tipo, detalle = self._ubicacion(p, idx_tabla)
                if parte == "word/document.xml":
                    if tipo == "tabla":
                        pid = detalle[0]
                    elif tipo == "cuadro-texto":
                        contador["TB"] += 1
                        pid = f"TB{contador['TB']:02d}"
                    else:
                        contador["P"] += 1
                        pid = f"P{contador['P']:04d}"
                else:
                    contador["X"] += 1
                    pid = f"{prefijo}{contador['X']:04d}"
                salida.append(
                    Parrafo(
                        id=pid,
                        elemento=p,
                        parte=parte,
                        contenedor=tipo if parte == "word/document.xml" else (
                            "cabecera" if prefijo.startswith("H") else "pie"
                        ),
                        estilo=estilo_de_parrafo(p),
                        tabla=detalle[1] if tipo == "tabla" else 0,
                        fila=detalle[2] if tipo == "tabla" else 0,
                        columna=detalle[3] if tipo == "tabla" else 0,
                        orden=orden,
                    )
                )
        return salida

    @staticmethod
    def _prefijo(parte: str) -> str:
        m = RE_HEADER.match(parte)
        if not m:
            return "P"
        tipo, num = m.group(1), m.group(2) or "1"
        return ("H" if tipo == "header" else "F") + num

    @staticmethod
    def _ubicacion(p, idx_tabla: dict):
        tabla = None
        cuadro = False
        anc = p.getparent()
        while anc is not None:
            if anc.tag == q("w:txbxContent"):
                cuadro = True
            if anc.tag == q("w:tbl") and tabla is None:
                tabla = anc
            anc = anc.getparent()
        if cuadro:
            return "cuadro-texto", ""
        if tabla is None:
            return "cuerpo", ""
        ti = idx_tabla.get(id(tabla), -1) + 1
        tr = p.getparent()
        while tr is not None and tr.tag != q("w:tr"):
            tr = tr.getparent()
        fila = col = 0
        if tr is not None:
            filas = tabla.findall(q("w:tr"))
            if tr in filas:
                fila = filas.index(tr) + 1
            tc = p.getparent()
            while tc is not None and tc.tag != q("w:tc"):
                tc = tc.getparent()
            if tc is not None:
                celdas = tr.findall(q("w:tc"))
                if tc in celdas:
                    col = celdas.index(tc) + 1
        return "tabla", (f"T{ti:02d}R{fila:02d}C{col:02d}", ti, fila, col)

    # -- utilidades ----------------------------------------------------
    def indice(self) -> dict[str, Parrafo]:
        return {p.id: p for p in self.parrafos()}

    def imagenes(self) -> list[tuple[str, str]]:
        """[(id de parrafo, parte de media)] de las imagenes del cuerpo."""
        salida = []
        for p in self.parrafos():
            for d in p.elemento.iter(q("w:drawing")):
                blip = d.find(".//" + q("a:blip"))
                rid = blip.get(q("r:embed")) if blip is not None else None
                rel = self.relaciones(p.parte).get(rid or "", ("", "?"))[1]
                salida.append((p.id, rel))
        return salida
