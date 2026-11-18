import shutil
import sys
import types
import zipfile

from typer.testing import CliRunner

from traductor import cli, verificar_formato
from traductor.docx_io import Documento

runner = CliRunner()


def test_dry_run_muestra_el_prompt(docx_real, tmp_path):
    copia = tmp_path / docx_real.name
    shutil.copy(docx_real, copia)
    resultado = runner.invoke(cli.app, ["traducir", str(copia), "--dry-run"])
    assert resultado.exit_code == 0, resultado.output
    salida = resultado.output
    assert "MENSAJE DE SISTEMA" in salida
    assert "Eres un traductor profesional" in salida
    assert "MENSAJE DE USUARIO" in salida
    assert "<<<P" in salida
    assert "Lotes:" in salida
    # no se creó ningún archivo de salida
    assert not (tmp_path / "FORMATO FINAL DE INSULINA_EN.docx").exists()


def test_flujo_completo_con_cliente_falso(docx_real, tmp_path):
    copia = tmp_path / docx_real.name
    shutil.copy(docx_real, copia)
    resultado = runner.invoke(cli.app, ["traducir", str(copia),
                                        "--cliente", "falso"])
    assert resultado.exit_code == 0, resultado.output

    salida = tmp_path / (copia.stem + "_EN_SIMULADO.docx")
    informe = tmp_path / (salida.name + ".informe.md")
    assert salida.exists() and informe.exists()

    # fidelidad de formato
    assert verificar_formato.comparar(copia, salida) == []

    # el informe declara los dos datos pedidos
    texto = informe.read_text(encoding="utf-8")
    assert "| **Reintentos** | **0** |" in texto
    assert "| **Párrafos con formato simplificado** | **0** |" in texto
    assert "Párrafos totales | 247" in texto

    doc = Documento(salida)
    idx = {p.id: p for p in doc.parrafos()}

    # encabezado: solo se traduce "Manuscrito en preparación 2026"
    cabecera = idx["H10001"].texto
    assert "SSRG International Journal of Electrical and Electronics Engineering" in cabecera
    assert "Manuscrito en preparación 2026" not in cabecera

    # pie intacto byte a byte
    with zipfile.ZipFile(docx_real) as a, zipfile.ZipFile(salida) as b:
        assert a.read("word/footer1.xml") == b.read("word/footer1.xml")
        assert a.read("word/styles.xml") == b.read("word/styles.xml")
        assert a.read("word/media/image4.jpg") == b.read("word/media/image4.jpg")

    # la bibliografía no se tradujo
    assert idx["P0080"].texto == next(
        p.texto for p in Documento(docx_real).parrafos() if p.id == "P0080")


def test_solo_traduce_los_ids_indicados(docx_real, tmp_path):
    copia = tmp_path / docx_real.name
    shutil.copy(docx_real, copia)
    elegidos = "P0002,P0011,T01R02C02"
    resultado = runner.invoke(cli.app, ["traducir", str(copia),
                                        "--cliente", "falso",
                                        "--solo", elegidos])
    assert resultado.exit_code == 0, resultado.output
    salida = tmp_path / (copia.stem + "_EN_SIMULADO_prueba.docx")
    assert salida.exists()

    doc = Documento(salida)
    idx = {p.id: p for p in doc.parrafos()}
    original = {p.id: p.texto for p in Documento(docx_real).parrafos()}
    for pid in elegidos.split(","):
        assert idx[pid].texto != original[pid], pid
    # el resto no se toca
    for pid in ("P0012", "P0025", "P0080", "T01R02C01"):
        assert idx[pid].texto == original[pid], pid
    assert (tmp_path / (salida.name + ".respuestas.txt")).exists()


def test_inspeccionar_comando(docx_real, tmp_path):
    copia = tmp_path / docx_real.name
    shutil.copy(docx_real, copia)
    resultado = runner.invoke(cli.app, ["inspeccionar", str(copia)])
    assert resultado.exit_code == 0
    assert "REPORTE DE INSPECCION" in resultado.output
    assert (tmp_path / (copia.name + ".inspeccion.txt")).exists()


def test_archivo_inexistente():
    resultado = runner.invoke(cli.app, ["traducir", "no-existe.docx"])
    assert resultado.exit_code != 0


def test_comando_interfaz_lanza_el_adaptador_local(monkeypatch):
    llamadas = []
    modulo = types.SimpleNamespace(
        lanzar_interfaz=lambda puerto: llamadas.append(puerto))
    monkeypatch.setitem(sys.modules, "traductor.interfaz", modulo)

    resultado = runner.invoke(cli.app, ["interfaz", "--puerto", "7877"])

    assert resultado.exit_code == 0, resultado.output
    assert llamadas == [7877]
