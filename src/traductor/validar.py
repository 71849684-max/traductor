"""Validaciones por parrafo (AGENTS.md seccion 11).

Cada funcion devuelve una lista de problemas (vacia = correcto).  Los codigos
son estables para poder contarlos en el informe.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import marcado

RE_CITA = re.compile(r"\[\s*\d+(?:\s*[-\u2013,]\s*\d+)*\s*\]")
# admite sufijos legitimos en ingles ("1990s", "1st", "3rd") sin contar como
# numero distinto, y sigue excluyendo digitos pegados a palabras ("Marin1")
RE_NUMERO = re.compile(
    r"(?<![A-Za-z0-9_])(\d+(?:[.,]\d+)?)(?:s|st|nd|rd|th)?(?![A-Za-z0-9_])")
RE_IDENTIFICADOR = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b")
RE_CODIGO = re.compile(r"\b[A-Z]{2,4}-\d{2,}\b")
RE_PALABRA = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+")

MINIMO_LONGITUD = 0.5
MAXIMO_LONGITUD = 1.8
MINIMO_IDIOMA = 40          # caracteres para fiarse de la deteccion

_detector = None


def _normalizar_numero(t: str) -> str:
    return t.replace(",", ".")


def citas(texto: str) -> list[str]:
    return [re.sub(r"\s+", "", m) for m in RE_CITA.findall(texto)]


def numeros(texto: str) -> list[str]:
    return [_normalizar_numero(m.group(1)) for m in RE_NUMERO.finditer(texto)]


def identificadores(texto: str) -> list[str]:
    return RE_IDENTIFICADOR.findall(texto) + RE_CODIGO.findall(texto)


def idioma(texto: str) -> str | None:
    """'es' | 'en' | None. Usa lingua si esta disponible."""
    global _detector
    if len(texto.strip()) < MINIMO_IDIOMA:
        return None
    try:
        if _detector is None:
            from lingua import Language, LanguageDetectorBuilder

            _detector = (LanguageDetectorBuilder
                         .from_languages(Language.SPANISH, Language.ENGLISH)
                         .build())
        valor = _detector.detect_language_of(texto)
        if valor is None:
            return None
        return {"SPANISH": "es", "ENGLISH": "en"}.get(valor.name,
                                                      valor.name.lower()[:2])
    except Exception:
        return _heuristica(texto)


_PALABRAS_ES = {"de", "la", "el", "los", "las", "que", "en", "y", "con", "para",
                "una", "del", "por", "se", "su", "al", "es", "como", "más",
                "entre", "sin", "sobre", "este", "esta", "también", "pero"}
_PALABRAS_EN = {"the", "of", "and", "to", "in", "is", "for", "with", "that",
                "this", "as", "on", "by", "are", "be", "from", "at", "or",
                "an", "it", "was", "which", "these"}


def _heuristica(texto: str) -> str | None:
    palabras = [p.lower() for p in RE_PALABRA.findall(texto)]
    if not palabras:
        return None
    es = sum(1 for p in palabras if p in _PALABRAS_ES)
    en = sum(1 for p in palabras if p in _PALABRAS_EN)
    if es == en:
        return None
    return "es" if es > en else "en"


@dataclass
class Resultado:
    problemas: list[str] = field(default_factory=list)
    detalles: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.problemas


def validar_bloque(origen: str, traduccion: str, *, destino: str,
                   ser: marcado.Serializado | None = None,
                   glosario: dict | None = None) -> Resultado:
    """Valida un bloque traducido contra su original serializado."""
    res = Resultado()
    if ser is None:
        res.problemas.append("SIN_SERIALIZADO")
        return res

    # etiquetas y marcadores (marcado.validar)
    for p in marcado.validar(traduccion, ser):
        codigo = ("MARCADOR_FALTANTE" if "faltan" in p else
                  "MARCADOR_EXTRA" if "desconocidos" in p else
                  "MARCADOR_ORDEN" if "orden" in p else
                  "ETIQUETA" if "etiqueta" in p else
                  "VACIO" if "vacia" in p or "ninguna" in p else "MARCADO")
        res.problemas.append(f"{codigo}: {p}")

    nuevo = marcado.texto_plano(traduccion)
    if not nuevo.strip() and ser.plano.strip():
        res.problemas.append("TRADUCCION_VACIA")
        return res

    # los tokens se comparan con las piezas separadas por espacio: un numero
    # pegado al final de un run ("...Marin1") no debe perderse ni confundirse
    origen_tokens = " ".join(pz.texto for pz in ser.piezas if pz.tipo == "texto")
    nuevo_tokens = " ".join(it[2] for it in marcado.parsear(traduccion)
                            if it[0] == "texto" and it[2])

    # citas
    if sorted(citas(origen_tokens)) != sorted(citas(nuevo_tokens)):
        res.problemas.append(
            f"CITA_ALTERADA: {citas(origen_tokens)} -> {citas(nuevo_tokens)}")

    # numeros
    if sorted(numeros(origen_tokens)) != sorted(numeros(nuevo_tokens)):
        faltan = [n for n in numeros(origen_tokens)
                  if n not in numeros(nuevo_tokens)]
        res.problemas.append(f"NUMERO_ALTERADO: faltan {faltan[:8]}")

    # identificadores y codigos
    faltan_id = [i for i in identificadores(origen_tokens)
                 if i not in nuevo_tokens]
    if faltan_id:
        res.problemas.append(f"IDENTIFICADOR_ALTERADO: {faltan_id[:8]}")

    # terminos no traducibles del glosario
    if glosario:
        for termino in glosario.get("no_traducir", []):
            if termino in ser.plano and termino not in nuevo:
                res.problemas.append(f"NO_TRADUCIR_ALTERADO: {termino}")
        for a, b in glosario.get("pares", []):
            # El término puede llevar sigla entre paréntesis: se acepta también
            # su forma base ("continuous glucose monitoring (CGM)" ↔ "... systems").
            if not contiene_termino(ser.plano, a):
                continue
            if not any(v.lower() in nuevo.lower() for v in _variantes_termino(b)):
                res.problemas.append(f"GLOSARIO: '{a}' deberia aparecer como '{b}'")

    # longitud
    if ser.plano.strip():
        ratio = len(nuevo) / max(len(ser.plano), 1)
        res.detalles["ratio"] = round(ratio, 2)
        if not (MINIMO_LONGITUD <= ratio <= MAXIMO_LONGITUD):
            res.problemas.append(f"LONGITUD: {ratio:.2f}x")

    # idioma (se omite en listas de nombres propios: no son texto traducible)
    if _es_lista_de_nombres(nuevo):
        res.detalles["idioma"] = "omitido (nombres propios)"
    else:
        detectado = idioma(nuevo)
        res.detalles["idioma"] = detectado
        if detectado and detectado != destino:
            res.problemas.append(
                f"IDIOMA: se esperaba {destino}, se detecto {detectado}")

    return res


def _variantes_termino(objetivo: str) -> list[str]:
    """'continuous glucose monitoring (CGM)' admite tambien la base o la sigla."""
    variantes = [objetivo]
    m = re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", objetivo)
    if m:
        variantes.extend([m.group(1).strip(), m.group(2).strip()])
    return variantes


def _patron(termino: str):
    """Limites de palabra que tambien funcionan con terminos que acaban en ')'.

    ``\\b`` no cierra despues de un parentesis, asi que '(CGM)' nunca casaba.
    """
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(termino)}(?![A-Za-z0-9])",
                      re.IGNORECASE)


def contiene_termino(texto: str, termino: str) -> bool:
    """True si el texto contiene el termino (o su forma base sin la sigla)."""
    return any(_patron(v).search(texto) for v in _variantes_termino(termino))


def _es_lista_de_nombres(texto: str) -> bool:
    palabras = RE_PALABRA.findall(texto)
    if len(palabras) < 4:
        return False
    resto = palabras[1:]
    mayusculas = sum(1 for p in resto if p[:1].isupper())
    return mayusculas / len(resto) >= 0.6


def validar_respuesta(respuesta: dict[str, str], esperados: dict[str, marcado.Serializado],
                      *, destino: str, glosario: dict | None = None) -> dict[str, Resultado]:
    """Valida todos los bloques de una respuesta y detecta IDs ausentes."""
    salida: dict[str, Resultado] = {}
    for pid, ser in esperados.items():
        if pid not in respuesta:
            salida[pid] = Resultado(["ID_AUSENTE"])
            continue
        salida[pid] = validar_bloque(ser.plano, respuesta[pid], destino=destino,
                                     ser=ser, glosario=glosario)
    for pid in respuesta:
        if pid not in esperados:
            salida[pid] = Resultado(["ID_DESCONOCIDO"])
    return salida
