"""Pruebas negativas: el verificador debe detectar alteraciones reales.

Sin estas pruebas, un verificador que siempre devuelve [] pasaria la prueba
de identidad. Aqui se altera a proposito una parte y se exige que falle.
"""

import zipfile

from lxml import etree

from traductor import verificar_formato


def _reescribir(origen, destino, cambios: dict[str, bytes]) -> None:
    with zipfile.ZipFile(origen) as zin, zipfile.ZipFile(destino, "w") as zout:
        for info in zin.infolist():
            datos = cambios.get(info.filename, zin.read(info.filename))
            zout.writestr(info.filename, datos)


def test_sin_cambios_da_ok(docx_real, tmp_path):
    destino = tmp_path / "igual.docx"
    with zipfile.ZipFile(docx_real) as zin, zipfile.ZipFile(destino, "w") as zout:
        for info in zin.infolist():
            zout.writestr(info.filename, zin.read(info.filename))
    assert verificar_formato.comparar(docx_real, destino) == []


def test_detecta_cambio_en_styles(docx_real, tmp_path):
    with zipfile.ZipFile(docx_real) as z:
        estilos = z.read("word/styles.xml")
    estilos = estilos.replace(b'w:styleId="Ttulo1"', b'w:styleId="TtuloX"')
    destino = tmp_path / "estilos.docx"
    _reescribir(docx_real, destino, {"word/styles.xml": estilos})
    dif = verificar_formato.comparar(docx_real, destino)
    assert any("styles.xml" in d for d in dif)


def test_detecta_cambio_en_media(docx_real, tmp_path):
    destino = tmp_path / "media.docx"
    _reescribir(docx_real, destino, {"word/media/image2.png": b"\x89PNG\r\n\x1a\nX"})
    dif = verificar_formato.comparar(docx_real, destino)
    assert any("image2.png" in d for d in dif)


def test_detecta_fila_de_tabla_eliminada(docx_real, tmp_path):
    with zipfile.ZipFile(docx_real) as z:
        doc = etree.fromstring(z.read("word/document.xml"))
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    tabla = next(doc.iter(W + "tbl"))
    fila = tabla.findall(W + "tr")[-1]
    tabla.remove(fila)
    destino = tmp_path / "tabla.docx"
    _reescribir(docx_real, destino,
                {"word/document.xml": etree.tostring(doc, xml_declaration=True,
                                                     encoding="UTF-8",
                                                     standalone=True)})
    dif = verificar_formato.comparar(docx_real, destino)
    assert any("filas" in d for d in dif)


def test_detecta_imagen_eliminada(docx_real, tmp_path):
    with zipfile.ZipFile(docx_real) as z:
        doc = etree.fromstring(z.read("word/document.xml"))
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    dibujo = next(doc.iter(W + "drawing"))
    dibujo.getparent().remove(dibujo)
    destino = tmp_path / "imagen.docx"
    _reescribir(docx_real, destino,
                {"word/document.xml": etree.tostring(doc, xml_declaration=True,
                                                     encoding="UTF-8",
                                                     standalone=True)})
    dif = verificar_formato.comparar(docx_real, destino)
    assert any("imagenes" in d for d in dif)


def test_detecta_run_sin_su_formato(docx_real, tmp_path):
    with zipfile.ZipFile(docx_real) as z:
        doc = etree.fromstring(z.read("word/document.xml"))
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    run = next(r for r in doc.iter(W + "r") if r.find(W + "rPr/" + W + "b") is not None)
    rpr = run.find(W + "rPr")
    run.remove(rpr)
    destino = tmp_path / "formato.docx"
    _reescribir(docx_real, destino,
                {"word/document.xml": etree.tostring(doc, xml_declaration=True,
                                                     encoding="UTF-8",
                                                     standalone=True)})
    dif = verificar_formato.comparar(docx_real, destino)
    assert any("esqueleto" in d for d in dif)


def test_cambiar_solo_el_texto_no_es_diferencia(docx_real, tmp_path):
    with zipfile.ZipFile(docx_real) as z:
        doc = etree.fromstring(z.read("word/document.xml"))
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    for t in doc.iter(W + "t"):
        if t.text:
            t.text = "texto traducido"
    destino = tmp_path / "texto.docx"
    _reescribir(docx_real, destino,
                {"word/document.xml": etree.tostring(doc, xml_declaration=True,
                                                     encoding="UTF-8",
                                                     standalone=True)})
    assert verificar_formato.comparar(docx_real, destino) == []
