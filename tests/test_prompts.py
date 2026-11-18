from pathlib import Path

from traductor import prompts
from traductor.lotes import Item

RAIZ = Path(__file__).resolve().parents[1]


def test_glosario_incluye_ssrg():
    g = prompts.cargar_glosario(RAIZ / "glosario.yaml", "es", "en")
    assert "SSRG International Journal of Electrical and Electronics Engineering" in g["no_traducir"]
    assert "Seventh Sense Research Group" in g["no_traducir"]
    assert ("trazabilidad", "traceability") in g["pares"]


def test_glosario_inverso():
    g = prompts.cargar_glosario(RAIZ / "glosario.yaml", "en", "es")
    assert ("traceability", "trazabilidad") in g["pares"]


def test_glosario_sin_archivo():
    assert prompts.cargar_glosario(None, "es", "en") == {"pares": [], "no_traducir": []}
    assert prompts.cargar_glosario(RAIZ / "no-existe.yaml", "es", "en")["pares"] == []


def test_sistema_parametrizado():
    g = prompts.cargar_glosario(RAIZ / "glosario.yaml", "es", "en")
    texto = prompts.sistema("es", "en", g)
    assert "español → inglés" in texto
    assert "inglés académico" in texto
    assert "traceability" in texto
    assert "⟦Xnn⟧" in texto


def test_regla_de_marcadores_y_voz():
    g = prompts.cargar_glosario(RAIZ / "glosario.yaml", "es", "en")
    texto = prompts.sistema("es", "en", g)
    assert ("Los marcadores ⟦Xnn⟧ reemplazan contenido protegido (citas, campos, "
            "imágenes).") in texto
    assert "Cópialos tal cual, en la misma posición dentro de la frase" in texto
    assert "sin corregir ni comentar" in texto
    assert 'no introduzcas "we"' in texto


def test_glosario_nuevos_terminos():
    g = prompts.cargar_glosario(RAIZ / "glosario.yaml", "es", "en")
    for termino in ("lectura_pulso", "registro_carbohidratos",
                    "programacion_insulina", "evento_insulina",
                    "estado_dispositivo", "SIMULADA", "FALLIDA"):
        assert termino in g["no_traducir"]


def test_usuario_con_contexto_y_bloques():
    items = [Item(id="P0012", texto="<b>Hola</b> mundo"),
             Item(id="P0013", texto="Otro párrafo")]
    texto = prompts.usuario(titulo="Título del artículo",
                            seccion="2.1. Enfoque",
                            anterior="Párrafo previo traducido.",
                            items=items)
    assert "Título del artículo" in texto
    assert "2.1. Enfoque" in texto
    assert "Párrafo previo traducido." in texto
    assert "<<<P0012>>>\n<b>Hola</b> mundo" in texto
    assert "<<<P0013>>>\nOtro párrafo" in texto


def test_prompt_completo():
    texto = prompts.prompt_completo("SISTEMA", "USUARIO")
    assert "MENSAJE DE SISTEMA" in texto and "MENSAJE DE USUARIO" in texto
