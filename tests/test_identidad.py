"""Prueba central: ida y vuelta SIN traducir sobre el documento completo.

Si el serializador/reconstructor no es fiel, esta prueba falla.
"""

import zipfile

from traductor import marcado, verificar_formato
from traductor.docx_io import Documento, q


def test_identidad_ida_y_vuelta_documento_completo(docx_real, tmp_path):
    doc = Documento(docx_real)
    original = {p: b for p, b in doc.partes.items()}
    textos_antes = {p.id: p.texto for p in doc.parrafos()}

    modificadas = set()
    for par in doc.parrafos():
        ser = marcado.serializar(par.elemento, par.id)
        marcado.aplicar(par.elemento, ser.texto, ser)
        if ser.piezas:
            modificadas.add(par.parte)

    for parte in modificadas:
        doc.marcar_sucio(parte)
    salida = doc.guardar(tmp_path / "identidad.docx")

    dif = verificar_formato.comparar(docx_real, salida)
    assert dif == [], "\n".join(dif)

    # las marcas del corrector se conservan intactas en la identidad
    assert sum(1 for _ in doc.xml("word/document.xml").iter(q("w:proofErr"))) == 100

    # el texto visible no cambio
    doc2 = Documento(salida)
    textos_despues = {p.id: p.texto for p in doc2.parrafos()}
    assert textos_despues == textos_antes

    # las partes que no son de texto siguen byte a byte
    with zipfile.ZipFile(salida) as z:
        for parte in ("word/styles.xml", "word/numbering.xml",
                      "word/settings.xml", "word/theme/theme1.xml",
                      "word/media/image1.png", "word/media/image4.jpg"):
            assert z.read(parte) == original[parte], parte


def test_identidad_por_parrafo(docx_real):
    """Cada parrafo, aislado, sobrevive a serializar -> reconstruir."""
    doc = Documento(docx_real)
    for par in doc.parrafos():
        ser = marcado.serializar(par.elemento, par.id)
        if not ser.piezas:
            continue
        esperado = verificar_formato.normalizar(par.elemento)
        from lxml import etree
        antes = etree.tostring(esperado, method="c14n")
        marcado.aplicar(par.elemento, ser.texto, ser)
        despues = etree.tostring(verificar_formato.normalizar(par.elemento),
                                 method="c14n")
        assert antes == despues, par.id


def _traduccion_simulada(ser) -> str:
    """Mismo marcado, texto distinto y de otra longitud."""
    trozos = []
    for it in marcado.parsear(ser.texto):
        if it[0] == "texto":
            trozos.append(f"<{it[1]}>TXT{len(it[2])}</{it[1]}>")
        else:
            trozos.append(f"\u27e6{it[1]}\u27e7")
    return "".join(trozos)


def test_traduccion_simulada_conserva_el_esqueleto(docx_real, tmp_path):
    doc = Documento(docx_real)
    antes = {p.id: p.texto for p in doc.parrafos()}
    traducidos = 0
    for par in doc.parrafos():
        ser = marcado.serializar(par.elemento, par.id)
        if not ser.piezas:
            continue
        if ser.protegido:
            marcado.aplicar(par.elemento, ser.texto, ser)
            continue
        marcado.aplicar(par.elemento, _traduccion_simulada(ser), ser)
        traducidos += 1
    assert traducidos > 60
    for parte in {p.parte for p in doc.parrafos()}:
        doc.marcar_sucio(parte)
    salida = doc.guardar(tmp_path / "traducido.docx")
    assert verificar_formato.comparar(docx_real, salida) == []
    despues = {p.id: p.texto for p in Documento(salida).parrafos()}
    assert despues["P0010"] != antes["P0010"]
    # los campos siguen intactos
    import zipfile
    with zipfile.ZipFile(salida) as z:
        assert z.read("word/document.xml").count(b"ZOTERO_ITEM") == 22
