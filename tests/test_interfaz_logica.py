"""Pruebas de la lógica independiente de la interfaz web.

Todas usan el cliente falso; ninguna prueba puede llamar a DeepSeek.
"""

import os
import shutil
import threading
import time
import zipfile
from pathlib import Path

import pytest

from traductor import interfaz_logica, pipeline
from traductor.deepseek import ErrorCliente, ErrorTransitorio


def _docx_minimo(ruta: Path, extra: dict[str, bytes] | None = None) -> None:
    miembros = {
        "[Content_Types].xml": b"<Types/>",
        "word/document.xml": b"<w:document/>",
    }
    miembros.update(extra or {})
    with zipfile.ZipFile(ruta, "w") as paquete:
        for nombre, contenido in miembros.items():
            paquete.writestr(nombre, contenido)


def test_valida_un_docx_real(docx_real, tmp_path):
    copia = tmp_path / "articulo.docx"
    shutil.copy(docx_real, copia)

    assert interfaz_logica.validar_archivo_subido(copia) == copia


@pytest.mark.parametrize("nombre,contenido", [
    ("texto.txt", b"no es un documento"),
    ("corrupto.docx", b"no es un zip"),
])
def test_rechaza_archivos_no_validos(nombre, contenido, tmp_path):
    archivo = tmp_path / nombre
    archivo.write_bytes(contenido)

    with pytest.raises(interfaz_logica.ArchivoInvalido):
        interfaz_logica.validar_archivo_subido(archivo)


def test_rechaza_ruta_peligrosa_en_paquete(tmp_path):
    archivo = tmp_path / "peligroso.docx"
    _docx_minimo(archivo, {"../fuera.txt": b"x"})

    with pytest.raises(interfaz_logica.ArchivoInvalido, match="ruta insegura"):
        interfaz_logica.validar_archivo_subido(archivo)


def test_rechaza_zip_bomb_por_razon_de_compresion(tmp_path):
    archivo = tmp_path / "bomba.docx"
    with zipfile.ZipFile(archivo, "w", compression=zipfile.ZIP_DEFLATED) as paquete:
        paquete.writestr("[Content_Types].xml", b"<Types/>")
        paquete.writestr("word/document.xml", b"<w:document/>")
        paquete.writestr("word/relleno.bin", b"0" * 1_000_000)

    with pytest.raises(interfaz_logica.ArchivoInvalido, match="compresión"):
        interfaz_logica.validar_archivo_subido(archivo)


def test_sanitiza_nombres_y_limpia_salidas_antiguas(tmp_path):
    raiz = tmp_path / "salidas"
    antigua = raiz / "antigua"
    reciente = raiz / "reciente"
    antigua.mkdir(parents=True)
    reciente.mkdir()
    hace_ocho_dias = time.time() - (8 * 24 * 60 * 60)
    os.utime(antigua, (hace_ocho_dias, hace_ocho_dias))

    eliminadas = interfaz_logica.limpiar_salidas_antiguas(raiz, dias=7)
    salida = interfaz_logica.preparar_carpeta_salida(raiz)

    assert antigua in eliminadas and not antigua.exists()
    assert reciente.exists() and salida.parent == raiz
    assert interfaz_logica.sanitizar_nombre("..\\inseguro / artículo.docx") == "artículo.docx"


def test_estimacion_no_escribe_archivos(docx_real, tmp_path):
    copia = tmp_path / docx_real.name
    shutil.copy(docx_real, copia)

    resultado = interfaz_logica.estimar(copia, direccion="es-en")

    assert resultado.dry_run and resultado.lotes > 0
    assert resultado.tokens_estimados > 0 and resultado.prompt_ejemplo
    assert resultado.coste_estimado > 0
    assert {p.name for p in tmp_path.iterdir()} == {copia.name}


def test_ejecucion_completa_falsa_empaqueta_resultados(docx_real, tmp_path):
    copia = tmp_path / docx_real.name
    shutil.copy(docx_real, copia)
    salida = interfaz_logica.preparar_carpeta_salida(tmp_path / "salidas")

    trabajo = interfaz_logica.iniciar_traduccion(
        copia, salida, direccion="automatica", modelo="deepseek-flash",
        modo="completa", cliente="falso")
    resultado = interfaz_logica.esperar_traduccion(trabajo)
    paquete = interfaz_logica.empaquetar_resultados(resultado)

    assert resultado.ruta_docx.exists()
    assert resultado.ruta_informe.exists()
    assert resultado.ruta_comparacion.exists()
    assert paquete.exists()
    with zipfile.ZipFile(paquete) as comprimido:
        assert {resultado.ruta_docx.name, resultado.ruta_informe.name,
                resultado.ruta_comparacion.name} <= set(comprimido.namelist())


def test_prueba_parcial_y_filtro_de_problemas(docx_real, tmp_path):
    copia = tmp_path / docx_real.name
    shutil.copy(docx_real, copia)
    salida = interfaz_logica.preparar_carpeta_salida(tmp_path / "salidas")

    trabajo = interfaz_logica.iniciar_traduccion(
        copia, salida, direccion="es-en", modelo="deepseek-flash",
        modo="parcial", ids="P0001,P0011", cliente="falso")
    resultado = interfaz_logica.esperar_traduccion(trabajo)
    problemas = interfaz_logica.filtrar_filas(resultado.filas, solo_problemas=True)

    assert resultado.parrafos_traducidos == 2
    assert resultado.ruta_respuestas.exists()
    assert all(fila["estado"] != "OK" for fila in problemas)


def test_ejecucion_usa_nombre_sanitizado_para_las_salidas(docx_real, tmp_path,
                                                          monkeypatch):
    copia = tmp_path / docx_real.name
    shutil.copy(docx_real, copia)
    salida = interfaz_logica.preparar_carpeta_salida(tmp_path / "salidas")
    monkeypatch.setattr(interfaz_logica, "sanitizar_nombre",
                        lambda _nombre: "documento_seguro.docx")

    trabajo = interfaz_logica.iniciar_traduccion(
        copia, salida, direccion="es-en", modelo="deepseek-flash",
        modo="parcial", ids="P0001", cliente="falso")
    resultado = interfaz_logica.esperar_traduccion(trabajo)

    assert resultado.ruta_docx.name.startswith("documento_seguro_EN_SIMULADO")
    assert not (salida / "documento_seguro.docx").exists()


def test_cancelacion_de_hilo_no_deja_artefactos(docx_real, tmp_path, monkeypatch):
    copia = tmp_path / docx_real.name
    shutil.copy(docx_real, copia)
    salida = interfaz_logica.preparar_carpeta_salida(tmp_path / "salidas")
    iniciada = threading.Event()
    permitir = threading.Event()
    original = pipeline.ClienteFalso.traducir

    def lenta(self, sistema, usuario):
        iniciada.set()
        permitir.wait(timeout=5)
        return original(self, sistema, usuario)

    monkeypatch.setattr(pipeline.ClienteFalso, "traducir", lenta)
    trabajo = interfaz_logica.iniciar_traduccion(
        copia, salida, direccion="automatica", modelo="deepseek-flash",
        modo="completa", cliente="falso")
    assert iniciada.wait(timeout=5)
    trabajo.cancelar.set()
    permitir.set()

    with pytest.raises(pipeline.TraduccionCancelada):
        interfaz_logica.esperar_traduccion(trabajo)
    assert not list(salida.iterdir())


@pytest.mark.parametrize(("error", "esperado"), [
    (ErrorCliente("falta DEEPSEEK_API_KEY"), "clave"),
    (ErrorCliente("401 invalid api key"), "inválida"),
    (ErrorCliente("insufficient balance"), "saldo"),
    (ErrorCliente("model not found"), "modelo"),
    (ErrorCliente("instala el paquete 'openai'"), "instalar"),
    (ErrorTransitorio("timeout de red"), "red"),
])
def test_mapea_errores_a_mensajes_en_espanol(error, esperado):
    assert esperado in interfaz_logica.mensaje_error(error).lower()
