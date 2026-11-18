"""Fase 1.2 — EN→ES sin API: glosario invertido, prompts y validación.

Ninguna prueba de este archivo llama a DeepSeek.
"""

import shutil

import pytest
import yaml
from lxml import etree
from typer.testing import CliRunner

from traductor import cli, glosario, informe, marcado, pipeline, prompts, validar
from traductor.deepseek import Cache, ClienteDeepSeek, ClienteFalso

RAIZ = __import__("pathlib").Path(__file__).resolve().parents[1]

NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')

GLOSARIO_EN_ES = {"pares": [("type 1 diabetes", "diabetes tipo 1"),
                            ("traceability", "trazabilidad")],
                  "no_traducir": ["Android Studio", "nivel_glucosa"]}

runner = CliRunner()


# ---------------------------------------------------------------- 1.1 glosario


def test_bloque_en_es_esta_al_dia():
    datos = yaml.safe_load((RAIZ / "glosario.yaml").read_text(encoding="utf-8"))
    revision = glosario.revisar(datos)
    assert revision["conflictos"] == []
    assert revision["al_dia"], (
        revision["faltan"], revision["sobran"], revision["distintos"])


def test_overrides_que_la_inversion_no_resuelve():
    g = prompts.cargar_glosario(RAIZ / "glosario.yaml", "en", "es")
    pares = dict(g["pares"])
    assert pares["tube"] == "manguera"
    assert pares["casing"] == "armazón"
    assert pares["primary tube"] == "manguera primaria"
    assert pares["continuous glucose monitoring (CGM)"] == (
        "monitoreo continuo de glucosa")


def test_conflicto_de_inversion_conserva_el_primero():
    inverso, conflictos = glosario.invertir(
        {"bomba": "pump", "motobomba": "pump", "cápsula": "capsule"})
    assert inverso == {"pump": "bomba", "capsule": "cápsula"}
    assert len(conflictos) == 1 and "motobomba" in conflictos[0]


def test_cargar_glosario_en_es_sin_duplicados():
    g = prompts.cargar_glosario(RAIZ / "glosario.yaml", "en", "es")
    claves = [a.casefold() for a, _ in g["pares"]]
    assert len(claves) == len(set(claves))
    assert ("traceability", "trazabilidad") in g["pares"]
    # no se cuela la inversión duplicada de es-en
    assert claves.count("casing") == 1


# ------------------------------------------------------------------ 1.1 prompts


def test_prompt_en_es_variante_y_convenciones():
    g = prompts.cargar_glosario(RAIZ / "glosario.yaml", "en", "es")
    texto = prompts.sistema("en", "es", g)
    assert "inglés → español" in texto
    assert "español neutro latinoamericano (Perú)" in texto
    assert "punto decimal (35.37)" in texto
    assert "espacio antes del signo de porcentaje (14 %)" in texto
    assert "no introduzcas «nosotros»" in texto
    assert "Tabla 1" in texto
    assert "todo en mayúsculas" in texto
    # glosario invertido y términos que no se traducen
    assert "traceability = trazabilidad" in texto
    assert "Android Studio" in texto


def test_reglas_congeladas_siguen_igual():
    g = prompts.cargar_glosario(RAIZ / "glosario.yaml", "en", "es")
    texto = prompts.sistema("en", "es", g)
    assert "Conserva exactamente las etiquetas" in texto
    assert "No cambies las citas [n]" in texto
    assert ("Los marcadores ⟦Xnn⟧ reemplazan contenido protegido (citas, campos, "
            "imágenes).") in texto
    assert "sin corregir ni comentar" in texto
    assert 'no introduzcas "we"' in texto


# ------------------------------------------------------------------ 1.1 validar


def par(interior: str):
    return etree.fromstring(f"<w:p {NS}>{interior}</w:p>")


def run(texto: str, rpr: str = "") -> str:
    return f"<w:r><w:rPr>{rpr}</w:rPr><w:t>{texto}</w:t></w:r>"


def test_validar_en_es_correcto_con_convenciones_espanolas():
    ser = marcado.serializar(par(run(
        "In 2022, about 14% of people with type 1 diabetes used traceability "
        "systems, with a difference of 0.36 percentage points [3].")), "P1")
    es = ("<f1>En 2022, cerca del 14 % de las personas con diabetes tipo 1 "
          "utilizaban sistemas de trazabilidad, con una diferencia de 0,36 "
          "puntos porcentuales [3].</f1>")
    res = validar.validar_bloque(ser.plano, es, destino="es", ser=ser,
                                 glosario=GLOSARIO_EN_ES)
    assert res.ok, res.problemas
    assert res.detalles["idioma"] == "es"
    assert res.detalles["ratio"] > 1.0        # el español crece


def test_validar_en_es_numero_alterado():
    ser = marcado.serializar(par(run(
        "The countdown lasts 10 seconds for every scheduled dose.")), "P1")
    res = validar.validar_bloque(
        ser.plano, "<f1>La cuenta regresiva dura 12 segundos para cada dosis "
        "programada.</f1>", destino="es", ser=ser)
    assert any("NUMERO_ALTERADO" in p for p in res.problemas), res.problemas


def test_validar_en_es_idioma_sin_traducir():
    ser = marcado.serializar(par(run(
        "The system monitors glucose and manages insulin therapy overnight.")), "P1")
    res = validar.validar_bloque(
        ser.plano,
        "<f1>The system monitors glucose and manages insulin therapy "
        "overnight.</f1>", destino="es", ser=ser)
    assert any("IDIOMA" in p for p in res.problemas), res.problemas


def test_validar_en_es_glosario_no_respetado():
    ser = marcado.serializar(par(run(
        "Traceability is required for every audit of the device.")), "P1")
    res = validar.validar_bloque(
        ser.plano, "<f1>La rastreabilidad es obligatoria en toda auditoría "
        "del dispositivo.</f1>", destino="es", ser=ser,
        glosario=GLOSARIO_EN_ES)
    assert any("GLOSARIO: 'traceability'" in p for p in res.problemas), res.problemas


def test_validar_en_es_marcadores_y_etiquetas():
    ser = marcado.serializar(
        par(run("Before ", "") + '<w:r><w:drawing/></w:r>'
            + run("after", "<w:b/>") + run(" the sensor.", "")))
    ok = "<f1>Antes ⟦X01⟧<b>después</b> el sensor.</f1>"
    res = validar.validar_bloque(ser.plano, ok, destino="es", ser=ser)
    assert res.ok, res.problemas

    sin_marcador = "<f1>Antes <b>después</b> el sensor.</f1>"
    res2 = validar.validar_bloque(ser.plano, sin_marcador, destino="es", ser=ser)
    assert any("MARCADOR_FALTANTE" in p for p in res2.problemas), res2.problemas


def test_validar_en_es_longitud_se_permite_crecer():
    ser = marcado.serializar(par(run(
        "Glucose monitoring improves outcomes in type 1 diabetes.")), "P1")
    es = ("<f1>El monitoreo de glucosa mejora los resultados en la diabetes "
          "tipo 1.</f1>")
    res = validar.validar_bloque(ser.plano, es, destino="es", ser=ser)
    assert not any("LONGITUD" in p for p in res.problemas), res.problemas
    assert res.detalles["ratio"] > 1.0


# ------------------------------------------------------------------ 1.1 caché


def test_cache_separa_direcciones():
    a = Cache.clave("sistema", "usuario", "deepseek-flash", "es", "en")
    b = Cache.clave("sistema", "usuario", "deepseek-flash", "en", "es")
    historica = Cache.clave("sistema", "usuario", "deepseek-flash")
    assert a != b and a != historica and b != historica
    assert historica == Cache.clave("sistema", "usuario", "deepseek-flash",
                                    None, None)


def test_cache_reutiliza_entrada_heredada(tmp_path):
    cache = Cache(tmp_path)
    historica = Cache.clave("sistema", "usuario", "deepseek-flash")
    cache.escribir(historica, "<<<P1>>>\nrespuesta vieja")

    cliente = ClienteDeepSeek(api_key="x", modelo="deepseek-flash",
                              de="en", a="es", cache=cache)
    assert cliente.traducir("sistema", "usuario") == "<<<P1>>>\nrespuesta vieja"
    assert cliente.stats.llamadas == 0
    assert cliente.stats.aciertos_cache == 1
    # se migra a la clave nueva
    nueva = Cache.clave("sistema", "usuario", "deepseek-flash", "en", "es")
    assert cache.leer(nueva) == "<<<P1>>>\nrespuesta vieja"


# ------------------------------------------------------- 1.1 informe y cliente


def test_consistencia_en_espanol():
    res = informe.consistencia(
        {"pares": [("traceability", "trazabilidad")]},
        {"P1": "La trazabilidad es clave en cada registro de auditoría."},
        destino="es")
    assert res["destino"] == "es"
    termino = res["terminos"][0]
    assert termino["objetivo"] == "trazabilidad" and termino["n"] == 1
    assert res["ejemplos"] == []


def test_consistencia_detecta_ingles_residual():
    res = informe.consistencia(
        {"pares": []},
        {"P1": "This is the result of the study with the device."},
        destino="es")
    assert res["ejemplos"] and res["ejemplos"][0][0] == "P1"


def test_cliente_falso_en_espanol():
    cliente = ClienteFalso(idioma="es")
    salida = cliente._bloque("<f1>Texto de prueba con suficiente longitud.</f1>")
    assert "Esta sección" in salida
    assert validar.idioma(marcado.texto_plano(salida)) == "es"


# ---------------------------------------------------------------- 1.2 dry-run


@pytest.fixture(scope="module")
def docx_en():
    ruta = RAIZ / "resultados" / "en" / "completo" / "FORMATO FINAL DE INSULINA_EN.docx"
    if not ruta.exists():
        pytest.skip("falta FORMATO FINAL DE INSULINA_EN.docx")
    return ruta


def test_cli_dry_run_en_es(docx_en, tmp_path):
    copia = tmp_path / docx_en.name
    shutil.copy(docx_en, copia)

    resultado = runner.invoke(cli.app, ["traducir", str(copia), "--dry-run",
                                        "--de", "en", "--a", "es"])
    assert resultado.exit_code == 0, resultado.output
    salida = resultado.output

    assert "Dirección: en → es" in salida
    assert "PROMPT COMPLETO DE UN LOTE DE EJEMPLO" in salida
    assert "===== MENSAJE DE SISTEMA =====" in salida
    assert "inglés → español" in salida
    assert "español neutro latinoamericano (Perú)" in salida
    assert "14 %" in salida
    assert "Tabla 1" in salida
    assert "traceability = trazabilidad" in salida
    assert "<<<" in salida
    # nada traducido ni escrito
    assert not (tmp_path / "FORMATO FINAL DE INSULINA_EN_ES.docx").exists()
    assert {p.name for p in tmp_path.iterdir()} == {copia.name}


# ------------------------------------------------------------- 1.2 pipeline


def test_pipeline_en_es_con_cliente_falso_no_usa_red(docx_en, tmp_path):
    copia = tmp_path / docx_en.name
    shutil.copy(docx_en, copia)

    res = pipeline.traducir_documento(copia, cliente="falso")

    assert res.origen == "en" and res.destino == "es"
    assert res.ruta_docx.name == "FORMATO FINAL DE INSULINA_EN_ES_SIMULADO.docx"
    assert res.diferencias == []
    assert res.formato_simplificado == []
    assert res.a_revisar == []
    assert res.tokens_total == 0
    assert res.filas and len(res.filas) == res.parrafos_totales

    filas = {f["id"]: f for f in res.filas}
    assert "Esta sección" in filas["P0007"]["en"]
    assert filas["P0007"]["estado"] == "OK"

    texto = res.ruta_informe.read_text(encoding="utf-8")
    assert "Dirección: en → es" in texto
    assert "| Término (ES) |" in texto
    assert "Posible texto en inglés sin traducir" in texto

    comparacion = res.ruta_comparacion.read_text(encoding="utf-8")
    assert comparacion.startswith("# Comparación EN → ES")
    assert "| EN | Article type: Original article |" in comparacion

