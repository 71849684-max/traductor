import pytest
from lxml import etree

from traductor import marcado
from traductor.verificar_formato import normalizar

NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
      'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"')


def par(interior: str):
    return etree.fromstring(f"<w:p {NS}>{interior}</w:p>")


def run(texto: str, rpr: str = "") -> str:
    return f"<w:r><w:rPr>{rpr}</w:rPr><w:t>{texto}</w:t></w:r>"


def c14n(p) -> bytes:
    return etree.tostring(normalizar(p), method="c14n")


def ida_y_vuelta(p):
    ser = marcado.serializar(p)
    marcado.aplicar(p, ser.texto, ser)
    return ser


def test_etiquetas_semanticas():
    p = par(run("negrita", "<w:b/>") + run("cursiva", "<w:i/>")
            + run("sub", "<w:vertAlign w:val='subscript'/>"))
    ser = marcado.serializar(p, "P1")
    assert "<b>negrita</b>" in ser.texto
    assert "<i>cursiva</i>" in ser.texto
    assert "<sub>sub</sub>" in ser.texto
    assert ser.plano == "negritacursivasub"


def test_formato_no_semantico_usa_fn():
    p = par(run("rojo", "<w:color w:val='FF0000'/>") +
            run("negro"))
    ser = marcado.serializar(p, "P1")
    assert "<f1>rojo</f1>" in ser.texto
    assert ser.etiquetas["f1"]


def test_dos_negritas_de_tamano_distinto_no_comparten_etiqueta():
    p = par(run("a", "<w:b/><w:sz w:val='20'/>") +
            run("b", "<w:b/><w:sz w:val='28'/>"))
    ser = marcado.serializar(p, "P1")
    assert ser.texto.startswith("<b>a</b>")
    assert "<f" in ser.texto


def test_identidad_ida_y_vuelta_simple():
    p = par(run("Hola ", "") + run("mundo", "<w:b/>") + run("!", ""))
    antes = c14n(p)
    ser = ida_y_vuelta(p)
    assert ser.plano == "Hola mundo!"
    assert c14n(p) == antes


def test_identidad_con_tab_y_page_break():
    p = par('<w:r><w:rPr/><w:tab/><w:t>uno</w:t></w:r>'
            '<w:r><w:rPr/><w:lastRenderedPageBreak/><w:t>dos</w:t></w:r>')
    antes = c14n(p)
    ser = ida_y_vuelta(p)
    assert ser.plano == "unodos"
    assert c14n(p) == antes


def test_prooferr_conserva_posicion_entre_caracteres():
    p = par(run("abc") + '<w:proofErr w:type="spellStart"/>'
            + run("def") + '<w:proofErr w:type="spellEnd"/>')
    antes = c14n(p)
    ser = ida_y_vuelta(p)
    assert ser.plano == "abcdef"
    assert c14n(p) == antes
    hijos = [etree.QName(c).localname for c in p]
    assert hijos == ["r", "proofErr", "r", "proofErr"]


def test_campo_completo_es_un_marcador():
    p = par(run("Texto", "") +
            '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            '<w:r><w:instrText> ADDIN ZOTERO_ITEM CSL_CITATION </w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
            '<w:r><w:rPr/><w:t>[1]</w:t></w:r>'
            '<w:r><w:fldChar w:fldCharType="end"/></w:r>' +
            run(" final", ""))
    ser = marcado.serializar(p, "P1")
    # el resultado visible del campo queda dentro del marcador, no en el texto
    assert ser.plano == "Texto final"
    assert ser.texto.count("X01") == 1
    assert "[1]" in "".join(
        etree.tostring(n, encoding="unicode") for n in ser.marcadores["X01"]
    )
    antes = c14n(p)
    ida_y_vuelta(p)
    assert c14n(p) == antes
    assert "ZOTERO" in etree.tostring(p, encoding="unicode")


def test_hipervinculo_protegido():
    p = par(run("ver ", "") +
            '<w:hyperlink r:id="rId9"><w:r><w:rPr/><w:t>aquí</w:t></w:r></w:hyperlink>' +
            run(" más", ""))
    ser = marcado.serializar(p, "P1")
    assert ser.plano == "ver  más"
    assert len(ser.marcadores) == 1
    antes = c14n(p)
    ida_y_vuelta(p)
    assert c14n(p) == antes


def test_texto_con_angulares_y_ampersand():
    p = par(run("a &lt; b &amp; c &gt; d"))
    ser = marcado.serializar(p, "P1")
    assert ser.texto.count("&lt;") == 1
    assert ser.texto.count("&amp;") == 1
    marcado.aplicar(p, ser.texto, ser)
    assert p.find(".//" + marcado.q("w:t")).text == "a < b & c > d"


def test_validacion_detecta_marcador_perdido():
    p = par(run("antes", "") + '<w:r><w:drawing/></w:r>' + run("despues", ""))
    ser = marcado.serializar(p, "P1")
    problemas = marcado.validar("<f1>antes despues</f1>", ser)
    assert any("faltan marcadores" in x for x in problemas)
    with pytest.raises(marcado.MarcadoInvalido):
        marcado.aplicar(p, "<f1>antes despues</f1>", ser)


def test_validacion_detecta_marcador_en_otro_orden():
    p = par(run("a", "") + '<w:r><w:drawing/></w:r>' + run("b", "")
            + '<w:r><w:drawing/></w:r>' + run("c", ""))
    ser = marcado.serializar(p, "P1")
    problemas = marcado.validar("<f1>a</f1>\u27e6X02\u27e7<f1>b</f1>\u27e6X01\u27e7<f1>c</f1>", ser)
    assert problemas


def test_validacion_detecta_etiqueta_desconocida():
    p = par(run("hola", "<w:b/>"))
    ser = marcado.serializar(p, "P1")
    assert marcado.validar("<z>hola</z>", ser)


def test_respaldo_un_solo_run():
    p = par(run("uno ", "") + run("dos", "<w:b/>") + run(" tres", ""))
    ser = marcado.serializar(p, "P1")
    marcado.reconstruir_simple(p, "uno dos tres", ser)
    runs = p.findall(marcado.q("w:r"))
    assert len(runs) == 1
    assert "".join(t.text for t in p.iter(marcado.q("w:t"))) == "uno dos tres"
