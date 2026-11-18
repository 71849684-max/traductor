import pytest

from traductor.docx_io import Documento


def test_partes_intactas_al_guardar(docx_real, tmp_path):
    doc = Documento(docx_real)
    antes = dict(doc.partes)
    salida = doc.guardar(tmp_path / "copia.docx")
    with __import__("zipfile").ZipFile(salida) as z:
        despues = {i.filename: z.read(i.filename) for i in z.infolist()}
    assert set(antes) == set(despues)
    for parte, datos in antes.items():
        assert datos == despues[parte], parte


def test_solo_se_reserializa_lo_marcado(docx_real, tmp_path):
    doc = Documento(docx_real)
    doc.xml("word/document.xml").set(
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}rsidR",
        "00FFFFFF")
    doc.marcar_sucio("word/document.xml")
    salida = doc.guardar(tmp_path / "copia.docx")
    import zipfile
    with zipfile.ZipFile(salida) as z:
        assert z.read("word/styles.xml") == doc.partes["word/styles.xml"]
        assert z.read("word/numbering.xml") == doc.partes["word/numbering.xml"]
        assert b"00FFFFFF" in z.read("word/document.xml")


def test_no_sobrescribe_el_original(docx_real):
    doc = Documento(docx_real)
    with pytest.raises(ValueError):
        doc.guardar(docx_real)


def test_ids_de_parrafo_estables(docx_real):
    doc = Documento(docx_real)
    a = [p.id for p in doc.parrafos()]
    b = [p.id for p in doc.parrafos()]
    assert a == b
    assert "P0001" in a
    assert "P0079" in a            # Referencias
    assert "T01R01C01" in a        # primera celda de la tabla 1
    assert "T04R07C04" in a        # ultima celda de la tabla 4
    assert any(i.startswith("H1") for i in a)
    assert any(i.startswith("F1") for i in a)
    assert len(a) == len(set(a))


def test_ubicacion_de_celdas(docx_real):
    doc = Documento(docx_real)
    idx = doc.indice()
    celda = idx["T02R02C01"]
    assert celda.contenedor == "tabla"
    assert (celda.tabla, celda.fila, celda.columna) == (2, 2, 1)
    assert celda.texto == "nivel_glucosa"


def test_relaciones_e_imagenes(docx_real):
    doc = Documento(docx_real)
    imgs = doc.imagenes()
    assert len(imgs) == 4
    assert all(dest.endswith((".png", ".jpg")) for _, dest in imgs)
