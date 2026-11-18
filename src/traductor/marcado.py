"""Serializacion de un parrafo a texto con etiquetas y marcadores, y vuelta.

Formato de intercambio (AGENTS.md seccion 9), por ejemplo::

    El <b>monitoreo</b> de glucosa⟦X01⟧ permite ver el <i>tiempo en rango</i>.

Reglas de diseno:

* Cada *pieza de texto* es un tramo contiguo con un ``rPr`` identico; se
  serializa como ``<tag>texto</tag>``.  La etiqueta es semantica (``b``,
  ``i``, ``u``, ``sup``, ``sub``) cuando el formato tiene exactamente una de
  esas propiedades; si no, es ``fN``.  La tabla etiqueta -> ``rPr`` original
  vive en :class:`Serializado`, de modo que la reconstruccion copia el
  formato exacto, no una aproximacion.
* Lo que no es texto traducible se sustituye por un marcador ``⟦Xnn⟧``:
  imagenes, campos completos (``fldChar begin`` .. ``end``, incluidos los de
  Zotero), hipervinculos, tabulaciones sueltas y simbolos.
* Los elementos de ancho cero hijos del parrafo (``w:proofErr``, marcadores
  de Word, rangos de comentario) se conservan **por posicion de caracter**,
  sin ensuciar el texto que ve el modelo.
"""

from __future__ import annotations

import copy
import html
import re
from dataclasses import dataclass, field

from lxml import etree

from .docx_io import (
    BARRERAS,
    XML_SPACE,
    es_run_texto,
    nombre,
    q,
    texto_de_run,
)

MARCA_IZQ = "\u27e6"   # ⟦
MARCA_DER = "\u27e7"   # ⟧
INICIO = MARCA_IZQ
FIN = MARCA_DER

TRANSITORIOS = ("tab", "lastRenderedPageBreak", "noBreakHyphen", "softHyphen")


class MarcadoInvalido(ValueError):
    """La traduccion no respeta etiquetas o marcadores."""


@dataclass
class Pieza:
    tipo: str                       # "texto" | "marcador"
    etiqueta: str = ""
    texto: str = ""
    marcador: str = ""
    nodos: list = field(default_factory=list)
    prototipo: object | None = None


@dataclass
class Serializado:
    id: str
    texto: str
    plano: str
    piezas: list[Pieza]
    etiquetas: dict[str, bytes]
    prototipos: dict[str, object]
    marcadores: dict[str, list]
    fragmentos: list[tuple[int, int, object]] = field(default_factory=list)
    barreras: list[tuple[int, object]] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    @property
    def protegido(self) -> bool:
        """Sin texto traducible (solo imagenes/campos)."""
        return not any(p.tipo == "texto" and p.texto for p in self.piezas)

    @property
    def caracteres(self) -> int:
        return len(self.plano)


# --------------------------------------------------------------------------
# serializacion
# --------------------------------------------------------------------------

def _c14n(el) -> bytes:
    return etree.tostring(el, method="c14n") if el is not None else b""


def clonar_en(el, padre):
    """Igual que :func:`clonar` pero creando el nodo colgando de ``padre``.

    Asi los prefijos se resuelven con el mapa vigente en el arbol y solo se
    redeclaran los que el original declaraba *localmente* (p. ej. ``a:`` en
    algunos nodos de dibujo).
    """
    heredado = padre.nsmap
    local = {p: u for p, u in el.nsmap.items() if heredado.get(p) != u}
    nuevo = etree.SubElement(padre, el.tag, nsmap=local or None)
    for clave, valor in el.attrib.items():
        nuevo.set(clave, valor)
    nuevo.text = el.text
    nuevo.tail = el.tail
    for hijo in el:
        clonar_en(hijo, nuevo)
    return nuevo


def _rpr(r):
    return _c14n(r.find(q("w:rPr")))


def _estructura(r) -> tuple[str, ...]:
    """Hijos del run distintos de ``w:rPr`` y ``w:t`` (tab, saltos...)."""
    return tuple(nombre(c) for c in r if c.tag not in (q("w:rPr"), q("w:t")))


def _semantica(rpr) -> str | None:
    """'b'/'i'/'u'/'sup'/'sub' si el formato tiene exactamente una."""
    if rpr is None:
        return None
    props = []

    def activo(tag):
        el = rpr.find(q(tag))
        if el is None:
            return False
        v = el.get(q("w:val"))
        return v is None or v.lower() not in ("0", "false", "off", "none")

    if activo("w:b"):
        props.append("b")
    if activo("w:i"):
        props.append("i")
    u = rpr.find(q("w:u"))
    if u is not None and (u.get(q("w:val")) or "single") != "none":
        props.append("u")
    va = rpr.find(q("w:vertAlign"))
    if va is not None:
        v = va.get(q("w:val"))
        if v == "superscript":
            props.append("sup")
        elif v == "subscript":
            props.append("sub")
    return props[0] if len(props) == 1 else None


def _etiqueta_para(run, etiquetas: dict[str, bytes],
                   prototipos: dict[str, object]) -> str:
    clave = _rpr(run)
    for tag, val in etiquetas.items():
        if val == clave:
            return tag
    candidata = _semantica(run.find(q("w:rPr")))
    tag = candidata if candidata and candidata not in etiquetas else None
    if tag is None:
        n = 1
        while f"f{n}" in etiquetas:
            n += 1
        tag = f"f{n}"
    etiquetas[tag] = clave
    prototipos[tag] = run
    return tag


def _es_inicio_campo(r) -> bool:
    for fc in r.iter(q("w:fldChar")):
        if fc.get(q("w:fldCharType")) == "begin":
            return True
    return False


def _fin_campo(hijos: list, i: int) -> int:
    """Indice del ultimo nodo del campo que empieza en ``hijos[i]``."""
    prof = 0
    for j in range(i, len(hijos)):
        if hijos[j].tag != q("w:r"):
            continue
        for fc in hijos[j].iter(q("w:fldChar")):
            t = fc.get(q("w:fldCharType"))
            if t == "begin":
                prof += 1
            elif t == "end":
                prof -= 1
        if prof == 0:
            return j
    return len(hijos) - 1


def _escapar(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _partir_protegidos(txt: str, frases) -> list[tuple[str, str]]:
    """Divide el texto en tramos ``("texto"|"protegido", subcadena)``."""
    if not frases or not txt:
        return [("texto", txt)]
    patron = re.compile("|".join(re.escape(f) for f in
                                 sorted(set(frases), key=len, reverse=True)))
    salida: list[tuple[str, str]] = []
    ultimo = 0
    for m in patron.finditer(txt):
        if m.start() > ultimo:
            salida.append(("texto", txt[ultimo:m.start()]))
        salida.append(("protegido", m.group(0)))
        ultimo = m.end()
    if ultimo < len(txt):
        salida.append(("texto", txt[ultimo:]))
    return salida or [("texto", txt)]


def _run_con(run, texto: str):
    """Copia desconectada del run con ``texto`` como contenido."""
    r = copy.deepcopy(run)
    ts = [c for c in r if c.tag == q("w:t")]
    pos = list(r).index(ts[0]) if ts else len(r)
    for c in ts:
        r.remove(c)
    t = etree.Element(q("w:t"))
    t.set(XML_SPACE, "preserve")
    t.text = texto
    r.insert(pos, t)
    return r


def serializar(p, pid: str = "", proteger=()) -> Serializado:
    """Serializa el parrafo a texto con etiquetas y marcadores.

    ``proteger`` es una lista de frases que deben quedar intactas: se extraen
    como marcadores.  Se aplica **despues** de fusionar runs, para frases que
    el original reparte en varios runs (p. ej. el nombre de la revista en el
    encabezado).
    """
    hijos = [c for c in p if c.tag != q("w:pPr")]
    piezas: list[Pieza] = []
    etiquetas: dict[str, bytes] = {}
    prototipos: dict[str, object] = {}
    fragmentos: list[tuple[int, int, object]] = []
    marcadores: dict[str, list] = {}
    barreras: list[tuple[int, object]] = []
    avisos: list[str] = []
    offset = 0
    n_marca = 0
    buffer: list[tuple[object, str]] = []

    def nuevo_marcador(nodos: list, restaurar: list | None = None) -> str:
        nonlocal n_marca
        n_marca += 1
        mid = f"X{n_marca:02d}"
        marcadores[mid] = restaurar if restaurar is not None else nodos
        piezas.append(Pieza("marcador", marcador=mid, nodos=nodos))
        return mid

    def volcar() -> None:
        """Convierte el buffer de runs en piezas (aplicando ``proteger``)."""
        nonlocal offset
        if not buffer:
            return
        runs = list(buffer)
        buffer.clear()
        base = offset
        tag = _etiqueta_para(runs[0][0], etiquetas, prototipos)
        texto = "".join(t for _, t in runs)

        rangos: list[tuple[int, int, object]] = []
        acum = 0
        for run, t in runs:
            rangos.append((acum, acum + len(t), run))
            acum += len(t)

        def proto_en(rel: int):
            for a, b, run in rangos:
                if a <= rel < b:
                    return run
            return rangos[-1][2]

        rel = 0
        for clase, sub in _partir_protegidos(texto, proteger):
            if not sub:
                continue
            ini, fin = rel, rel + len(sub)
            rel = fin
            if clase == "protegido":
                nuevo_marcador([r for _, _, r in rangos],
                               [_run_con(proto_en(ini), sub)])
                continue
            piezas.append(Pieza("texto", etiqueta=tag, texto=sub,
                                nodos=[r for _, _, r in rangos],
                                prototipo=runs[0][0]))
            for a, b, run in rangos:
                lo, hi = max(a, ini), min(b, fin)
                if lo < hi:
                    fragmentos.append((base + lo, base + hi, run))
            offset += len(sub)

    i = 0
    while i < len(hijos):
        el = hijos[i]
        if el.tag == q("w:r"):
            if _es_inicio_campo(el):
                volcar()
                j = _fin_campo(hijos, i)
                nuevo_marcador(hijos[i:j + 1])
                i = j + 1
                continue
            if es_run_texto(el):
                txt = texto_de_run(el)
                if txt:
                    tag = _etiqueta_para(el, etiquetas, prototipos)
                    if buffer and (
                            _etiqueta_para(buffer[0][0], etiquetas, prototipos) != tag
                            or _estructura(buffer[0][0]) != _estructura(el)):
                        volcar()
                    buffer.append((el, txt))
                else:
                    volcar()
                    avisos.append("run de texto vacio protegido")
                    nuevo_marcador([el])
                i += 1
                continue
            volcar()
            nuevo_marcador([el])          # imagen, tab suelta, simbolo, etc.
            i += 1
            continue
        if el.tag == q("w:sectPr"):
            i += 1                        # se conserva al final, no es contenido
            continue
        if el.tag in {q(t) for t in BARRERAS}:
            # no se vuelca el buffer: las barreras son de ancho cero y se
            # reinsertan por posicion de caracter dentro del texto fusionado
            pendiente = sum(len(t) for _, t in buffer)
            barreras.append((offset + pendiente, el))
            i += 1
            continue
        # hipervinculo, sdt, fldSimple, ins/del, customXml...
        volcar()
        nuevo_marcador([el])
        i += 1
    volcar()

    partes = []
    for pz in piezas:
        if pz.tipo == "texto":
            partes.append(f"<{pz.etiqueta}>{_escapar(pz.texto)}</{pz.etiqueta}>")
        else:
            partes.append(f"{INICIO}{pz.marcador}{FIN}")
    plano = "".join(pz.texto for pz in piezas if pz.tipo == "texto")

    return Serializado(
        id=pid,
        texto="".join(partes),
        plano=plano,
        piezas=piezas,
        etiquetas=etiquetas,
        prototipos=prototipos,
        marcadores=marcadores,
        fragmentos=fragmentos,
        barreras=barreras,
        avisos=avisos,
    )


# --------------------------------------------------------------------------
# lectura de la respuesta del modelo
# --------------------------------------------------------------------------

def parsear(texto: str) -> list[tuple]:
    """Devuelve [("texto", tag, txt) | ("marcador", mid)]."""
    items: list[tuple] = []
    pila: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(texto)
    while i < n:
        c = texto[i]
        if c == INICIO:
            j = texto.find(FIN, i)
            if j < 0:
                raise MarcadoInvalido("marcador sin cerrar")
            mid = texto[i + 1:j].strip()
            if buf:
                items.append(("texto", pila[-1] if pila else "", "".join(buf)))
                buf = []
            items.append(("marcador", mid))
            i = j + 1
            continue
        if c == "<":
            j = texto.find(">", i)
            if j < 0:
                raise MarcadoInvalido("etiqueta sin cerrar")
            token = texto[i + 1:j].strip()
            if buf:
                items.append(("texto", pila[-1] if pila else "", "".join(buf)))
                buf = []
            if token.startswith("/"):
                tag = token[1:].strip()
                if not pila or pila[-1] != tag:
                    raise MarcadoInvalido(f"cierre inesperado </{tag}>")
                pila.pop()
            else:
                tag = token.rstrip("/")
                if not tag:
                    raise MarcadoInvalido("etiqueta vacia")
                pila.append(tag)
            i = j + 1
            continue
        buf.append(c)
        i += 1
    if buf:
        items.append(("texto", pila[-1] if pila else "", "".join(buf)))
    if pila:
        raise MarcadoInvalido(f"etiqueta sin cerrar: <{pila[-1]}>")
    return _unescape(items)


def _unescape(items: list[tuple]) -> list[tuple]:
    salida = []
    for it in items:
        if it[0] == "texto":
            salida.append(("texto", it[1], html.unescape(it[2])))
        else:
            salida.append(it)
    return salida


def texto_plano(traduccion: str) -> str:
    try:
        return "".join(i[2] for i in parsear(traduccion) if i[0] == "texto")
    except MarcadoInvalido:
        return ""


# --------------------------------------------------------------------------
# validacion
# --------------------------------------------------------------------------

def validar(traduccion: str, ser: Serializado) -> list[str]:
    problemas: list[str] = []
    if not ser.piezas:
        return problemas
    try:
        items = parsear(traduccion)
    except MarcadoInvalido as exc:
        return [str(exc)]
    usados = [i[1] for i in items if i[0] == "marcador"]
    esperados = list(ser.marcadores)
    if sorted(usados) != sorted(esperados):
        faltan = [m for m in esperados if m not in usados]
        sobran = [m for m in usados if m not in esperados]
        if faltan:
            problemas.append(f"faltan marcadores: {', '.join(faltan)}")
        if sobran:
            problemas.append(f"marcadores desconocidos: {', '.join(sobran)}")
        repetidos = [m for m in set(usados) if usados.count(m) > 1]
        if repetidos:
            problemas.append(f"marcadores repetidos: {', '.join(repetidos)}")
    else:
        orden_original = [i for i, pz in enumerate(ser.piezas) if pz.tipo == "marcador"]
        if usados != [ser.piezas[k].marcador for k in orden_original]:
            problemas.append("marcadores en orden distinto al original")
    tags = {i[1] for i in items if i[0] == "texto" and i[1]}
    desconocidas = sorted(tags - set(ser.etiquetas))
    if desconocidas:
        problemas.append(f"etiquetas desconocidas: {', '.join(desconocidas)}")
    if not tags and ser.plano.strip():
        problemas.append("la traduccion no trae ninguna etiqueta de formato")
    if not texto_plano(traduccion).strip() and ser.plano.strip():
        problemas.append("la traduccion esta vacia")
    return problemas


# --------------------------------------------------------------------------
# reconstruccion
# --------------------------------------------------------------------------

def _nuevo_run(prototipo, texto: str, padre):
    r = clonar_en(prototipo, padre)
    ts = [c for c in r if c.tag == q("w:t")]
    pos = list(r).index(ts[0]) if ts else len(r)
    for c in ts:
        r.remove(c)
    t = etree.Element(q("w:t"))
    t.set(XML_SPACE, "preserve")
    t.text = texto
    r.insert(pos, t)
    return r


def _emitir_texto(salida: list, proto_fn, txt: str, pos: int,
                  barreras: list[tuple[int, object]], padre) -> None:
    """Parte ``txt`` en los offsets de barrera y emite runs y barreras.

    ``proto_fn(pos)`` devuelve el run prototipo que corresponde a esa posicion
    del texto original, para que cada trozo conserve sus atributos exactos.
    """
    n = len(txt)
    idx = 0
    while idx < n:
        while barreras and barreras[0][0] <= pos + idx:
            salida.append(barreras.pop(0)[1])
        corte = n
        if barreras:
            corte = min(max(barreras[0][0] - pos, 0), n)
            if corte <= idx:
                corte = n
        prototipo = proto_fn(pos + idx)
        if prototipo is None:
            raise MarcadoInvalido("sin formato de referencia para el texto")
        salida.append(_nuevo_run(prototipo, txt[idx:corte], padre))
        idx = corte
    while barreras and barreras[0][0] <= pos + n:
        salida.append(barreras.pop(0)[1])


def aplicar(p, traduccion: str, ser: Serializado) -> None:
    """Reescribe el contenido del parrafo conservando ``pPr`` y los nodos no
    consumidos. Lanza :class:`MarcadoInvalido` si la validacion falla."""
    problemas = validar(traduccion, ser)
    if problemas:
        raise MarcadoInvalido("; ".join(problemas))
    if not ser.piezas:
        return

    items = parsear(traduccion)
    nuevo_plano = "".join(i[2] for i in items if i[0] == "texto")
    igual = nuevo_plano == ser.plano
    barreras = list(ser.barreras)
    descartados: list = []
    if not igual:
        # los rangos del corrector solo tienen sentido con el texto original
        barreras = [b for b in ser.barreras if nombre(b[1]) != "proofErr"]
        descartados = [n for _, n in ser.barreras if nombre(n) == "proofErr"]
    salida: list = []

    piezas_texto = [pz for pz in ser.piezas if pz.tipo == "texto"]
    inicios: dict[int, int] = {}
    acum = 0
    for pz in piezas_texto:
        inicios[id(pz)] = acum
        acum += len(pz.texto)

    items_texto = [k for k, it in enumerate(items) if it[0] == "texto" and it[2]]
    mapa: dict[int, Pieza] = {}
    if len(items_texto) == len(piezas_texto):
        mapa = dict(zip(items_texto, piezas_texto))
    else:
        colas: dict[str, list[Pieza]] = {}
        for pz in piezas_texto:
            colas.setdefault(pz.etiqueta, []).append(pz)
        usados: dict[str, int] = {}
        for k in items_texto:
            tag = items[k][1]
            cola = colas.get(tag) or []
            if not cola:
                raise MarcadoInvalido(f"etiqueta sin formato asociado: {tag}")
            pos_cola = usados.get(tag, 0)
            usados[tag] = pos_cola + 1
            mapa[k] = cola[min(pos_cola, len(cola) - 1)]

    # prototipo segun la posicion dentro del texto original
    def proto_en(pos: int):
        for ini, fin, proto in ser.fragmentos:
            if ini <= pos < fin:
                return proto
        for ini, _fin, proto in ser.fragmentos:
            if ini >= pos:
                return proto
        return ser.fragmentos[-1][2] if ser.fragmentos else None

    for k, it in enumerate(items):
        if it[0] == "marcador":
            for n in ser.marcadores[it[1]]:
                salida.append(clonar_en(n, p))
            continue
        txt = it[2]
        if not txt:
            continue
        pz = mapa.get(k)
        if pz is None:
            continue
        if not ser.fragmentos:
            raise MarcadoInvalido("parrafo sin fragmentos de texto")
        _emitir_texto(salida, proto_en, txt, inicios[id(pz)], barreras, p)
    for _, nodo in barreras:            # barreras sobrantes, al final
        salida.append(nodo)

    consumidos = [n for pz in ser.piezas for n in pz.nodos]
    consumidos.extend(n for _, n in ser.barreras)
    consumidos.extend(descartados)
    if not consumidos:
        return
    hijos = list(p)
    indices = [k for k, ch in enumerate(hijos) if any(ch is c for c in consumidos)]
    if not indices:
        return
    inicio = min(indices)
    conjunto = {id(c) for c in consumidos}
    for ch in list(p):
        if id(ch) in conjunto:
            p.remove(ch)
    for k, nodo in enumerate(salida):
        p.insert(inicio + k, nodo)


def reconstruir_simple(p, traduccion: str, ser: Serializado) -> None:
    """Respaldo (seccion 8.3.6): un solo run con el formato del primer texto."""
    primer = next((pz for pz in ser.piezas if pz.tipo == "texto"), None)
    if primer is None:
        return
    proto = primer.prototipo
    nuevo = _nuevo_run(proto, texto_plano(traduccion) or primer.texto, p)
    consumidos = [n for pz in ser.piezas for n in pz.nodos]
    consumidos.extend(n for _, n in ser.barreras)
    hijos = list(p)
    indices = [k for k, ch in enumerate(hijos) if any(ch is c for c in consumidos)]
    if not indices:
        return
    inicio = min(indices)
    conjunto = {id(c) for c in consumidos}
    for ch in list(p):
        if id(ch) in conjunto:
            p.remove(ch)
    p.insert(inicio, nuevo)
