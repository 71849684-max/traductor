"""Pantalla Gradio local para el traductor DOCX.

La lógica de negocio se mantiene en :mod:`traductor.interfaz_logica` para
que esta capa solo describa componentes y conecte eventos.
"""

from __future__ import annotations

import os
import queue
import time

os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"

import gradio as gr

from . import interfaz_logica
from .deepseek import MODELO_POR_DEFECTO, MODELOS

_DIRECCIONES = {
    "Automática": "automatica",
    "Español → Inglés": "es-en",
    "Inglés → Español": "en-es",
}
_MODOS = {
    "Traducción completa": "completa",
    "Prueba parcial": "parcial",
    "Solo estimar": "estimar",
}


def _ruta(valor) -> str | None:
    return str(valor) if valor else None


def _filas_tabla(filas: list[dict], solo_problemas: bool) -> list[list[str]]:
    return [[fila.get("id", ""), fila.get("es", ""), fila.get("en", ""),
             fila.get("estado", "")]
            for fila in interfaz_logica.filtrar_filas(
                filas, solo_problemas=solo_problemas)]


def _resumen(resultado) -> str:
    fidelidad = resultado.fidelidad
    avisos = "<br>".join(resultado.avisos) if resultado.avisos else "Ninguno"
    return (
        "### Resultado\n"
        f"- Párrafos traducidos: **{resultado.parrafos_traducidos}**\n"
        f"- Reintentos: **{resultado.reintentos}**\n"
        f"- Formato simplificado: **{len(resultado.formato_simplificado)}**\n"
        f"- A revisar: **{len(resultado.a_revisar)}**\n"
        f"- Tokens: **{resultado.tokens_total}**\n"
        f"- Fidelidad de formato: **{fidelidad}**\n"
        f"- Avisos: {avisos}"
    )


def _mostrar_estimacion(archivo, direccion, modelo, glosario) -> str:
    if not archivo:
        return "Carga un archivo para obtener la estimación."
    try:
        resultado = interfaz_logica.estimar(
            _ruta(archivo), direccion=_DIRECCIONES[direccion], modelo=modelo,
            glosario=_ruta(glosario), precio_millon=interfaz_logica.precio_millon())
        coste = f" · costo estimado: {resultado.coste_estimado:.4f}"
        return (f"Idioma detectado: **{resultado.origen}** · Dirección: "
                f"**{resultado.origen} → {resultado.destino}**  \n"
                f"Lotes: **{resultado.lotes}** · Tokens estimados: "
                f"**{resultado.tokens_estimados}**{coste}")
    except BaseException as exc:
        return f"**Error:** {interfaz_logica.mensaje_error(exc)}"


def _ejecutar(archivo, direccion, modelo, glosario, modo, ids, confirmar,
              solo_problemas, progreso=gr.Progress()):
    vacio = (None, None, None, None, [], [])
    if not archivo:
        yield ("**Error:** selecciona un archivo .docx.", "", *vacio)
        return
    try:
        clave_direccion = _DIRECCIONES[direccion]
        clave_modo = _MODOS[modo]
        estimacion = interfaz_logica.estimar(
            _ruta(archivo), direccion=clave_direccion, modelo=modelo,
            glosario=_ruta(glosario), precio_millon=interfaz_logica.precio_millon())
        if clave_modo == "estimar":
            texto = _mostrar_estimacion(archivo, direccion, modelo, glosario)
            ejemplo = estimacion.prompt_ejemplo or "No hay lotes para traducir."
            yield ("Estimación completada.", f"{texto}\n\n#### Prompt de ejemplo\n```\n{ejemplo}\n```",
                   *vacio)
            return
        if clave_modo == "completa" and estimacion.tokens_estimados > \
                interfaz_logica.umbral_tokens() and not confirmar:
            yield ("**Confirmación requerida:** la estimación supera el umbral configurado.",
                   _mostrar_estimacion(archivo, direccion, modelo, glosario), *vacio)
            return
        carpeta = interfaz_logica.preparar_carpeta_salida()
        trabajo = interfaz_logica.iniciar_traduccion(
            _ruta(archivo), carpeta, direccion=clave_direccion, modelo=modelo,
            modo=clave_modo, ids=ids, glosario=_ruta(glosario))
        yield ("Traducción iniciada.", _mostrar_estimacion(archivo, direccion, modelo, glosario),
               *vacio)
        ultimo_mensaje = "Procesando lotes…"
        while trabajo.hilo is not None and trabajo.hilo.is_alive():
            try:
                fraccion, ultimo_mensaje = trabajo.progreso.get(timeout=0.15)
                progreso(fraccion, desc=ultimo_mensaje)
                yield (ultimo_mensaje, "", *vacio)
            except queue.Empty:
                time.sleep(0.05)
        resultado = interfaz_logica.esperar_traduccion(trabajo)
        paquete = interfaz_logica.empaquetar_resultados(resultado)
        filas = _filas_tabla(resultado.filas, solo_problemas)
        yield ("Traducción finalizada.", _resumen(resultado), str(resultado.ruta_docx),
               str(resultado.ruta_informe), str(resultado.ruta_comparacion), str(paquete),
               filas, resultado.filas)
    except BaseException as exc:
        yield (f"**Error:** {interfaz_logica.mensaje_error(exc)}", "", *vacio)


def _cancelar() -> str:
    if interfaz_logica.cancelar_traduccion_activa():
        return "Cancelación solicitada: se detendrá después del lote en curso."
    return "No hay una traducción activa para cancelar."


def crear_interfaz() -> gr.Blocks:
    """Construye la aplicación sin abrir un puerto ni iniciar una traducción."""
    with gr.Blocks(title="Traductor DOCX ES ⇄ EN") as demo:
        gr.Markdown("# Traductor de artículos DOCX\nEl formato original se conserva; "
                    "revisa siempre el resultado antes de publicarlo.")
        with gr.Row():
            archivo = gr.File(label="Archivo DOCX (máximo 50 MB)", file_types=[".docx"],
                              type="filepath")
            glosario = gr.File(label="Glosario opcional (.yaml)", file_types=[".yaml", ".yml"],
                                type="filepath")
        with gr.Row():
            direccion = gr.Radio(list(_DIRECCIONES), value="Automática", label="Dirección")
            modelo = gr.Dropdown(list(MODELOS), value=MODELO_POR_DEFECTO, label="Modelo")
            modo = gr.Radio(list(_MODOS), value="Traducción completa", label="Modo")
        ids = gr.Textbox(label="IDs para prueba parcial", placeholder="P0001,P0002")
        confirmar = gr.Checkbox(label="Confirmo continuar si la estimación supera el umbral")
        estimacion = gr.Markdown("Carga un archivo para obtener la estimación.")
        with gr.Row():
            traducir = gr.Button("Traducir", variant="primary")
            cancelar = gr.Button("Cancelar")
        estado = gr.Markdown()
        resumen = gr.Markdown()
        with gr.Row():
            descarga_docx = gr.File(label="Documento traducido")
            descarga_informe = gr.File(label="Informe")
            descarga_comparacion = gr.File(label="Comparación")
            descarga_zip = gr.File(label="ZIP de resultados")
        solo_problemas = gr.Checkbox(label="Solo con problemas")
        filas_estado = gr.State([])
        tabla = gr.Dataframe(headers=["ID", "ES", "EN", "Estado"],
                             datatype=["str", "str", "str", "str"],
                             label="Comparación", interactive=False, wrap=True)

        entradas_estimacion = [archivo, direccion, modelo, glosario]
        archivo.change(_mostrar_estimacion, inputs=entradas_estimacion,
                       outputs=estimacion)
        direccion.change(_mostrar_estimacion, inputs=entradas_estimacion,
                         outputs=estimacion)
        modelo.change(_mostrar_estimacion, inputs=entradas_estimacion,
                      outputs=estimacion)
        glosario.change(_mostrar_estimacion, inputs=entradas_estimacion,
                        outputs=estimacion)
        traducir.click(
            _ejecutar,
            inputs=[archivo, direccion, modelo, glosario, modo, ids, confirmar, solo_problemas],
            outputs=[estado, resumen, descarga_docx, descarga_informe,
                     descarga_comparacion, descarga_zip, tabla, filas_estado],
        )
        cancelar.click(_cancelar, outputs=estado, queue=False)
        solo_problemas.change(
            lambda filas, solo: _filas_tabla(filas, solo),
            inputs=[filas_estado, solo_problemas], outputs=tabla)
    return demo.queue(default_concurrency_limit=1)


def lanzar_interfaz(puerto: int = 7860) -> None:
    """Limpia resultados expirados y expone solo una interfaz local."""
    interfaz_logica.limpiar_salidas_antiguas()
    crear_interfaz().launch(server_name="127.0.0.1", server_port=puerto,
                             share=False, inbrowser=True, show_error=False,
                             allowed_paths=[str(interfaz_logica.SALIDAS_POR_DEFECTO)])
