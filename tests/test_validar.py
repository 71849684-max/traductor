from lxml import etree

from traductor import marcado, validar

NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')
GLOSARIO = {"pares": [("glucosa", "glucose"), ("diabetes tipo 1", "type 1 diabetes")],
            "no_traducir": ["Universidad Continental", "nivel_glucosa"]}


def par(interior: str):
    return etree.fromstring(f"<w:p {NS}>{interior}</w:p>")


def run(texto: str, rpr: str = "") -> str:
    return f"<w:r><w:rPr>{rpr}</w:rPr><w:t>{texto}</w:t></w:r>"


def serializado(interior: str):
    return marcado.serializar(par(interior), "P1")


def test_caso_correcto():
    ser = serializado(run("La glucosa del paciente se mantiene estable durante la noche."))
    ok = "<f1>The patient's glucose remains stable throughout the night.</f1>"
    res = validar.validar_bloque(ser.plano, ok, destino="en", ser=ser, glosario=GLOSARIO)
    assert res.ok, res.problemas
    assert res.detalles["idioma"] == "en"


def test_etiqueta_perdida():
    ser = serializado(run("El estudio muestra ", "") + run("datos", "<w:b/>") + run(".", ""))
    res = validar.validar_bloque(ser.plano, "The study shows data.", destino="en", ser=ser)
    assert any(p.startswith(("ETIQUETA", "MARCADO")) for p in res.problemas), res.problemas


def test_marcador_movido():
    ser = serializado(run("Antes ", "") + '<w:r><w:drawing/></w:r>' + run(" despues", "")
                      + '<w:r><w:drawing/></w:r>' + run(" final", ""))
    res = validar.validar_bloque(
        ser.plano, "Before \u27e6X02\u27e7 after \u27e6X01\u27e7 final",
        destino="en", ser=ser)
    assert any("MARCADOR_ORDEN" in p for p in res.problemas), res.problemas


def test_marcador_perdido():
    ser = serializado(run("Antes ", "") + '<w:r><w:drawing/></w:r>' + run(" despues", ""))
    res = validar.validar_bloque(ser.plano, "Before after", destino="en", ser=ser)
    assert any("MARCADOR_FALTANTE" in p for p in res.problemas), res.problemas


def test_cita_alterada():
    ser = serializado(run("El estudio previo [1] y el metanalisis [2] lo confirman."))
    res = validar.validar_bloque(
        ser.plano, "The previous study [1] and the meta-analysis [3] confirm it.",
        destino="en", ser=ser)
    assert any("CITA_ALTERADA" in p for p in res.problemas), res.problemas


def test_numero_alterado():
    ser = serializado(run("La cuenta regresiva es de 10 segundos exactos."))
    res = validar.validar_bloque(
        ser.plano, "The countdown lasts 12 exact seconds.", destino="en", ser=ser)
    assert any("NUMERO_ALTERADO" in p for p in res.problemas), res.problemas


def test_identificador_alterado():
    ser = serializado(run("La variable nivel_glucosa se actualiza cada minuto."))
    res = validar.validar_bloque(
        ser.plano, "The glucose_level variable is updated every minute.",
        destino="en", ser=ser, glosario=GLOSARIO)
    assert any("IDENTIFICADOR_ALTERADO" in p for p in res.problemas), res.problemas


def test_idioma_incorrecto():
    ser = serializado(run("El presente estudio describe la arquitectura de una aplicación "
                          "móvil orientada al monitoreo de glucosa y a la gestión segura de "
                          "la insulinoterapia en pacientes con diabetes tipo 1."))
    res = validar.validar_bloque(
        ser.plano,
        "<f1>El presente estudio describe la arquitectura de una aplicación móvil "
        "orientada al monitoreo de glucosa y a la gestión segura de la "
        "insulinoterapia en pacientes con diabetes tipo 1.</f1>",
        destino="en", ser=ser)
    assert any("IDIOMA" in p for p in res.problemas), res.problemas


def test_glosario_no_respetado():
    ser = serializado(run("La glucosa del paciente se mantiene estable durante la noche."))
    res = validar.validar_bloque(
        ser.plano, "The patient's sugar stays stable throughout the night.",
        destino="en", ser=ser, glosario=GLOSARIO)
    assert any("GLOSARIO" in p for p in res.problemas), res.problemas


def test_longitud_fuera_de_rango():
    ser = serializado(run("Texto suficientemente largo como para que el ratio se dispare "
                          "y la validacion lo detecte sin ninguna duda."))
    res = validar.validar_bloque(ser.plano, "Short.", destino="en", ser=ser)
    assert any("LONGITUD" in p for p in res.problemas), res.problemas


def test_validar_respuesta_ids():
    a = serializado(run("Primer parrafo de prueba con texto suficiente."))
    b = serializado(run("Segundo parrafo de prueba con texto suficiente."))
    esperados = {"P1": a, "P2": b}
    res = validar.validar_respuesta({"P1": "<f1>First test paragraph.</f1>",
                                     "P9": "<f1>x</f1>"},
                                    esperados, destino="en")
    assert "ID_AUSENTE" in res["P2"].problemas
    assert "ID_DESCONOCIDO" in res["P9"].problemas


def test_traduccion_vacia():
    ser = serializado(run("Un parrafo con contenido real y suficiente longitud."))
    res = validar.validar_bloque(ser.plano, "", destino="en", ser=ser)
    assert any("VACIA" in p or "VACIO" in p for p in res.problemas), res.problemas


def test_decadas_y_ordinales_no_alteran_numeros():
    assert validar.numeros("the 1990s and the 1st place, 14 % of 35.37 million") == [
        "1990", "1", "14", "35.37"]
    ser = serializado(run("La proporción es el doble de la de la década de 1990."))
    res = validar.validar_bloque(
        ser.plano, "<f1>The proportion is double that of the 1990s.</f1>",
        destino="en", ser=ser)
    assert res.ok, res.problemas


def test_glosario_admite_base_sin_sigla():
    ser = serializado(run("Se aplicó monitoreo continuo de glucosa durante la noche."))
    ok = "<f1>Continuous glucose monitoring systems were applied overnight.</f1>"
    res = validar.validar_bloque(ser.plano, ok, destino="en", ser=ser,
                                 glosario=GLOSARIO)
    assert not any("GLOSARIO" in p for p in res.problemas), res.problemas


def test_lista_de_nombres_no_falla_por_idioma():
    ser = serializado(run("Zoraida Yazmin Meneses Marin, Alexandar Walter Riveros "
                          "Bernardo, Jorge Elias Sanchez Capani, Christian Murga."))
    misma = ("<f1>Zoraida Yazmin Meneses Marin, Alexandar Walter Riveros "
             "Bernardo, Jorge Elias Sanchez Capani, Christian Murga.</f1>")
    res = validar.validar_bloque(ser.plano, misma, destino="en", ser=ser)
    assert res.detalles["idioma"].startswith("omitido")
    assert not any("IDIOMA" in p for p in res.problemas), res.problemas
