from traductor import secciones
from traductor.docx_io import Documento


def test_titulos_numerados():
    assert secciones.es_titulo("1. Introducción")
    assert secciones.es_titulo("2.1. Enfoque y alcance del diseño")
    assert secciones.es_titulo("3.6. Flujo general y matriz de requisitos")
    assert not secciones.es_titulo("El texto termina en punto y sigue.")


def test_palabras_clave():
    assert secciones.es_titulo("Referencias")
    assert secciones.es_titulo("Abstract")
    assert secciones.es_titulo("Palabras clave - diabetes")
    assert not secciones.es_titulo("Una frase cualquiera sin numeración")


def test_estilo_de_titulo_manda():
    titulos = frozenset({"Ttulo1", "Ttulo2"})
    assert secciones.es_titulo("x" * 200 + ".", "Ttulo2", titulos)


def test_titulo_referencias_no_se_protege():
    class P:
        contenedor = "cuerpo"

        def __init__(self, texto):
            self.texto = texto
            self.estilo = ""
            self.extra = {}

    ps = [P("Referencias"),
          P("World Health Organization. Global report on diabetes. 2016."),
          P("International Diabetes Federation. IDF Atlas. 2024.")]
    secciones.asignar(ps)
    # el título se traduce (AGENTS.md §7) ...
    assert ps[0].extra["titulo"] is True
    assert ps[0].extra["protegido"] is False
    # ... y las entradas de la bibliografía no
    assert ps[1].extra["protegido"] is True
    assert ps[2].extra["protegido"] is True


def test_asignacion_sobre_documento_real(docx_real):
    doc = Documento(docx_real)
    parrafos = doc.parrafos()
    titulos = frozenset({"Ttulo", "Ttulo1", "Ttulo2"})
    secciones.asignar(parrafos, titulos)
    idx = {p.id: p for p in parrafos}
    assert idx["P0010"].extra["seccion"] == "1. Introducción"
    assert idx["P0011"].extra["seccion"] == "1. Introducción"
    assert idx["P0016"].extra["seccion"] == "2.1. Enfoque y alcance del diseño"
    assert idx["P0079"].extra["protegido"] is False     # título "Referencias"
    assert idx["P0080"].extra["protegido"] is True     # [1] Worl Health...
    assert idx["P0101"].extra["protegido"] is True
    assert idx["P0064"].extra["protegido"] is False
