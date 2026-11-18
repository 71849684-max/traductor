"""Glosario: inversión ``es-en`` → ``en-es``, conflictos y control de deriva.

AGENTS.md §12: para ``en-es`` el agente invierte las entradas de ``es-en`` y
agrega los términos que no se invierten bien.  El bloque ``en-es`` de
``glosario.yaml`` se genera con :func:`construir_en_es`; una prueba comprueba
que el archivo está al día.

Uso::

    python -m traductor.glosario            # reporte: conflictos y deriva
    python -m traductor.glosario --yaml glosario.yaml
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

# Términos que la inversión automática no resuelve bien y se fijan a mano.
# La clave debe coincidir con la entrada inglesa del bloque invertido.
OVERRIDES_EN_ES: dict[str, str] = {
    # «primary tube» / «secondary tube» invierten a su forma larga; el término
    # genérico se fija explícitamente.
    "tube": "manguera",
    # «armazón» se invierte a «casing»; se deja explícito por claridad.
    "casing": "armazón",
    "primary tube": "manguera primaria",
    "secondary tube": "manguera secundaria",
    # Aparece en las palabras clave del artículo y no está en es-en.
    "mobile application": "aplicación móvil",
    # El original en español no usa sigla: no se agrega «(MCG)».
    "continuous glucose monitoring (CGM)": "monitoreo continuo de glucosa",
}


def invertir(tabla: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Invierte ``{es: en}`` en ``{en: es}``.

    Si dos términos en español comparten la misma traducción al inglés se
    conserva el primero (orden del archivo) y se reporta el conflicto.
    """
    inverso: dict[str, str] = {}
    conflictos: list[str] = []
    for es, en in (tabla or {}).items():
        es, en = str(es).strip(), str(en).strip()
        if not es or not en:
            continue
        if en in inverso:
            if inverso[en] != es:
                conflictos.append(
                    f"«{en}» ya venía de «{inverso[en]}»; se descarta «{es}»")
            continue
        inverso[en] = es
    return inverso, conflictos


def construir_en_es(datos: dict) -> tuple[dict[str, str], list[str]]:
    """Bloque ``en-es`` completo: inversión de ``es-en`` + overrides."""
    inverso, conflictos = invertir(datos.get("es-en") or {})
    for clave, valor in OVERRIDES_EN_ES.items():
        inverso[clave] = valor
    return dict(sorted(inverso.items(), key=lambda kv: kv[0].lower())), conflictos


def revisar(datos: dict) -> dict:
    """Compara el bloque ``en-es`` del YAML con el que se generaría."""
    esperado, conflictos = construir_en_es(datos)
    actual = {str(k): str(v) for k, v in (datos.get("en-es") or {}).items()}
    faltan = sorted(k for k in esperado if k not in actual)
    sobran = sorted(k for k in actual if k not in esperado)
    distintos = sorted(k for k in esperado
                       if k in actual and actual[k] != esperado[k])
    return {"conflictos": conflictos, "faltan": faltan, "sobran": sobran,
            "distintos": distintos, "al_dia": not (faltan or sobran or distintos),
            "esperado": esperado}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ruta = Path("glosario.yaml")
    if "--yaml" in argv:
        ruta = Path(argv[argv.index("--yaml") + 1])
    datos = yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}
    informe = revisar(datos)

    print(f"Glosario: {ruta}")
    print(f"Pares es-en: {len(datos.get('es-en') or {})}")
    print(f"Pares en-es generados: {len(informe['esperado'])}")
    print(f"Conflictos de inversión: {len(informe['conflictos'])}")
    for c in informe["conflictos"]:
        print(f"  - {c}")
    if informe["al_dia"]:
        print("Bloque en-es: al día.")
        return 0
    print("Bloque en-es: DESACTUALIZADO")
    for k in informe["faltan"]:
        print(f"  - falta: {k}")
    for k in informe["sobran"]:
        print(f"  - sobra: {k}")
    for k in informe["distintos"]:
        print(f"  - distinto: {k}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
