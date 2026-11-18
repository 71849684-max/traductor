"""Informe de traduccion (AGENTS.md seccion 1 y 16).

Recoge lo que se tradujo, lo que se omitio y lo que hay que revisar, e incluye
el recuento de parrafos caidos a formato simplificado y de reintentos.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Variantes que conviene revisar aunque no sean errores.
VARIANTES = {
    "portable device": ["wearable", "wearable device", "portable"],
    "countdown": ["countdown timer", "countdown clock", "count-down"],
    "insulin therapy": ["insulin dosing", "insulin treatment", "insulinotherapy"],
    "glucose monitoring": ["glucose control", "glucose tracking"],
    "audit log": ["audit trail", "audit record", "audit registry"],
    "time in range": ["time-in-range", "TIR"],
    "self-monitoring": ["self monitoring", "self-management", "selfmanagement"],
    "heart rate": ["heart-rate", "cardiac rate"],
    "casing": ["housing", "shell"],
    "capsule": ["cartridge"],
    "injector": ["injection device"],
    "pump": ["infusion pump"],
}

# Palabras que casi solo aparecen si quedo texto en espanol.
STOP_ES = {
    "el", "la", "los", "las", "del", "que", "con", "para", "una", "por",
    "su", "al", "como", "más", "entre", "sin", "sobre", "este", "esta",
    "estos", "estas", "también", "pero", "desde", "hasta", "según",
    "durante", "mediante", "cada", "donde", "cuando", "todo", "toda",
    "todos", "todas", "está", "son", "fue", "han", "muy", "se", "lo",
    "le", "les", "ni", "ya", "hay", "sino", "porque", "aunque", "mientras",
}

# Palabras que casi solo aparecen si quedo texto en ingles.
STOP_EN = {
    "the", "of", "and", "to", "in", "is", "for", "with", "that", "this",
    "as", "on", "by", "are", "be", "from", "at", "or", "an", "it", "was",
    "which", "these", "we", "our", "their", "has", "have", "not", "but",
    "can", "may", "also", "such", "more", "than", "between", "using",
    "used", "should", "would", "there", "whether",
}

# Variantes en espanol que conviene revisar aunque no sean errores.
VARIANTES_ES = {
    "dispositivo portátil": ["wearable", "dispositivo vestible", "portátil"],
    "cuenta regresiva": ["temporizador", "contador regresivo"],
    "insulinoterapia": ["terapia con insulina", "tratamiento con insulina",
                        "dosificación de insulina"],
    "monitoreo de glucosa": ["control de glucosa", "monitoreo glucémico"],
    "monitoreo continuo de glucosa": ["MCG", "monitoreo continuo"],
    "registro de auditoría": ["registro de auditoria", "bitácora de auditoría",
                              "pista de auditoría"],
    "tiempo en rango": ["tiempo dentro del rango", "TIR"],
    "automonitoreo": ["autocontrol", "automanejo"],
    "ritmo cardiaco": ["frecuencia cardiaca", "frecuencia cardíaca"],
    "armazón": ["carcasa", "cubierta"],
    "cápsula": ["cartucho"],
    "inyector": ["dispositivo de inyección"],
    "bomba": ["bomba de infusión"],
    "lapiceros conectados": ["plumas conectadas", "bolígrafos conectados"],
}

RE_PALABRA = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+")


def escribir_comparacion(ruta: str | Path, filas: list[dict], meta: dict) -> Path:
    """Tabla por ID con ES | EN | estado, para revisión párrafo a párrafo."""
    ruta = Path(ruta)
    lineas: list[str] = []
    a = lineas.append
    a(f"# Comparación {meta.get('origen', 'es').upper()} → "
      f"{meta.get('destino', 'en').upper()} — {meta.get('archivo', '')}")
    a("")
    fecha = meta.get("fecha") or datetime.now().strftime("%Y-%m-%d %H:%M")
    a(f"- Generado: {fecha}")
    a(f"- Salida: `{meta.get('salida', '')}`")
    a(f"- Cliente: {meta.get('cliente', '')} · modelo {meta.get('modelo', '')}")
    a(f"- Párrafos traducidos: {len([f for f in filas if f.get('traducido')])}"
      f" · con incidencias: "
      f"{len([f for f in filas if f.get('traducido') and f['estado'] != 'OK'])}")
    a("")

    def celda(texto: str) -> str:
        return (texto or "").replace("|", "\\|").replace("\n", " ")

    for f in filas:
        a(f"## {f['id']}")
        a("")
        a("| Campo | Contenido | Revisión del autor |")
        a("|---|---|---|")
        a(f"| {meta.get('origen', 'es').upper()} | {celda(f['es'])} |   |")
        a(f"| {meta.get('destino', 'en').upper()} | {celda(f['en'])} |   |")
        a(f"| Validación | {celda(f['estado'])} |   |")
        a("")
    ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return ruta


def _patron(termino: str):
    """Límites que también cierran tras ')' (p. ej. «... (CGM)»)."""
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(termino)}(?![A-Za-z0-9])",
                      re.IGNORECASE)


def _ocurrencias(aguja: str, pajar: str) -> int:
    return len(_patron(aguja).findall(pajar))


def _sin_canonico(texto: str, termino: str) -> str:
    """Quita el término canónico para no contarlo como su propia variante."""
    return _patron(termino).sub(" ", texto)


def consistencia(glosario: dict, textos: dict[str, str],
                 destino: str = "en") -> dict:
    """Cuenta el uso de cada termino del glosario y busca variantes."""
    unido = "\n".join(textos.values())
    catalogo = VARIANTES if destino == "en" else VARIANTES_ES
    terminos = []
    for origen, objetivo in glosario.get("pares", []):
        resto = _sin_canonico(unido, objetivo)
        variantes = [(v, n) for v in catalogo.get(objetivo.lower(), [])
                     if (n := _ocurrencias(v, resto))]
        n_full = _ocurrencias(objetivo, unido)
        n = n_full
        m = re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", objetivo)
        if m and n_full == 0:
            base, sigla = m.group(1).strip(), m.group(2).strip()
            n_base = _ocurrencias(base, unido)
            if n_base:
                n = n_base
                variantes.append((f"{base} (sin «{sigla}»)", n_base))
        terminos.append({"origen": origen, "objetivo": objetivo,
                         "n": n, "variantes": variantes})

    residual: Counter = Counter()
    ejemplos = []
    basura = STOP_ES if destino == "en" else STOP_EN
    for pid, texto in textos.items():
        palabras = [p.lower() for p in RE_PALABRA.findall(texto)]
        encontradas = sorted({p for p in palabras if p in basura})
        if len(encontradas) >= 2:
            ejemplos.append((pid, encontradas[:8]))
            residual.update(encontradas)
    return {"terminos": terminos, "residual": residual, "ejemplos": ejemplos,
            "destino": destino}


@dataclass
class Informe:
    archivo: str
    salida: str
    origen: str = "es"
    destino: str = "en"
    modelo: str = ""
    cliente: str = ""

    parrafos_totales: int = 0
    parrafos_traducidos: int = 0
    omitidos: int = 0
    protegidos: int = 0
    celdas_traducidas: int = 0

    lotes: int = 0
    reintentos: int = 0
    llamadas: int = 0
    aciertos_cache: int = 0
    tokens_entrada: int = 0
    tokens_salida: int = 0

    formato_simplificado: list[str] = field(default_factory=list)
    a_revisar: list[tuple[str, list[str]]] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    imagenes_con_texto: list[str] = field(default_factory=list)
    erratas: list[str] = field(default_factory=list)
    fidelidad: str = "(no verificada)"
    pdf: str = ""
    paginas_original: int | None = None
    paginas_traducido: int | None = None
    consistencia: dict = field(default_factory=dict)
    problemas: Counter = field(default_factory=Counter)
    validaciones: dict[str, list[str]] = field(default_factory=dict)
    trazas: list[dict] = field(default_factory=list)
    inicio: datetime = field(default_factory=datetime.now)

    def contar_problemas(self, problemas: list[str]) -> None:
        for p in problemas:
            self.problemas[p.split(":")[0]] += 1
        self.problemas["_total"] += len(problemas)

    def escribir(self, ruta: str | Path) -> Path:
        ruta = Path(ruta)
        lineas: list[str] = []
        a = lineas.append
        a(f"# Informe de traducción — {Path(self.archivo).name}")
        a("")
        a(f"- Generado: {self.inicio:%Y-%m-%d %H:%M}")
        a(f"- Salida: `{Path(self.salida).name}`")
        a(f"- Dirección: {self.origen} → {self.destino}")
        a(f"- Cliente: {self.cliente or '(no indicado)'}"
          f"{f' · modelo {self.modelo}' if self.modelo else ''}")
        a("")
        a("## Conteo")
        a("")
        a("| Concepto | Valor |")
        a("|---|---|")
        a(f"| Párrafos totales | {self.parrafos_totales} |")
        a(f"| Traducidos | {self.parrafos_traducidos} |")
        a(f"| Celdas de tabla traducidas | {self.celdas_traducidas} |")
        a(f"| Omitidos (bibliografía, pie, vacíos) | {self.omitidos} |")
        a(f"| Protegidos (solo imágenes/campos) | {self.protegidos} |")
        a(f"| Lotes enviados | {self.lotes} |")
        a(f"| Llamadas al modelo | {self.llamadas} |")
        a(f"| **Reintentos** | **{self.reintentos}** |")
        a(f"| Aciertos de caché | {self.aciertos_cache} |")
        a(f"| Tokens de entrada | {self.tokens_entrada:,} |")
        a(f"| Tokens de salida | {self.tokens_salida:,} |")
        a(f"| **Párrafos con formato simplificado** | **{len(self.formato_simplificado)}** |")
        a(f"| Párrafos a revisar | {len(self.a_revisar)} |")
        a("")
        a(f"**Fidelidad de formato:** {self.fidelidad}")
        if self.pdf:
            a(f"**PDF comparativo:** {self.pdf}")
            if self.paginas_original is not None:
                a(f"**Páginas:** original {self.paginas_original} · "
                  f"traducido {self.paginas_traducido}")
            else:
                a("**Páginas:** no se pudo contar (sin LibreOffice).")
        a("")

        a("## Párrafos con formato simplificado")
        a("")
        if self.formato_simplificado:
            for pid in self.formato_simplificado:
                a(f"- `{pid}`")
        else:
            a("Ninguno: todos los párrafos conservaron sus etiquetas y marcadores.")
        a("")

        a("## Párrafos a revisar")
        a("")
        if self.a_revisar:
            for pid, problemas in self.a_revisar:
                a(f"- `{pid}`")
                for p in problemas:
                    a(f"  - {p}")
        else:
            a("Ninguno.")
        a("")

        a("## Problemas por tipo")
        a("")
        if self.problemas:
            for tipo, n in sorted(self.problemas.items()):
                if tipo.startswith("_"):
                    continue
                a(f"- {tipo}: {n}")
            a(f"- total: {self.problemas.get('_total', 0)}")
        else:
            a("Sin problemas de validación.")
        a("")

        if self.validaciones:
            a("## Validación por párrafo")
            a("")
            for pid in sorted(self.validaciones):
                problemas = self.validaciones[pid]
                if problemas:
                    a(f"- `{pid}`: {len(problemas)} problema(s)")
                    for p in problemas:
                        a(f"  - {p}")
                else:
                    a(f"- `{pid}`: OK")
            a("")

        a("## Consistencia terminológica")
        a("")
        idioma_destino = (self.consistencia.get("destino") or self.destino)
        if self.consistencia.get("terminos"):
            a(f"| Término ({idioma_destino.upper()}) | Veces | Variantes detectadas |")
            a("|---|---:|---|")
            for t in self.consistencia["terminos"]:
                variantes = ", ".join(f"{v} ({n})" for v, n in t["variantes"]) or "—"
                a(f"| {t['objetivo']} | {t['n']} | {variantes} |")
        else:
            a("Sin glosario cargado.")
        a("")
        ejemplos = self.consistencia.get("ejemplos") or []
        residual = self.consistencia.get("residual") or {}
        sobrante = "español" if idioma_destino == "en" else "inglés"
        if ejemplos:
            a(f"**Posible texto en {sobrante} sin traducir:** {len(ejemplos)} párrafo(s).")
            for pid, palabras in ejemplos:
                a(f"- `{pid}`: {', '.join(palabras)}")
            if residual:
                top = ", ".join(f"{p} ({n})" for p, n in residual.most_common(10))
                a(f"- Palabras más repetidas: {top}")
        else:
            a(f"**Posible texto en {sobrante} sin traducir:** ninguno detectado.")
        a("")

        a("## Imágenes con texto")
        a("")
        if self.imagenes_con_texto:
            for x in self.imagenes_con_texto:
                a(f"- {x}")
            a("")
            a("El texto dentro de las imágenes no se traduce: hay que rehacerlas "
              "o reemplazarlas a mano.")
        else:
            a("No se detectaron imágenes con texto.")
        a("")

        a("## Erratas del original (no corregidas)")
        a("")
        if self.erratas:
            for x in self.erratas:
                a(f"- {x}")
        else:
            a("Ninguna registrada.")
        a("")

        a("## Avisos")
        a("")
        if self.avisos:
            for x in self.avisos:
                a(f"- {x}")
        else:
            a("Ninguno.")
        a("")
        a("---")
        a("Revisión humana obligatoria antes de enviar a la revista.")
        ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
        return ruta
