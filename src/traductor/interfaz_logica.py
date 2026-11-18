"""Servicios puros y testeables para la interfaz local.

Este módulo no importa Gradio ni registra contenido de los documentos. La
interfaz visual solamente consume estas funciones y objetos.
"""

from __future__ import annotations

import os
import queue
import re
import shutil
import threading
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath

from . import pipeline
from .deepseek import ErrorCliente, ErrorTransitorio

MAX_TAMANO_SUBIDA = 50 * 1024 * 1024
MAX_TAMANO_DESCOMPRIMIDO = 250 * 1024 * 1024
MAX_RAZON_COMPRESION = 100
UMBRAL_TOKENS_POR_DEFECTO = 100_000
PRECIO_MILLON_POR_DEFECTO = 0.28
RAIZ_PROYECTO = Path(__file__).resolve().parents[2]
SALIDAS_POR_DEFECTO = RAIZ_PROYECTO / "salidas"
_TRABAJO_ACTIVO: "TrabajoTraduccion | None" = None
_BLOQUEO_TRABAJO = threading.Lock()


class ArchivoInvalido(ValueError):
    """El archivo cargado no es un DOCX seguro que se pueda procesar."""


def sanitizar_nombre(nombre: str) -> str:
    """Devuelve solo un nombre de archivo seguro, conservando Unicode útil."""
    base = Path(str(nombre).replace("\\", "/")).name.strip()
    limpio = "".join(c if (c.isalnum() or c in " ._-()") else "_" for c in base)
    limpio = re.sub(r"\s+", " ", limpio).strip(" .")
    return limpio or "documento.docx"


def _miembro_peligroso(nombre: str) -> bool:
    normalizado = nombre.replace("\\", "/")
    ruta = PurePosixPath(normalizado)
    return (normalizado.startswith("/") or normalizado.startswith("\\")
            or (len(normalizado) > 1 and normalizado[1] == ":")
            or ".." in ruta.parts)


def validar_archivo_subido(ruta: str | Path) -> Path:
    """Comprueba extensión, tamaño y estructura ZIP antes de abrir un DOCX."""
    archivo = Path(ruta)
    if archivo.suffix.lower() != ".docx":
        raise ArchivoInvalido("Selecciona un archivo con extensión .docx.")
    try:
        if not archivo.is_file() or archivo.stat().st_size > MAX_TAMANO_SUBIDA:
            raise ArchivoInvalido("El archivo no existe o supera el límite de 50 MB.")
        with zipfile.ZipFile(archivo) as paquete:
            miembros = set()
            total = 0
            for info in paquete.infolist():
                if _miembro_peligroso(info.filename):
                    raise ArchivoInvalido("El DOCX contiene una ruta insegura.")
                if info.is_dir():
                    continue
                total += info.file_size
                if total > MAX_TAMANO_DESCOMPRIMIDO:
                    raise ArchivoInvalido("El DOCX descomprimido es demasiado grande.")
                if info.file_size and (info.compress_size == 0 or
                                       info.file_size / info.compress_size >
                                       MAX_RAZON_COMPRESION):
                    raise ArchivoInvalido(
                        "El DOCX tiene una razón de compresión sospechosa.")
                miembros.add(info.filename)
    except ArchivoInvalido:
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        raise ArchivoInvalido("El archivo no es un DOCX válido.") from exc
    if not {"[Content_Types].xml", "word/document.xml"} <= miembros:
        raise ArchivoInvalido("El ZIP no contiene la estructura requerida de DOCX.")
    return archivo


def limpiar_salidas_antiguas(raiz: str | Path = SALIDAS_POR_DEFECTO, *,
                             dias: int = 7) -> list[Path]:
    """Elimina exclusivamente carpetas de resultados más antiguas que ``dias``."""
    directorio = Path(raiz)
    if not directorio.exists():
        return []
    limite = datetime.now().timestamp() - timedelta(days=dias).total_seconds()
    eliminadas: list[Path] = []
    for candidata in directorio.iterdir():
        if candidata.is_dir() and candidata.stat().st_mtime < limite:
            shutil.rmtree(candidata)
            eliminadas.append(candidata)
    return eliminadas


def preparar_carpeta_salida(raiz: str | Path = SALIDAS_POR_DEFECTO) -> Path:
    """Crea una carpeta de ejecución única dentro de ``salidas``."""
    base = Path(raiz)
    base.mkdir(parents=True, exist_ok=True)
    while True:
        candidata = base / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        try:
            candidata.mkdir()
        except FileExistsError:
            continue
        return candidata


def _direccion(direccion: str) -> tuple[str | None, str | None]:
    opciones = {
        "automatica": (None, None),
        "es-en": ("es", "en"),
        "en-es": ("en", "es"),
    }
    try:
        return opciones[direccion]
    except KeyError as exc:
        raise ValueError("Dirección de traducción no válida.") from exc


def estimar(archivo: str | Path, *, direccion: str = "automatica",
            modelo: str | None = None, glosario: str | Path | None = None,
            precio_millon: float = PRECIO_MILLON_POR_DEFECTO) -> pipeline.ResultadoTraduccion:
    """Prepara un dry-run; no escribe resultados ni instancia el cliente real."""
    entrada = validar_archivo_subido(archivo)
    de, a = _direccion(direccion)
    glosario_efectivo = glosario or RAIZ_PROYECTO / "glosario.yaml"
    argumentos = {"de": de, "a": a, "glosario": glosario_efectivo, "dry_run": True,
                  "precio_millon": precio_millon}
    if modelo is not None:
        argumentos["modelo"] = modelo
    return pipeline.traducir_documento(entrada, **argumentos)


def umbral_tokens() -> int:
    """Obtiene un límite no secreto con una alternativa conservadora."""
    try:
        return max(1, int(os.environ.get("UMBRAL_TOKENS", UMBRAL_TOKENS_POR_DEFECTO)))
    except ValueError:
        return UMBRAL_TOKENS_POR_DEFECTO


def precio_millon() -> float:
    """Lee una tarifa no secreta, con la referencia local usada por defecto."""
    try:
        return max(0.0, float(os.environ.get("PRECIO_MILLON",
                                             PRECIO_MILLON_POR_DEFECTO)))
    except ValueError:
        return PRECIO_MILLON_POR_DEFECTO


@dataclass
class TrabajoTraduccion:
    """Estado de una ejecución que el adaptador visual puede consultar."""

    cancelar: threading.Event
    progreso: queue.Queue[tuple[float, str]] = field(default_factory=queue.Queue)
    hilo: threading.Thread | None = None
    resultado: pipeline.ResultadoTraduccion | None = None
    error: BaseException | None = None
    carpeta_salida: Path | None = None


def _limpiar_artefactos(carpeta: Path) -> None:
    """Deja la carpeta de ejecución vacía sin tocar la caché reutilizable."""
    for elemento in carpeta.iterdir():
        if elemento.is_dir():
            shutil.rmtree(elemento)
        else:
            elemento.unlink()


def iniciar_traduccion(archivo: str | Path, carpeta_salida: str | Path, *,
                       direccion: str = "automatica", modelo: str,
                       modo: str = "completa", ids: str | None = None,
                       glosario: str | Path | None = None,
                       cliente: str = "real") -> TrabajoTraduccion:
    """Ejecuta el pipeline en un hilo y publica solo metadatos de progreso."""
    global _TRABAJO_ACTIVO
    with _BLOQUEO_TRABAJO:
        if _TRABAJO_ACTIVO is not None and _TRABAJO_ACTIVO.hilo is not None \
                and _TRABAJO_ACTIVO.hilo.is_alive():
            raise RuntimeError("Ya hay una traducción en curso.")
    entrada = validar_archivo_subido(archivo)
    salida = Path(carpeta_salida)
    salida.mkdir(parents=True, exist_ok=True)
    de, a = _direccion(direccion)
    if modo == "completa":
        solo = None
    elif modo == "parcial" and ids and ids.strip():
        solo = ids
    elif modo == "parcial":
        raise ValueError("Indica al menos un ID para la prueba parcial.")
    else:
        raise ValueError("Modo de ejecución no válido.")

    glosario_efectivo = glosario or RAIZ_PROYECTO / "glosario.yaml"
    trabajo = TrabajoTraduccion(cancelar=threading.Event(), carpeta_salida=salida)
    entrada_segura = salida / sanitizar_nombre(entrada.name)
    copiar_entrada = entrada.resolve() != entrada_segura.resolve()

    def informar(fraccion: float, mensaje: str) -> None:
        trabajo.progreso.put((fraccion, mensaje.replace("/", " de ")))

    def ejecutar() -> None:
        try:
            if copiar_entrada:
                shutil.copyfile(entrada, entrada_segura)
            trabajo.resultado = pipeline.traducir_documento(
                entrada_segura, salida, de=de, a=a, modelo=modelo, solo=solo,
                glosario=glosario_efectivo, cliente=cliente, reanudar=True,
                on_progreso=informar, cancelar=trabajo.cancelar)
        except BaseException as exc:  # se propaga de forma controlada al UI
            _limpiar_artefactos(salida)
            trabajo.error = exc
        finally:
            if copiar_entrada:
                entrada_segura.unlink(missing_ok=True)

    trabajo.hilo = threading.Thread(target=ejecutar, daemon=True,
                                    name="traductor-interfaz")
    with _BLOQUEO_TRABAJO:
        _TRABAJO_ACTIVO = trabajo
    trabajo.hilo.start()
    return trabajo


def esperar_traduccion(trabajo: TrabajoTraduccion,
                       timeout: float | None = None) -> pipeline.ResultadoTraduccion:
    """Espera al hilo y devuelve el resultado o relanza su error."""
    if trabajo.hilo is None:
        raise RuntimeError("El trabajo no se inició.")
    trabajo.hilo.join(timeout)
    if trabajo.hilo.is_alive():
        raise TimeoutError("La traducción aún está en curso.")
    if trabajo.error is not None:
        raise trabajo.error
    if trabajo.resultado is None:
        raise RuntimeError("La traducción terminó sin resultado.")
    return trabajo.resultado


def cancelar_traduccion_activa() -> bool:
    """Solicita detener la única ejecución activa, sin esperar a que termine."""
    with _BLOQUEO_TRABAJO:
        if _TRABAJO_ACTIVO is None or _TRABAJO_ACTIVO.hilo is None \
                or not _TRABAJO_ACTIVO.hilo.is_alive():
            return False
        _TRABAJO_ACTIVO.cancelar.set()
        return True


def empaquetar_resultados(resultado: pipeline.ResultadoTraduccion) -> Path:
    """Crea un ZIP de los artefactos públicos de una traducción terminada."""
    rutas = [resultado.ruta_docx, resultado.ruta_informe, resultado.ruta_comparacion,
             resultado.ruta_respuestas]
    disponibles = [Path(ruta) for ruta in rutas if ruta is not None and Path(ruta).exists()]
    if not disponibles:
        raise ValueError("No hay resultados para empaquetar.")
    destino = disponibles[0].parent / f"{disponibles[0].stem}_resultados.zip"
    with zipfile.ZipFile(destino, "w", compression=zipfile.ZIP_DEFLATED) as paquete:
        for ruta in disponibles:
            paquete.write(ruta, arcname=sanitizar_nombre(ruta.name))
    return destino


def filtrar_filas(filas: list[dict], *, solo_problemas: bool = False) -> list[dict]:
    """Devuelve las filas ordenadas; opcionalmente oculta las que validaron OK."""
    resultado = list(filas)
    if solo_problemas:
        resultado = [fila for fila in resultado if fila.get("estado") != "OK"]
    return sorted(resultado, key=lambda fila: fila.get("orden", 0))


def mensaje_error(error: BaseException) -> str:
    """Traduce errores técnicos a mensajes seguros y accionables en español."""
    if isinstance(error, ArchivoInvalido):
        return str(error)
    if isinstance(error, pipeline.TraduccionCancelada):
        return "La traducción fue cancelada. La caché queda disponible para reanudar."
    texto = str(error).lower()
    if "401" in texto or "authentication" in texto or "invalid api key" in texto:
        return "La clave de la API es inválida. Revísala fuera de la interfaz."
    if "balance" in texto or "saldo" in texto or "402" in texto:
        return "No hay saldo disponible para realizar la traducción."
    if "model" in texto and ("not found" in texto or "does not exist" in texto):
        return "El modelo seleccionado no existe o no está disponible."
    if isinstance(error, ErrorTransitorio) or any(
            termino in texto for termino in ("timeout", "network", "red", "conex")):
        return "Hubo un error de red. Inténtalo nuevamente."
    if "deepseek_api_key" in texto or "falta" in texto and "key" in texto:
        return "La clave de la API no está definida. Configúrala fuera de la interfaz."
    if "instala el paquete" in texto and "openai" in texto:
        return "Falta una dependencia local. Debes instalar el paquete requerido y reiniciar la interfaz."
    if isinstance(error, ErrorCliente):
        return "No se pudo conectar con el servicio de traducción."
    return "Ocurrió un error inesperado al procesar el archivo."
