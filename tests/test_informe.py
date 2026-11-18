from traductor import informe
from traductor.informe import Informe


def test_informe_incluye_conteos(tmp_path):
    inf = Informe(archivo="a.docx", salida="a_EN.docx", origen="es", destino="en",
                  modelo="deepseek-v4-flash", cliente="cliente falso",
                  parrafos_totales=247, parrafos_traducidos=213,
                  celdas_traducidas=144, omitidos=25, protegidos=10,
                  lotes=44, llamadas=44, reintentos=3)
    inf.formato_simplificado.extend(["P0012", "T02R04C01"])
    inf.a_revisar.append(("P0013", ["CITA_ALTERADA: [1] -> [2]"]))
    inf.contar_problemas(["CITA_ALTERADA: [1] -> [2]", "LONGITUD: 2.10x"])
    inf.fidelidad = "OK: esqueleto XML idéntico"
    inf.imagenes_con_texto.append("`P0023` — Fig. 1")
    inf.erratas.append("`P0080`: «Worl Health Organization»")
    ruta = inf.escribir(tmp_path / "informe.md")
    texto = ruta.read_text(encoding="utf-8")

    assert "| **Reintentos** | **3** |" in texto
    assert "| **Párrafos con formato simplificado** | **2** |" in texto
    assert "`P0012`" in texto and "`T02R04C01`" in texto
    assert "CITA_ALTERADA" in texto
    assert "Fidelidad de formato:** OK" in texto
    assert "Revisión humana obligatoria" in texto


def test_informe_sin_incidencias(tmp_path):
    inf = Informe(archivo="a.docx", salida="a_EN.docx")
    inf.fidelidad = "OK"
    texto = inf.escribir(tmp_path / "i.md").read_text(encoding="utf-8")
    assert "Ninguno: todos los párrafos conservaron" in texto
    assert "| **Reintentos** | **0** |" in texto


def test_variantes_no_cuentan_el_termino_canonico():
    """«portátil» dentro de «dispositivo portátil» no es una variante."""
    glosario = {"pares": [
        ("portable device", "dispositivo portátil"),
        ("continuous glucose monitoring (CGM)", "monitoreo continuo de glucosa"),
    ]}
    res = informe.consistencia(
        glosario,
        {"P1": "El dispositivo portátil envía datos y el monitoreo continuo de "
               "glucosa ayuda al paciente."},
        destino="es")
    por_termino = {t["objetivo"]: t for t in res["terminos"]}
    assert por_termino["dispositivo portátil"]["n"] == 1
    assert por_termino["dispositivo portátil"]["variantes"] == []
    assert por_termino["monitoreo continuo de glucosa"]["n"] == 1
    assert por_termino["monitoreo continuo de glucosa"]["variantes"] == []


def test_variantes_si_cuentan_las_que_estan_fuera_del_canonico():
    res = informe.consistencia(
        {"pares": [("portable device", "dispositivo portátil")]},
        {"P1": "El dispositivo vestible y el dispositivo portátil conviven."},
        destino="es")
    variantes = dict(res["terminos"][0]["variantes"])
    assert variantes == {"dispositivo vestible": 1}
