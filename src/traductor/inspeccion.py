#!/usr/bin/env python3
"""Paso 0: inspeccion de estructura de un .docx (solo lectura).

Uso:
    python inspeccion.py "documentos/originales/FORMATO FINAL DE INSULINA.docx" [--max-texto 80]

No modifica el archivo. Lee el paquete OOXML con zipfile + lxml (sin
python-docx, para ver el XML tal cual). Imprime el reporte y lo guarda en
<archivo>.inspeccion.txt.
"""

from __future__ import annotations

import hashlib
import re
import struct
import sys
import zipfile
from collections import Counter, OrderedDict
from pathlib import Path

from lxml import etree

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "v": "urn:schemas-microsoft-com:vml",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
    "o": "urn:schemas-microsoft-com:office:office",
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
}
P = {k: "{%s}" % v for k, v in NS.items()}


def q(tag: str) -> str:
    """'w:p' -> '{ns}p'"""
    pre, local = tag.split(":")
    return P[pre] + local


# --------------------------------------------------------------------------
# utilidades
# --------------------------------------------------------------------------

class Rep:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, s: str = "") -> None:
        self.lines.append(s)

    def titulo(self, n: str, t: str) -> None:
        self("")
        enc = f"{n}. {t}"
        self(enc)
        self("-" * len(enc))

    def texto(self) -> str:
        return "\n".join(self.lines)


def cm(twips: float) -> str:
    return f"{twips / 567.0:.2f} cm"


def emu_cm(emu: float) -> str:
    return f"{emu / 360000.0:.2f} cm"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def _png_px(b: bytes):
    if b[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    w, h = struct.unpack(">II", b[16:24])
    return w, h


def _jpeg_px(b: bytes):
    if b[:2] != b"\xff\xd8":
        return None
    i = 2
    n = len(b)
    while i < n - 9:
        if b[i] != 0xFF:
            i += 1
            continue
        marker = b[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        ln = struct.unpack(">H", b[i + 2:i + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h, w = struct.unpack(">HH", b[i + 5:i + 9])
            return w, h
        i += 2 + ln
    return None


def _gif_px(b: bytes):
    if b[:6] not in (b"GIF87a", b"GIF89a"):
        return None
    w, h = struct.unpack("<HH", b[6:10])
    return w, h


def px_de_imagen(b: bytes):
    for f in (_png_px, _jpeg_px, _gif_px):
        try:
            r = f(b)
        except Exception:
            r = None
        if r:
            return r
    return None


def tipo_imagen(b: bytes, nombre: str) -> str:
    if b[:4] == b"\x89PNG":
        return "PNG"
    if b[:2] == b"\xff\xd8":
        return "JPEG"
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return "GIF"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "WEBP"
    if b[:4] == b"\xd7\xcd\xc6\x9a":
        return "WMF"
    if b[:4] == b"\x01\x00\x00\x00":
        return "EMF"
    if b[:4] == b"II*\x00" or b[:4] == b"MM\x00*":
        return "TIFF"
    return Path(nombre).suffix.lstrip(".").upper() or "?"


def texto_de_par(p, limite: int = 90) -> tuple[str, int]:
    partes = []
    for t in p.iter(q("w:t")):
        # solo el texto de este parrafo (no de parrafos anidados en cuadros de texto)
        anc = t.getparent()
        nearest = None
        while anc is not None:
            if anc.tag == q("w:p"):
                nearest = anc
                break
            anc = anc.getparent()
        if nearest is p and t.text:
            partes.append(t.text)
    txt = "".join(partes)
    txt = txt.replace("\n", " ").replace("\r", " ")
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt[:limite], len(txt)


def own_runs(p) -> list:
    out = []
    for r in p.iter(q("w:r")):
        anc = r.getparent()
        while anc is not None and anc.tag != q("w:p"):
            anc = anc.getparent()
        if anc is p:
            out.append(r)
    return out


def val_on(el) -> bool:
    """Propiedad booleana de run: presente y no desactivada."""
    if el is None:
        return False
    v = el.get(q("w:val"))
    return v is None or v.lower() not in ("0", "false", "off", "none")


def firma_run(r) -> str:
    rpr = r.find(q("w:rPr"))
    if rpr is None:
        return "sin-rPr"
    f = []
    if val_on(rpr.find(q("w:b"))):
        f.append("B")
    if val_on(rpr.find(q("w:i"))):
        f.append("I")
    u = rpr.find(q("w:u"))
    if u is not None and (u.get(q("w:val")) or "single") != "none":
        f.append("U")
    va = rpr.find(q("w:vertAlign"))
    if va is not None:
        v = va.get(q("w:val"))
        if v == "superscript":
            f.append("SUP")
        elif v == "subscript":
            f.append("SUB")
    if val_on(rpr.find(q("w:strike"))):
        f.append("STRIKE")
    if val_on(rpr.find(q("w:caps"))):
        f.append("CAPS")
    if val_on(rpr.find(q("w:smallCaps"))):
        f.append("SMALLCAPS")
    st = rpr.find(q("w:rStyle"))
    if st is not None:
        f.append("st=" + (st.get(q("w:val")) or ""))
    rf = rpr.find(q("w:rFonts"))
    if rf is not None:
        fam = rf.get(q("w:ascii")) or rf.get(q("w:hAnsi"))
        if fam:
            f.append("f=" + fam)
    sz = rpr.find(q("w:sz"))
    if sz is not None:
        f.append("sz=" + (sz.get(q("w:val")) or ""))
    col = rpr.find(q("w:color"))
    if col is not None:
        c = col.get(q("w:val"))
        if c and c not in ("auto", "000000"):
            f.append("c=" + c)
    hl = rpr.find(q("w:highlight"))
    if hl is not None:
        f.append("hl=" + (hl.get(q("w:val")) or ""))
    return ",".join(f) if f else "plano"


def ubicacion_p(p, idx_tbl: dict):
    """Devuelve (tipo, detalle)."""
    anc = p.getparent()
    tbl = None
    txbx = False
    while anc is not None:
        if anc.tag == q("w:txbxContent"):
            txbx = True
        if anc.tag == q("w:tbl") and tbl is None:
            tbl = anc
        anc = anc.getparent()
    if txbx:
        return "cuadro-texto", ""
    if tbl is not None:
        ti = idx_tbl.get(id(tbl), -1) + 1
        tr = p.getparent()
        while tr is not None and tr.tag != q("w:tr"):
            tr = tr.getparent()
        ri = ci = 0
        if tr is not None:
            filas_tbl = tbl.findall(q("w:tr"))
            if tr in filas_tbl:
                ri = filas_tbl.index(tr) + 1
            c = p.getparent()
            while c is not None and c.tag != q("w:tc"):
                c = c.getparent()
            if c is not None:
                celdas_tr = tr.findall(q("w:tc"))
                if c in celdas_tr:
                    ci = celdas_tr.index(c) + 1
        return "tabla", f"T{ti:02d}R{ri:02d}C{ci:02d}"
    return "cuerpo", ""


# --------------------------------------------------------------------------
# lectura del paquete
# --------------------------------------------------------------------------

def leer_partes(z: zipfile.ZipFile) -> "OrderedDict[str, bytes]":
    partes = OrderedDict()
    for info in z.infolist():
        if info.is_dir():
            continue
        partes[info.filename] = z.read(info.filename)
    return partes


def parse(partes: dict, nombre: str):
    if nombre not in partes:
        return None
    try:
        return etree.fromstring(partes[nombre])
    except Exception:
        return None


def rels(partes: dict, nombre: str) -> dict:
    d = str(Path(nombre).parent / "_rels" / (Path(nombre).name + ".rels"))
    d = d.replace("\\", "/").lstrip("./")
    root = parse(partes, d)
    out = {}
    if root is None:
        return out
    for rel in root:
        out[rel.get("Id")] = (rel.get("Type", "").rsplit("/", 1)[-1],
                              rel.get("Target", ""),
                              rel.get("TargetMode", ""))
    return out


def resolver(base: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    p = Path(base).parent
    return str((p / target)).replace("\\", "/")


# --------------------------------------------------------------------------
# principales
# --------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    if not argv:
        print("uso: python inspeccion.py ARCHIVO.docx [--max-texto N]")
        return 2
    ruta = Path(argv[0])
    max_texto = 90
    if "--max-texto" in argv:
        max_texto = int(argv[argv.index("--max-texto") + 1])
    if not ruta.exists():
        print(f"no existe: {ruta}")
        return 2

    R = Rep()
    z = zipfile.ZipFile(ruta)
    partes = leer_partes(z)
    doc = parse(partes, "word/document.xml")
    if doc is None:
        print("no encuentro word/document.xml")
        return 1

    doc_rels = rels(partes, "word/document.xml")
    styles = parse(partes, "word/styles.xml")
    settings = parse(partes, "word/settings.xml")
    core = parse(partes, "docProps/core.xml")

    # ---------------- cabecera del reporte ----------------
    R("REPORTE DE INSPECCION (Paso 0) - solo lectura, el archivo NO se modifica")
    R(f"Archivo : {ruta.name}")
    R(f"Tamano  : {ruta.stat().st_size:,} bytes")
    R(f"SHA256  : {sha256(ruta)}")
    R(f"Python  : {sys.version.split()[0]}")

    # ---------------- 1. paquete ----------------
    R.titulo("1", "PAQUETE OOXML")
    R(f"Partes totales: {len(partes)}")
    R("")
    R(f"{'bytes':>10}  parte")
    for n, b in sorted(partes.items(), key=lambda kv: -len(kv[1])):
        R(f"{len(b):>10,}  {n}")

    # media
    media = {n: b for n, b in partes.items() if n.startswith("word/media/")}
    R("")
    R(f"Archivos en word/media/: {len(media)}")
    if media:
        R(f"{'bytes':>10}  {'tipo':<5} {'px':>11}  archivo")
        for n, b in media.items():
            px = px_de_imagen(b)
            pxs = f"{px[0]}x{px[1]}" if px else "-"
            R(f"{len(b):>10,}  {tipo_imagen(b, n):<5} {pxs:>11}  {n}")
    fuentes = [n for n in partes if n.startswith("word/fonts/")]
    if fuentes:
        R("")
        R(f"Fuentes incrustadas: {len(fuentes)} -> {', '.join(fuentes)}")

    # ---------------- 2. conteo global ----------------
    R.titulo("2", "CONTEO GLOBAL (document.xml)")
    pares_doc = list(doc.iter(q("w:p")))
    runs_doc = list(doc.iter(q("w:r")))
    tablas = list(doc.iter(q("w:tbl")))
    idx_tbl = {id(t): i for i, t in enumerate(tablas)}

    def n(tag: str, root=doc) -> int:
        return len(root.findall(".//" + q(tag)))

    dibujos = n("w:drawing")
    pict = n("w:pict")
    objetos = n("w:object")
    altcontent = n("mc:AlternateContent")
    txbx = n("w:txbxContent")

    celdas = len(list(doc.iter(q("w:tc"))))
    filas = len(list(doc.iter(q("w:tr"))))

    R(f"parrafos (w:p, incluye tablas y cuadros de texto) : {len(pares_doc)}")
    R(f"runs (w:r)                                         : {len(runs_doc)}")
    R(f"tablas (w:tbl)                                     : {len(tablas)}")
    R(f"  filas (w:tr, todas)                              : {filas}")
    R(f"  celdas (w:tc, todas)                             : {celdas}")
    R(f"imagenes: w:drawing={dibujos}  w:pict={pict}  w:object={objetos}")
    R(f"mc:AlternateContent (compatibilidad)               : {altcontent}")
    R(f"cuadros de texto (w:txbxContent)                   : {txbx}")
    R(f"hipervinculos (w:hyperlink)                        : {n('w:hyperlink')}")
    R(f"campos complejos (w:fldChar begin)                 : {len([f for f in doc.iter(q('w:fldChar')) if f.get(q('w:fldCharType')) == 'begin'])}")
    R(f"campos simples (w:fldSimple)                       : {n('w:fldSimple')}")
    R(f"saltos de linea (w:br)                             : {n('w:br')}")
    R(f"  de los cuales de pagina                          : {len([b for b in doc.iter(q('w:br')) if b.get(q('w:type')) == 'page'])}")
    R(f"tabulaciones (w:tab)                               : {n('w:tab')}")
    R(f"marcadores (w:bookmarkStart)                       : {n('w:bookmarkStart')}")
    R(f"notas al pie / finales (ref. en cuerpo)            : {n('w:footnoteReference')} / {n('w:endnoteReference')}")
    R(f"comentarios (ref. en cuerpo)                       : {n('w:commentReference')}")
    R(f"ecuaciones (m:oMath)                               : {n('m:oMath')}")
    R(f"controles de contenido (w:sdt)                     : {n('w:sdt')}")
    R(f"control de cambios: ins={n('w:ins')} del={n('w:del')} moveFrom={n('w:moveFrom')} moveTo={n('w:moveTo')}")
    R(f"  cambios de formato: rPrChange={n('w:rPrChange')} pPrChange={n('w:pPrChange')} sectPrChange={n('w:sectPrChange')}")
    R(f"texto de campo (w:instrText)                       : {n('w:instrText')}  <- NUNCA se traduce")
    R(f"runs dentro de resultados de campo                 : {n('w:fldChar')} marcas fldChar")
    R(f"parrafos con numeracion (w:numPr)                  : {n('w:numPr')}")

    # ---------------- 3. secciones ----------------
    R.titulo("3", "SECCIONES (w:sectPr)")
    sects = list(doc.iter(q("w:sectPr")))
    R(f"Total de secciones: {len(sects)}")
    for i, sp in enumerate(sects, 1):
        pg = sp.find(q("w:pgSz"))
        mar = sp.find(q("w:pgMar"))
        cols = sp.find(q("w:cols"))
        tipo = sp.find(q("w:type"))
        R("")
        R(f"  Seccion {i}:")
        if pg is not None:
            wv = int(pg.get(q("w:w")) or 0)
            hv = int(pg.get(q("w:h")) or 0)
            orient = pg.get(q("w:orient")) or "portrait"
            R(f"    pagina: {cm(wv)} x {cm(hv)} ({orient})")
        if mar is not None:
            R("    margenes: arriba={} abajo={} izq={} der={} encabezado={} pie={} gutter={}".format(
                cm(int(mar.get(q("w:top")) or 0)), cm(int(mar.get(q("w:bottom")) or 0)),
                cm(int(mar.get(q("w:left")) or 0)), cm(int(mar.get(q("w:right")) or 0)),
                cm(int(mar.get(q("w:header")) or 0)), cm(int(mar.get(q("w:footer")) or 0)),
                cm(int(mar.get(q("w:gutter")) or 0))))
        if cols is not None:
            R(f"    columnas: num={cols.get(q('w:num')) or '1'} espacio={cols.get(q('w:space')) or '0'} iguales={cols.get(q('w:equalWidth'))}")
        if tipo is not None:
            R(f"    tipo de salto: {tipo.get(q('w:val'))}")
        R(f"    titlePg (portada distinta): {'si' if sp.find(q('w:titlePg')) is not None else 'no'}")
        refs = []
        for t in ("headerReference", "footerReference"):
            for e in sp.findall(q("w:" + t)):
                rid = e.get(q("r:id"))
                destino = doc_rels.get(rid, ("?", "?", ""))[1]
                refs.append(f"{t}[{(e.get(q('w:type')) or 'default')}]->{destino}")
        R("    refs: " + (", ".join(refs) if refs else "(ninguna, hereda de la seccion anterior)"))

    # ---------------- 4. estilos ----------------
    R.titulo("4", "ESTILOS")
    defs = {}
    if styles is not None:
        for st in styles.findall(q("w:style")):
            sid = st.get(q("w:styleId"))
            nm = st.find(q("w:name"))
            defs[sid] = {
                "nombre": nm.get(q("w:val")) if nm is not None else "",
                "tipo": st.get(q("w:type")),
                "basado": (st.find(q("w:basedOn")).get(q("w:val"))
                           if st.find(q("w:basedOn")) is not None else ""),
                "por_defecto": st.get(q("w:default")) == "1",
                "oculto": st.get(q("w:semiHidden")) == "1",
            }
    R(f"Estilos definidos en styles.xml: {len(defs)}")
    st_def = next((sid for sid, d in defs.items()
                   if d["tipo"] == "paragraph" and d["por_defecto"]), None)
    R(f"Estilo de parrafo por defecto: {st_def!r} -> {defs.get(st_def, {}).get('nombre', '?')}")

    uso_par = Counter()
    uso_run = Counter()
    for p in pares_doc:
        ps = p.find(q("w:pPr") + "/" + q("w:pStyle"))
        if ps is not None:
            uso_par[ps.get(q("w:val"))] += 1
        else:
            uso_par[f"({st_def}: por defecto)"] += 1
    for r in runs_doc:
        rs = r.find(q("w:rPr") + "/" + q("w:rStyle"))
        if rs is not None:
            uso_run[rs.get(q("w:val"))] += 1

    R("")
    R("Estilos de PARRAFO usados (documento):")
    for sid, c in uso_par.most_common():
        info = defs.get(sid, {})
        R(f"  {c:>5}  {sid:<26} nombre={info.get('nombre', '?'):<22} tipo={info.get('tipo', '?')}")
    if uso_run:
        R("")
        R("Estilos de RUN usados:")
        for sid, c in uso_run.most_common():
            R(f"  {c:>5}  {sid:<28} nombre={defs.get(sid, {}).get('nombre', '?')}")

    firmas_glob = Counter()
    for r in runs_doc:
        firmas_glob[firma_run(r)] += 1
    R("")
    R(f"Formatos de run distintos: {len(firmas_glob)}")
    for f, c in firmas_glob.most_common(25):
        R(f"  {c:>5}  {f}")

    # runs con tachado (puede ser texto tachado real, no control de cambios)
    tachados = []
    for r in runs_doc:
        rpr = r.find(q("w:rPr"))
        if rpr is not None and val_on(rpr.find(q("w:strike"))):
            tachados.append("".join(t.text or "" for t in r.iter(q("w:t"))))
    if tachados:
        R("")
        R(f"AVISO: {len(tachados)} runs con tachado real (w:strike), no es control de cambios.")
        for t in tachados[:14]:
            if t.strip():
                R(f"    {t[:70]!r}")

    # ---------------- 5. recorrido en orden ----------------
    R.titulo("5", "RECORRIDO EN ORDEN DE DOCUMENTO")
    R("  (ID / ubicacion / estilo / runs / formato / banderas / texto truncado)")
    R("  banderas: IMG imagen, HL hipervinculo, FLD campo, BM marcador,")
    R("            INS/DEL control de cambios, TXB cuadro de texto, NUM numeracion")
    estilo_titulo_ids = {sid for sid, d in defs.items()
                         if d["tipo"] == "paragraph" and
                         (d["nombre"].lower().startswith("heading") or
                          d["nombre"].lower().startswith("titulo") or
                          d["nombre"].lower().startswith("title"))}

    contador = Counter()
    registros = []  # (id, tipo_ubic, estilo, texto, len, flags, firmas, jc, num)
    for p in pares_doc:
        tipo, det = ubicacion_p(p, idx_tbl)
        if tipo == "cuerpo":
            contador["P"] += 1
            pid = f"P{contador['P']:04d}"
        elif tipo == "tabla":
            pid = det
        else:
            contador["TB"] += 1
            pid = f"TB{contador['TB']:02d}"

        ppr = p.find(q("w:pPr"))
        ps = ppr.find(q("w:pStyle")) if ppr is not None else None
        estilo = ps.get(q("w:val")) if ps is not None else "-"
        jc = ppr.find(q("w:jc")) if ppr is not None else None
        jcs = jc.get(q("w:val")) if jc is not None else "-"
        num = ppr.find(q("w:numPr")) if ppr is not None else None
        nums = ""
        if num is not None:
            il = num.find(q("w:ilvl"))
            ni = num.find(q("w:numId"))
            nums = f"{ni.get(q('w:val')) if ni is not None else '?'}/{il.get(q('w:val')) if il is not None else '0'}"

        rs = own_runs(p)
        firms = []
        for r in rs:
            f = firma_run(r)
            firms.append(f if f in ("B", "I", "U", "SUP", "SUB", "plano", "sin-rPr") else "F*")
        txt, ln = texto_de_par(p, max_texto)

        flags = []
        for t in rs:
            el = t
            if el.find(q("w:drawing")) is not None or el.find(q("w:pict")) is not None:
                flags.append("IMG")
            if t.find(q("w:txbxContent")) is not None:
                flags.append("TXB")
        if p.find(q("w:hyperlink")) is not None:
            flags.append("HL")
        if p.find(q("w:fldSimple")) is not None or p.find(".//" + q("w:fldChar")) is not None:
            flags.append("FLD")
        if p.find(".//" + q("w:bookmarkStart")) is not None:
            flags.append("BM")
        if p.find(".//" + q("w:ins")) is not None:
            flags.append("INS")
        if p.find(".//" + q("w:del")) is not None:
            flags.append("DEL")
        if nums:
            flags.append("NUM")
        nb = " ".join(dict.fromkeys(flags))

        registros.append((pid, tipo, det, estilo, txt, ln, nb, ",".join(firms), jcs, nums))
        R(f"{pid:<10} {tipo:<13} est={estilo:<14} r={len(rs):<3} jc={jcs:<5} {nb:<14} | {txt!r} ({ln})")

    # ---------------- 6. tablas ----------------
    R.titulo("6", "TABLAS")
    for i, tb in enumerate(tablas, 1):
        tblpr = tb.find(q("w:tblPr"))
        grid = tb.find(q("w:tblGrid"))
        cols = grid.findall(q("w:gridCol")) if grid is not None else []
        anchos = [cm(int(c.get(q("w:w")) or 0)) for c in cols]
        trs = [tr for tr in tb.iter(q("w:tr"))
               if tr.getparent() is tb or tr.getparent().tag == q("w:tbl")]
        trs = [tr for tr in tb.findall(q("w:tr"))]
        celdas_tot = sum(len([tc for tc in tr.findall(q("w:tc"))]) for tr in trs)
        span = len([g for g in tb.iter(q("w:gridSpan"))])
        vmerge = len([g for g in tb.iter(q("w:vMerge"))])
        anid = len([t2 for t2 in tb.iter(q("w:tbl"))]) - 1
        within = sum(len(c.findall(q("w:p"))) for c in tb.iter(q("w:tc")))
        R("")
        R(f"  Tabla {i}: filas={len(trs)} columnas(grid)={len(cols)} celdas={celdas_tot} "
          f"gridSpan={span} vMerge={vmerge} tablas-anidadas={anid} parrafos={within}")
        R(f"    anchos: {' | '.join(anchos)}")
        if tblpr is not None:
            est = tblpr.find(q("w:tblStyle"))
            bor = tblpr.find(q("w:tblBorders"))
            R(f"    estilo={est.get(q('w:val')) if est is not None else '-'} "
              f"bordes={'si' if bor is not None else 'no'} "
              f"ancho={tblpr.find(q('w:tblW')).get(q('w:w')) if tblpr.find(q('w:tblW')) is not None else '-'}")
        # leyenda: parrafo no vacio anterior y posterior
        def vecino(el, paso):
            e = el.getprevious() if paso < 0 else el.getnext()
            while e is not None and e.tag != q("w:p"):
                e = e.getprevious() if paso < 0 else e.getnext()
            return e

        prev = vecino(tb, -1)
        while prev is not None and not texto_de_par(prev, 5)[0]:
            prev = vecino(prev, -1)
        nxt = vecino(tb, +1)
        while nxt is not None and not texto_de_par(nxt, 5)[0]:
            nxt = vecino(nxt, +1)
        cap_prev = texto_de_par(prev, 70)[0] if prev is not None else ""
        cap_next = texto_de_par(nxt, 70)[0] if nxt is not None else ""
        R(f"    leyenda anterior: {cap_prev!r}")
        R(f"    leyenda posterior: {cap_next!r}")
        if re.match(r"^\s*(Tabla|Table|Cuadro)", cap_next, re.I):
            R("    -> leyenda DEBAJO de la tabla (normal en este documento)")
        elif re.match(r"^\s*(Tabla|Table|Cuadro)", cap_prev, re.I):
            R("    -> leyenda ENCIMA de la tabla")
        else:
            R("    AVISO: no encuentro leyenda 'Tabla/Table/Cuadro' junto a la tabla")

    # ---------------- 7. imagenes ----------------
    R.titulo("7", "IMAGENES Y DIBUJOS")
    total_img = 0
    # recorrer otra vez en orden para asociar dibujos a su parrafo
    contador2 = Counter()
    for p in pares_doc:
        tipo, det = ubicacion_p(p, idx_tbl)
        if tipo == "cuerpo":
            contador2["P"] += 1
            pid = f"P{contador2['P']:04d}"
        elif tipo == "tabla":
            pid = det
        else:
            contador2["TB"] += 1
            pid = f"TB{contador2['TB']:02d}"
        for d in p.iter(q("w:drawing")):
            total_img += 1
            ext = d.find(".//" + q("wp:extent"))
            dim = f"{emu_cm(int(ext.get('cx')))} x {emu_cm(int(ext.get('cy')))}" if ext is not None else "?"
            docpr = d.find(".//" + q("wp:docPr"))
            nombre = docpr.get("name") if docpr is not None else ""
            descr = docpr.get("descr") if docpr is not None else ""
            inline = d.find(q("wp:inline")) is not None
            blip = d.find(".//" + q("a:blip"))
            rid = blip.get(q("r:embed")) if blip is not None else None
            destino = doc_rels.get(rid, ("", "?", ""))[1] if rid else "?"
            parte = resolver("word/document.xml", destino) if destino != "?" else "?"
            tam = len(partes.get(parte, b"")) if parte in partes else 0
            px = px_de_imagen(partes.get(parte, b"")) if parte in partes else None
            pxs = f"{px[0]}x{px[1]}px" if px else ""
            R(f"  {pid:<10} {'inline' if inline else 'anchor'} {dim:<16} {pxs:<12} {parte} ({tam:,} B) "
              f"name={nombre!r} alt={descr!r}")
        for d in p.iter(q("w:pict")):
            total_img += 1
            imd = d.find(".//" + q("v:imagedata"))
            rid = imd.get(q("r:id")) if imd is not None else None
            destino = doc_rels.get(rid, ("", "?", ""))[1] if rid else "?"
            R(f"  {pid:<10} VML/pict      -> {destino}")
    R("")
    R(f"Total de imagenes/dibujos: {total_img}")

    # ---------------- 8. hipervinculos ----------------
    R.titulo("8", "HIPERVINCULOS")
    for hl in doc.iter(q("w:hyperlink")):
        rid = hl.get(q("r:id"))
        anchor = hl.get(q("w:anchor"))
        destino = doc_rels.get(rid, ("", "?", ""))[1] if rid else f"#{anchor}"
        txt = "".join(t.text or "" for t in hl.iter(q("w:t")))
        R(f"  {destino:<60} texto={txt!r}")
    if not list(doc.iter(q("w:hyperlink"))):
        R("  (ninguno)")
    mailtos = [t for (tip, t, m) in doc_rels.values()
               if tip == "hyperlink" and t.lower().startswith("mailto:")]
    if mailtos:
        R(f"  destinos mailto (no se traducen): {len(mailtos)}")
        for t in mailtos:
            R(f"    {t}")

    # ---------------- 9. campos ----------------
    R.titulo("9", "CAMPOS (se conservan tal cual: w:instrText NUNCA se traduce)")
    instr = Counter()
    for it in doc.iter(q("w:instrText")):
        instr[(it.text or "").strip()] += 1
    for fs in doc.iter(q("w:fldSimple")):
        instr[fs.get(q("w:instr")) or "?"] += 1
    if not instr:
        R("  (ninguno)")
    grupos = Counter()
    for k, c in instr.items():
        tok = " ".join(k.split()[:2])[:48]
        grupos[tok] += c
    for k, c in grupos.most_common():
        R(f"  {c:>3}x  {k}")
    zotero = sum(c for k, c in instr.items() if "ZOTERO" in k)
    if zotero:
        R("")
        R(f"  IMPORTANTE: {zotero} campos son de Zotero (citaciones y bibliografia).")
        R("  Su resultado visible ([1]...[21] y la lista de referencias) es contenido de campo:")
        R("  NO se traduce y NO se debe tocar w:instrText ni el XML de resultados.")
    con_campos = [p for p in pares_doc if p.find(".//" + q("w:fldChar")) is not None]
    R(f"  parrafos que contienen campos: {len(con_campos)}")

    # ---------------- 10. cabeceras y pies ----------------
    R.titulo("10", "CABECERAS Y PIES")
    for nombre in [n for n in partes if re.match(r"word/(header|footer)\d*\.xml$", n)]:
        root = parse(partes, nombre)
        if root is None:
            continue
        ps = list(root.iter(q("w:p")))
        texto = " / ".join(t for t in (texto_de_par(p, 110)[0] for p in ps) if t)
        R(f"  {nombre}: parrafos={len(ps)} runs={len(list(root.iter(q('w:r'))))} "
          f"img={len(list(root.iter(q('w:drawing'))))} "
          f"tablas={len(list(root.iter(q('w:tbl'))))}")
        R(f"      texto: {texto!r}")

    # ---------------- 11. notas, comentarios, cambios ----------------
    R.titulo("11", "NOTAS AL PIE, COMENTARIOS Y CONTROL DE CAMBIOS")
    for nombre, etiqueta in (("word/footnotes.xml", "notas al pie"),
                             ("word/endnotes.xml", "notas finales"),
                             ("word/comments.xml", "comentarios")):
        root = parse(partes, nombre)
        if root is None:
            R(f"  {etiqueta}: (no existe la parte {nombre})")
            continue
        tp = list(root.iter(q("w:p")))
        R(f"  {etiqueta}: {nombre} parrafos={len(tp)}")
    ins = [i for i in doc.iter(q("w:ins"))]
    dele = [d for d in doc.iter(q("w:del"))]
    for i in ins[:5]:
        R(f"    INS autor={i.get(q('w:author'))!r} fecha={i.get(q('w:date'))} "
          f"texto={''.join(t.text or '' for t in i.iter(q('w:t')))[:60]!r}")
    for d in dele[:5]:
        R(f"    DEL autor={d.get(q('w:author'))!r} fecha={d.get(q('w:date'))} "
          f"texto={''.join(t.text or '' for t in d.iter(q('w:delText')))[:60]!r}")

    # ---------------- 12. idioma ----------------
    R.titulo("12", "IDIOMA DEL TEXTO (w:lang)")
    idiomas = Counter()
    for r in doc.iter(q("w:r")):
        lg = r.find(q("w:rPr") + "/" + q("w:lang"))
        if lg is not None:
            idiomas[f"val={lg.get(q('w:val'))} este={lg.get(q('w:eastAsia'))} bidi={lg.get(q('w:bidi'))}"] += 1
        else:
            idiomas["(sin w:lang)"] += 1
    for k, c in idiomas.most_common():
        R(f"  {c:>5}  {k}")
    if settings is not None:
        th = settings.find(q("w:themeFontLang"))
        if th is not None:
            R(f"  themeFontLang: {dict(th.attrib)}")

    # ---------------- 13. riesgos de traduccion ----------------
    R.titulo("13", "RIESGOS DE TRADUCCION (patrones detectados)")
    patrones = {
        "correo": r"[\w.+-]+@[\w-]+\.[\w.]{2,}",
        "url": r"https?://\S+",
        "doi": r"10\.\d{4,9}/[^\s,;)]+",
        "cita [n]": r"\[\d+(?:\s*[-\u2013,]\s*\d+)*\]",
        "identificador_con_guion_bajo": r"\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b",
        "codigo_tipo_RF": r"\b[A-Z]{2,4}-\d{2,}\b",
        "porcentaje": r"\d+(?:[.,]\d+)?\s?%",
        "sigla_mayusculas": r"\b[A-Z]{2,6}\b",
        "ISBN_ISSN": r"(?:ISSN|ISBN)\s*[\d\-Xx/]+",
    }
    for etiqueta, pat in patrones.items():
        encontrados = Counter()
        for p in pares_doc:
            txt, _ = texto_de_par(p, 10**6)
            for m in re.findall(pat, txt):
                encontrados[m] += 1
        R("")
        R(f"  {etiqueta}: {sum(encontrados.values())} ocurrencias, {len(encontrados)} distintas")
        if encontrados:
            muestra = ", ".join(f"{k}({v})" for k, v in encontrados.most_common(12))
            R(f"      {muestra}")

    # ---------------- 14. candidatos a titulo y zonas ----------------
    R.titulo("14", "TITULOS DE SECCION DETECTADOS (heuristica)")
    PALABRAS = {"abstract", "resumen", "introduccion", "introducción", "referencias",
                "references", "conclusiones", "conclusions", "resultados", "results",
                "discusion", "discusión", "metodologia", "metodología", "materials",
                "agradecimientos", "acknowledgments", "palabras clave", "keywords",
                "objetivo", "objective", "trabajos relacionados"}
    titulos = []
    for pid, tipo, det, estilo, txt, ln, nb, firms, jc, nums in registros:
        if tipo != "cuerpo" or not txt:
            continue
        t = txt.rstrip()
        es = False
        if estilo in estilo_titulo_ids or estilo.lower().startswith(("heading", "titulo", "title")):
            es = True
        elif ln < 120 and not t.endswith(".") and re.match(r"^\d+(\.\d+)*\.?\s+\S", t):
            es = True
        elif t.rstrip(":").lower() in PALABRAS:
            es = True
        if es:
            titulos.append((pid, estilo, txt))
            R(f"  {pid:<10} est={estilo:<16} {txt!r}")
    R(f"  total: {len(titulos)}")

    # referencia: zona de bibliografia
    R("")
    R("  Zona protegible (bibliografia):")
    idx_ref = None
    for i, (pid, tipo, det, estilo, txt, ln, nb, firms, jc, nums) in enumerate(registros):
        if tipo == "cuerpo" and re.fullmatch(r"(?i)\s*(\d+\.?\s*)?(referencias|references|bibliograf[ií]a)\s*:?",
                                             txt or ""):
            idx_ref = i
    if idx_ref is None:
        R("    no se encontro un titulo de Referencias por heuristica de texto")
    else:
        pid, *_ = registros[idx_ref]
        resto = [r for r in registros[idx_ref + 1:] if r[1] == "cuerpo" and r[4]]
        R(f"    titulo en {pid}; parrafos posteriores={len(resto)} (se copian sin traducir)")

    # ---------------- 15. sintesis ----------------
    R.titulo("15", "SINTESIS PARA DECIDIR")
    cuerpo = [r for r in registros if r[1] == "cuerpo"]
    chars = sum(r[5] for r in cuerpo)
    R(f"  parrafos de cuerpo: {len(cuerpo)} de {len(registros)} totales")
    R(f"  caracteres en parrafos de cuerpo (aprox): {chars:,}")
    R(f"  parrafos con mas de un formato de run: {sum(1 for r in registros if len(set(r[7].split(','))) > 1)}")
    R(f"  parrafos vacios: {sum(1 for r in registros if r[5] == 0)}")
    R("")
    R("  AVISOS:")
    if zotero_total := sum(c for k, c in instr.items() if "ZOTERO" in k):
        R(f"  - {zotero_total} campos Zotero: las citas [n] del cuerpo y los 21 registros de la")
        R("    bibliografia son RESULTADO de campo. Traducirlos los rompe: se protegen con marcador.")
    if not any(r.find(q("w:rPr") + "/" + q("w:lang")) is not None for r in runs_doc):
        R("  - ningun run tiene w:lang: si se agrega el atributo se altera el esqueleto XML.")
        R("    Decision: NO agregar w:lang (el idioma real esta en styles/themeFontLang).")
    cols2 = [i for i, sp in enumerate(sects, 1)
             if sp.find(q("w:cols")) is not None and (sp.find(q("w:cols")).get(q("w:num")) or "1") != "1"]
    if cols2:
        R(f"  - secciones a 2 columnas: {cols2}")
        R("    ES->EN suele ACORTAR el texto (~15-25 %): sobra espacio y puede reflowear")
        R("    el contenido; EN->ES lo alarga en la misma proporcion. No se ajustan")
        R("    fuentes ni margenes; las diferencias van al informe.")
    if tachados:
        R(f"  - {len(tachados)} runs con tachado en la linea de autores (P0003/P0004): es tachado real,")
        R("    no control de cambios; hay que conservarlo.")
    if txbx:
        R(f"  - {txbx} cuadros de texto: sus parrafos NO estan en el flujo normal del cuerpo")
    if n("w:sdt"):
        R(f"  - {n('w:sdt')} controles de contenido (w:sdt): hay que traducir el w:t interno respetando el sdt")
    if n("m:oMath"):
        R(f"  - {n('m:oMath')} ecuaciones: se conservan tal cual")
    if ins or dele:
        R(f"  - control de cambios activo (ins={len(ins)}, del={len(dele)}): decidir si se aceptan antes de traducir")
    if n("w:commentReference"):
        R(f"  - {n('w:commentReference')} comentarios: no se traducen")
    if n("w:footnoteReference"):
        R(f"  - {n('w:footnoteReference')} notas al pie: traducir su texto por separado")
    elif "word/footnotes.xml" in partes:
        R("  - footnotes.xml existe pero NO hay referencias en el cuerpo: solo separadores.")
    if n("w:object"):
        R(f"  - {n('w:object')} objetos OLE incrustados")
    huerfanos = [p for p in pares_doc if not own_runs(p)]
    if huerfanos:
        R(f"  - {len(huerfanos)} parrafos sin runs (vacios o solo con marcadores): no se tocan")
    media_px = {n2: px_de_imagen(b) for n2, b in media.items()}
    grande = [n2 for n2, px in media_px.items() if px and px[0] * px[1] > 2_000_000]
    if grande:
        R(f"  - imagenes de muy alta resolucion (posible reduccion sin perdida visual): {', '.join(grande)}")
    R("  - Fig. 3 y Tabla 2 contienen identificadores en espanol (nivel_glucosa, ...):")
    R("    se traduce la descripcion, NUNCA el identificador.")

    # core props
    if core is not None:
        R("")
        R("  Propiedades del documento (docProps/core.xml):")
        for tag in ("dc:title", "dc:creator", "cp:lastModifiedBy", "dc:language",
                    "dcterms:created", "dcterms:modified"):
            el = core.find(q(tag))
            if el is not None and el.text:
                R(f"    {tag}: {el.text.strip()!r}")

    salida = R.texto()
    destino = ruta.with_suffix(ruta.suffix + ".inspeccion.txt")
    destino.write_text(salida, encoding="utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(salida)
    print(f"\n[reporte guardado en {destino.name}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
