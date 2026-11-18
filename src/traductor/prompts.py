"""Prompts del traductor (AGENTS.md seccion 10).

El prompt de sistema es fijo (parametrizado) y el de usuario lleva el
contexto de solo lectura: titulo del articulo, seccion, ultimo parrafo
traducido y glosario.
"""

from __future__ import annotations

from pathlib import Path

import yaml

SISTEMA = """Eres un traductor profesional de artículos científicos de ingeniería y salud digital, {origen} → {destino}.

Tarea: traduce cada bloque delimitado por <<<ID>>>. Devuelve los mismos delimitadores e IDs, en el mismo orden, sin texto adicional.

Reglas:
1. Traduce el sentido con el estilo de un artículo científico en {variante}. Voz clara, terminología técnica precisa, sin literalismos.
2. Usa el contexto (título del artículo, sección, párrafo previo) para resolver ambigüedades y mantén el mismo término para el mismo concepto en todo el texto.
3. No agregues, quites ni resumas información. No expliques ni comentes.
4. Conserva exactamente las etiquetas <b> <i> <u> <sup> <sub> y las <fN> (misma cantidad, mismo orden) y los marcadores ⟦Xnn⟧ (sin traducirlos ni moverlos fuera de su frase).
5. No cambies las citas [n], los números, las unidades, los códigos (RF-01, 05, 250), ni los identificadores con guion bajo.
6. Adapta convenciones numéricas y tipográficas al idioma destino (decimales, "14 %" ↔ "14%"). {convenciones}
7. Respeta el glosario y la lista de términos que no se traducen.
{glosario}
8. Títulos y leyendas: mantén su numeración ("2.3.", "Tabla 1" ↔ "Table 1") y, si el original está todo en mayúsculas, devuélvelo todo en mayúsculas.
9. Si un fragmento ya está en el idioma destino, devuélvelo igual.
10. Los marcadores ⟦Xnn⟧ reemplazan contenido protegido (citas, campos, imágenes). Cópialos tal cual, en la misma posición dentro de la frase, y traduce normalmente el texto que los rodea.
11. Si el original tiene erratas o redacción descuidada, traduce el sentido sin corregir ni comentar.
12. Mantén la voz del original (impersonal o pasiva); no introduzcas "we".
"""

VARIANTES = {"en": "inglés académico (variante estadounidense)",
             "es": "español neutro latinoamericano (Perú)"}
NOMBRES = {"es": "español", "en": "inglés"}

# Convenciones del idioma de destino; se inyectan en la regla 6.
CONVENCIONES = {
    "en": ("Convenciones del inglés: punto decimal (35.37), sin espacio antes "
           "del signo de porcentaje (14%), coma para los miles (1,000) y "
           "puntuación inglesa."),
    "es": ("Convenciones del español (Perú): punto decimal (35.37), espacio "
           "antes del signo de porcentaje (14 %), sin separador de miles en "
           "cifras de cuatro dígitos (1000) y signos de apertura en las "
           "interrogaciones y exclamaciones (¿…?, ¡…!). Mantén la voz "
           "impersonal: no introduzcas «nosotros»."),
}


def _pares(origen: str, destino: str, datos: dict) -> list[tuple[str, str]]:
    """Pares (origen, destino) del YAML, sin duplicados.

    El bloque explícito de la dirección manda sobre la inversión del bloque
    contrario (AGENTS.md §12), de modo que los overrides se respetan.
    """
    mapa: dict[str, tuple[str, str]] = {}
    inversa = datos.get(f"{destino}-{origen}") or {}
    if isinstance(inversa, dict):
        # k está en el idioma destino y v en el de origen: se intercambian.
        for k, v in inversa.items():
            a, b = str(v).strip(), str(k).strip()
            if a and b:
                mapa[a.casefold()] = (a, b)
    directa = datos.get(f"{origen}-{destino}") or {}
    if isinstance(directa, dict):
        for k, v in directa.items():
            a, b = str(k).strip(), str(v).strip()
            if a and b:
                mapa[a.casefold()] = (a, b)
    return list(mapa.values())


def cargar_glosario(ruta: str | Path | None, origen: str, destino: str) -> dict:
    """Carga el YAML y devuelve ``{pares: [(origen, destino)], no_traducir: []}``."""
    vacio = {"pares": [], "no_traducir": []}
    if ruta is None:
        return vacio
    ruta = Path(ruta)
    if not ruta.exists():
        return vacio
    datos = yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}
    return {
        "pares": _pares(origen, destino, datos),
        "no_traducir": [str(x) for x in (datos.get("no_traducir") or [])],
    }


def glosario_texto(glosario: dict) -> str:
    lineas = []
    if glosario.get("pares"):
        lineas.append("Glosario obligatorio:")
        lineas.extend(f"  {a} = {b}" for a, b in glosario["pares"])
    if glosario.get("no_traducir"):
        lineas.append("No traducir (copiar literal):")
        lineas.extend(f"  {x}" for x in glosario["no_traducir"])
    return "\n".join(lineas) if lineas else "(sin glosario)"


def sistema(origen: str, destino: str, glosario: dict) -> str:
    return SISTEMA.format(
        origen=NOMBRES.get(origen, origen),
        destino=NOMBRES.get(destino, destino),
        variante=VARIANTES.get(destino, destino),
        convenciones=CONVENCIONES.get(destino, ""),
        glosario=glosario_texto(glosario),
    )


def bloques(items) -> str:
    """Bloques ``<<<ID>>>`` de un lote."""
    return "\n".join(f"<<<{i.id}>>>\n{i.texto}" for i in items)


def usuario(*, titulo: str, seccion: str, anterior: str | None,
            items) -> str:
    partes = ["Contexto de solo lectura (no lo traduzcas):"]
    partes.append(f"  Artículo: {titulo or '(sin título)'}")
    partes.append(f"  Sección: {seccion or '(sin sección)'}")
    if anterior:
        recorte = anterior if len(anterior) <= 400 else anterior[:400] + " […]"
        partes.append(f"  Último párrafo ya traducido: {recorte}")
    partes.append("")
    partes.append("Traduce estos bloques y devuélvelos con el mismo <<<ID>>>:")
    partes.append(bloques(items))
    return "\n".join(partes)


def prompt_completo(sistema_txt: str, usuario_txt: str) -> str:
    """Texto unico para ``--dry-run`` (lo que se enviaria a la API)."""
    return (
        "===== MENSAJE DE SISTEMA =====\n"
        f"{sistema_txt}\n\n"
        "===== MENSAJE DE USUARIO =====\n"
        f"{usuario_txt}\n"
    )
