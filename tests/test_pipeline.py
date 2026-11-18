"""Pruebas del núcleo reutilizable ``pipeline`` (Fase 0).

Todo con el cliente falso: ninguna prueba toca la API real.
"""

import hashlib
import shutil
import threading

import pytest
from typer.testing import CliRunner

from traductor import cli, pipeline, verificar_formato
from traductor.docx_io import Documento

runner = CliRunner()


def _sha(ruta) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def corrida(docx_real, tmp_path_factory):
    """Una traducción completa con el cliente falso, compartida por el módulo."""
    entrada = tmp_path_factory.mktemp("entrada") / docx_real.name
    shutil.copy(docx_real, entrada)
    antes = _sha(entrada)

    eventos: list[tuple[float, str]] = []
    res = pipeline.traducir_documento(
        entrada, cliente="falso",
        on_progreso=lambda f, m: eventos.append((f, m)))
    return entrada, res, eventos, antes


def test_escribe_los_tres_archivos(corrida):
    entrada, res, _, _ = corrida
    assert res.ruta_docx.exists() and res.ruta_docx.name == (
        "FORMATO FINAL DE INSULINA_EN_SIMULADO.docx")
    assert res.ruta_docx.parent == entrada.parent      # junto al original
    assert res.ruta_informe.exists()
    assert res.ruta_comparacion.exists()
    assert res.ruta_respuestas is None                 # solo con --solo
    assert res.pdf == ""                               # sin --pdf


def test_no_modifica_el_original(corrida):
    entrada, _, _, antes = corrida
    assert _sha(entrada) == antes


def test_fidelidad_y_conteos(corrida):
    _, res, _, _ = corrida
    assert res.diferencias == []
    assert verificar_formato.comparar(
        corrida[0], res.ruta_docx) == []
    assert res.fidelidad.startswith("OK")
    assert res.ok
    assert res.origen == "es" and res.destino == "en"
    assert res.parrafos_totales == 247
    assert res.parrafos_traducidos == 214
    assert res.celdas_traducidas == 144
    assert res.lotes == 45
    assert res.reintentos == 0
    assert res.formato_simplificado == []
    assert res.a_revisar == []
    assert res.tokens_entrada == 0 and res.tokens_salida == 0
    assert res.tokens_total == 0


def test_filas_de_comparacion(corrida):
    _, res, _, _ = corrida
    assert len(res.filas) == 247
    por_id = {f["id"]: f for f in res.filas}
    assert por_id["P0001"]["estado"] == "OK"
    assert por_id["P0001"]["traducido"] is True
    assert por_id["P0001"]["en"] != "—"
    assert por_id["P0001"]["validacion"] == []
    # bibliografía y pie no se traducen, y se explica por qué
    assert por_id["P0080"]["traducido"] is False
    assert por_id["P0080"]["estado"] == "no traducido (bibliografía)"
    assert por_id["F10001"]["estado"] == "no traducido (pie)"
    # el título de la sección sí se traduce (solo las entradas se protegen)
    assert por_id["P0079"]["traducido"] is True
    # las filas conservan el orden del documento
    assert [f["orden"] for f in res.filas] == sorted(f["orden"] for f in res.filas)


def test_progreso_por_lote(corrida):
    _, res, eventos, _ = corrida
    assert len(eventos) == res.lotes
    assert eventos[0] == (pytest.approx(1 / 45), "Lote 1/45")
    assert eventos[-1] == (pytest.approx(1.0), "Lote 45/45")
    fracciones = [f for f, _ in eventos]
    assert fracciones == sorted(fracciones)
    assert all(0 < f <= 1 for f in fracciones)


def test_dry_run_no_escribe_ni_traduce(docx_real, tmp_path):
    copia = tmp_path / docx_real.name
    shutil.copy(docx_real, copia)
    antes = {p.name for p in tmp_path.iterdir()}

    res = pipeline.traducir_documento(copia, dry_run=True, precio_millon=0.28)

    assert res.dry_run and res.hay_lotes
    assert res.ruta_docx is None and res.ruta_informe is None
    assert res.lotes == 45
    assert res.parrafos_para_traducir == 214
    assert res.caracteres > 0 and res.tokens_estimados == res.caracteres // 4
    assert res.coste_estimado > 0
    assert "MENSAJE DE SISTEMA" in res.prompt_ejemplo
    assert "<<<P" in res.prompt_ejemplo
    assert res.llamadas == 0
    assert {p.name for p in tmp_path.iterdir()} == antes


def test_carpeta_salida_y_solo(docx_real, tmp_path):
    copia = tmp_path / docx_real.name
    shutil.copy(docx_real, copia)
    salidas = tmp_path / "salidas" / "2026-10-29_1200"

    res = pipeline.traducir_documento(copia, salidas, cliente="falso",
                                      solo="P0001,P0011")

    assert res.ruta_docx.parent == salidas
    assert res.ruta_docx.name.endswith("_EN_SIMULADO_prueba.docx")
    assert res.ruta_informe.parent == salidas
    assert res.ruta_comparacion.parent == salidas
    assert res.ruta_respuestas.exists()
    assert res.parrafos_traducidos == 2

    antes = {p.id: p.texto for p in Documento(docx_real).parrafos()}
    ahora = {p.id: p.texto for p in Documento(res.ruta_docx).parrafos()}
    for pid in ("P0001", "P0011"):
        assert ahora[pid] != antes[pid]
    for pid in ("P0002", "P0008", "P0080"):
        assert ahora[pid] == antes[pid]


def test_cli_y_pipeline_dan_el_mismo_docx(corrida, docx_real, tmp_path):
    entrada, res, _, _ = corrida
    copia = tmp_path / docx_real.name
    shutil.copy(docx_real, copia)

    resultado = runner.invoke(cli.app, ["traducir", str(copia),
                                        "--cliente", "falso"])
    assert resultado.exit_code == 0, resultado.output

    salida_cli = tmp_path / (copia.stem + "_EN_SIMULADO.docx")
    assert _sha(salida_cli) == _sha(res.ruta_docx)
    assert f"Listo: {salida_cli.name}" in resultado.output
    assert "reintentos: 0" in resultado.output


def test_error_fatal_aborta_sin_escribir_nada(docx_real, tmp_path, monkeypatch):
    """Una clave inválida (401) aborta en el primer lote y no deja artefactos."""
    from traductor.deepseek import ErrorFatal

    class ClienteRoto:
        def __init__(self, **kwargs):
            self.stats = None

        def traducir(self, sistema, usuario):
            raise ErrorFatal("401 Authentication Fails")

    monkeypatch.setattr(pipeline, "ClienteDeepSeek", ClienteRoto)
    copia = tmp_path / docx_real.name
    shutil.copy(docx_real, copia)

    with pytest.raises(ErrorFatal):
        pipeline.traducir_documento(copia)

    assert {p.name for p in tmp_path.iterdir()} == {copia.name}


def test_cancelar_entre_lotes_no_escribe_archivos_parciales(docx_real, tmp_path):
    """La cancelación se observa antes del lote siguiente y no guarda nada."""
    copia = tmp_path / docx_real.name
    shutil.copy(docx_real, copia)
    salidas = tmp_path / "salidas" / "cancelada"
    cancelar = threading.Event()

    with pytest.raises(pipeline.TraduccionCancelada):
        pipeline.traducir_documento(
            copia, salidas, cliente="falso", cancelar=cancelar,
            on_progreso=lambda _fraccion, _mensaje: cancelar.set())

    assert not list(salidas.glob("*"))


def test_evento_cancelar_sin_activar_conserva_resultado_identico(docx_real, tmp_path):
    """Un evento opcional inactivo no cambia el flujo histórico del CLI."""
    primera = tmp_path / "primera"
    segunda = tmp_path / "segunda"
    primera.mkdir()
    segunda.mkdir()
    entrada_a = primera / docx_real.name
    entrada_b = segunda / docx_real.name
    shutil.copy(docx_real, entrada_a)
    shutil.copy(docx_real, entrada_b)

    sin_evento = pipeline.traducir_documento(entrada_a, cliente="falso")
    con_evento = pipeline.traducir_documento(
        entrada_b, cliente="falso", cancelar=threading.Event())

    assert _sha(sin_evento.ruta_docx) == _sha(con_evento.ruta_docx)


def test_cli_sale_con_codigo_3_en_error_fatal(docx_real, tmp_path, monkeypatch):
    from traductor.deepseek import ErrorFatal

    class ClienteRoto:
        def __init__(self, **kwargs):
            self.stats = None

        def traducir(self, sistema, usuario):
            raise ErrorFatal("401 Authentication Fails, your api key is invalid")

    monkeypatch.setattr(pipeline, "ClienteDeepSeek", ClienteRoto)
    copia = tmp_path / docx_real.name
    shutil.copy(docx_real, copia)

    res = runner.invoke(cli.app, ["traducir", str(copia)])

    assert res.exit_code == 3, res.output
    assert "Error del cliente" in res.output
    assert "401" in res.output
    assert {p.name for p in tmp_path.iterdir()} == {copia.name}
