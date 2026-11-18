"""Pruebas sin servidor para el adaptador visual de Gradio."""

import gradio as gr

from traductor import interfaz


def test_crea_una_interfaz_gradio():
    demo = interfaz.crear_interfaz()

    assert isinstance(demo, gr.Blocks)
