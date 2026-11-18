"""Núcleo reutilizable: prepara y traduce un .docx.

Aquí vive toda la lógica que antes estaba en ``cli.py``, para que el CLI y la
interfaz web (Fase 2) hagan exactamente lo mismo.  El CLI se limita a leer
argumentos, imprimir y traducir errores a códigos de salida.

Nada de este módulo imprime por consola ni importa ``typer``: los avisos
viajan en :class:`ResultadoTraduccion` y el progreso se comunica con el
callback ``on_progreso``.

    res = traducir_documento("articulo.docx", on_progreso=print)
    print(res.ruta_docx, res.reintentos, res.fidelidad)

La fidelidad de formato sigue siendo la prioridad (AGENTS.md §8): solo se
sustituye el contenido de los ``w:t``.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import informe as modulo_informe
from . import lotes as modulo_lotes
from . import marcado, prompts, secciones, validar, verificar_formato
from .deepseek import (MODELO_POR_DEFECTO, Cache, ClienteDeepSeek, ClienteFalso,
                       ErrorCliente, ErrorFatal, ErrorTransitorio)
from .docx_io import Documento, q

# Firma del callback de progreso: (fracción 0..1, mensaje).
Progreso = Callable[[float, str], None]

# Frases que deben quedar literales dentro de un párrafo traducible.
PROTEGIDOS_ENCABEZADO = (
    "SSRG International Journal of Electrical and Electronics Engineering",
    "Seventh Sense Research Group",
)

ERRATAS_CONOCIDAS = (
    ("Worl Health Organization", "probable errata de «World Health Organization»"),
    ("BL|E", "probable errata de «BLE»"),
    ("4cmurga @continental.edu.pe", "correo con un espacio antes de la arroba"),
)

MAX_REINTENTOS_VALIDACION = 2


class TraduccionCancelada(Exception):
    """La interfaz solicitó detenerse después del lote en curso.

    Se propaga antes de guardar el documento, por lo que los artefactos de
    salida no quedan a medio escribir. La caché del cliente, si está activa,
    permanece disponible para una reanudación posterior.
    """


@dataclass
class ResultadoTraduccion:
    """Todo lo que el CLI (y la interfaz) necesitan mostrar o descargar."""

    # rutas de salida
    ruta_docx: Path | None = None
    ruta_informe: Path | None = None
    ruta_comparacion: Path | None = None
    ruta_respuestas: Path | None = None
    ruta_pdf: Path | None = None

    # dirección y motor
    origen: str = "es"
    destino: str = "en"
    modelo: str = ""
    cliente: str = ""

    # conteos
    parrafos_totales: int = 0
    parrafos_para_traducir: int = 0
    parrafos_traducidos: int = 0
    celdas_traducidas: int = 0
    omitidos: int = 0
    protegidos: int = 0
    lotes: int = 0

    # consumo
    reintentos: int = 0
    llamadas: int = 0
    aciertos_cache: int = 0
    tokens_entrada: int = 0
    tokens_salida: int = 0

    # incidencias
    formato_simplificado: list[str] = field(default_factory=list)
    a_revisar: list[tuple[str, list[str]]] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    inexistentes: list[str] = field(default_factory=list)

    # fidelidad de formato (§8.5)
    fidelidad: str = "(no verificada)"
    diferencias: list[str] = field(default_factory=list)

    # PDF comparativo (opcional, requiere LibreOffice)
    pdf: str = ""
    paginas_original: int | None = None
    paginas_traducido: int | None = None

    # detalle por párrafo: id, es, en, final, estado, validación, orden…
    filas: list[dict] = field(default_factory=list)

    # -- solo para ``dry_run`` ----------------------------------------
    dry_run: bool = False
    hay_lotes: bool = True
    caracteres: int = 0
    tokens_estimados: int = 0
    coste_estimado: float = 0.0
    precio_millon: float = 0.0
    prompt_ejemplo: str = ""

    # objeto completo del informe, por si la interfaz necesita más detalle
    informe: modulo_informe.Informe | None = None

    @property
    def ok(self) -> bool:
        """Sin fallos de fidelidad ni párrafos pendientes de revisión."""
        return not self.diferencias and not self.a_revisar

    @property
    def tokens_total(self) -> int:
        return self.tokens_entrada + self.tokens_salida


# ---------------------------------------------------------------- preparación


def estilos_titulo(doc: Documento) -> frozenset[str]:
    raiz = doc.xml("word/styles.xml")
    ids = set()
    for st in raiz.iter(q("w:style")):
        nombre = st.find(q("w:name"))
        valor = (nombre.get(q("w:val")) if nombre is not None else "") or ""
        if valor.lower().startswith(("heading", "title", "titulo")):
            ids.add(st.get(q("w:styleId")))
    return frozenset(ids)


def titulo_articulo(parrafos, estilos: frozenset[str]) -> str:
    for p in parrafos:
        if p.contenedor == "cuerpo" and len(p.texto) > 20 and (
                p.estilo in estilos or p.estilo.lower().startswith(("title", "titulo"))):
            return p.texto.strip()
    return parrafos[0].texto.strip() if parrafos else ""


def direccion(parrafos, de: str | None, a: str | None) -> tuple[str, str]:
    if de and a:
        return de, a
    muestra = "\n".join(p.texto for p in parrafos
                        if len(p.texto) > 120)[:4000]
    detectado = validar.idioma(muestra) or "es"
    origen = de or detectado
    destino = a or ("en" if origen == "es" else "es")
    return origen, destino


def nombre_salida(archivo: Path, destino: str, *, simulado: bool = False,
                  prueba: bool = False) -> str:
    sufijo = f"_{destino.upper()}"
    if simulado:
        sufijo += "_SIMULADO"
    if prueba:
        sufijo += "_prueba"
    return f"{archivo.stem}{sufijo}.docx"


def leer_solo(solo: str | set[str] | None) -> set[str] | None:
    if solo is None:
        return None
    if isinstance(solo, str):
        ids = {x.strip().upper() for x in solo.split(",") if x.strip()}
        return ids or None
    ids = {str(x).strip().upper() for x in solo if str(x).strip()}
    return ids or None


def preparar(archivo: Path, *,
             de: str | None = None, a: str | None = None,
             glosario_ruta: Path | None = None,
             max_caracteres: int = 4000,
             max_parrafos: int = 12,
             solo: set[str] | None = None):
    """Lee el documento, serializa y agrupa. No toca nada ni llama a nadie."""
    doc = Documento(archivo)
    parrafos = doc.parrafos()
    estilos = estilos_titulo(doc)
    secciones.asignar(parrafos, estilos)
    origen, destino = direccion(parrafos, de, a)

    if glosario_ruta is None:
        for candidato in (Path("glosario.yaml"), archivo.parent / "glosario.yaml"):
            if candidato.exists():
                glosario_ruta = candidato
                break
    glosario = prompts.cargar_glosario(glosario_ruta, origen, destino)
    titulo = titulo_articulo(parrafos, estilos)

    originales = {p.id: p.texto for p in parrafos}
    sers: dict[str, marcado.Serializado] = {}
    items: list[modulo_lotes.Item] = []
    omitidos = 0
    protegidos = 0
    fuera_de_solo = 0
    inexistentes = set(solo or ())

    for par in parrafos:
        proteger = PROTEGIDOS_ENCABEZADO if par.contenedor == "cabecera" else ()
        try:
            ser = marcado.serializar(par.elemento, par.id, proteger=proteger)
        except Exception:
            omitidos += 1
            continue
        sers[par.id] = ser
        if solo is not None and par.id not in solo:
            fuera_de_solo += 1
            continue
        inexistentes.discard(par.id)
        if par.contenedor == "pie":
            omitidos += 1
            continue
        if par.extra.get("protegido"):
            omitidos += 1
            continue
        if ser.protegido or not ser.plano.strip():
            protegidos += 1
            continue
        items.append(modulo_lotes.Item(
            id=par.id, texto=ser.texto, seccion=par.extra.get("seccion", ""),
            titulo=bool(par.extra.get("titulo")),
        ))

    tanda = modulo_lotes.agrupar(items, max_caracteres, max_parrafos)
    return {
        "doc": doc, "parrafos": parrafos, "estilos": estilos,
        "origen": origen, "destino": destino, "glosario": glosario,
        "titulo": titulo, "sers": sers, "items": items, "lotes": tanda,
        "omitidos": omitidos, "protegidos": protegidos,
        "imagenes": imagenes_con_texto(parrafos),
        "erratas": erratas(parrafos),
        "originales": originales,
        "solo": solo,
        "fuera_de_solo": fuera_de_solo,
        "inexistentes": sorted(inexistentes),
    }


def contexto(prep: dict) -> dict:
    return {"sistema": prompts.sistema(prep["origen"], prep["destino"],
                                       prep["glosario"]),
            "titulo": prep["titulo"]}


def mensaje_usuario(prep: dict, lote, anterior: str | None) -> str:
    return prompts.usuario(titulo=prep["titulo"],
                           seccion=lote.seccion,
                           anterior=anterior,
                           items=lote.items)


def aplicar_bloque(par, bloque: str, ser, simplificados: list[str]) -> list[str]:
    """Aplica la traducción; si el marcado no valida, cae al respaldo."""
    try:
        marcado.aplicar(par.elemento, bloque, ser)
        return []
    except marcado.MarcadoInvalido as exc:
        marcado.reconstruir_simple(par.elemento, bloque, ser)
        simplificados.append(par.id)
        return [f"formato simplificado: {exc}"]


# ------------------------------------------------------------------- progreso


def _procesar_lotes(prep: dict, tanda, cliente, ctx: dict, inf,
                    on_progreso: Progreso | None,
                    cancelar: threading.Event | None = None):
    """Traduce los lotes, valida y escribe el texto en el documento."""
    anterior: str | None = None
    modificadas: set[str] = set()
    textos_en: dict[str, str] = {}
    por_id = {p.id: p for p in prep["parrafos"]}
    total = len(tanda)

    for k, lote in enumerate(tanda, 1):
        if cancelar is not None and cancelar.is_set():
            raise TraduccionCancelada("Traducción cancelada por la persona usuaria.")
        esperados = {i.id: prep["sers"][i.id] for i in lote.items}
        elegido: dict[str, str] = {}
        pendientes = dict(esperados)
        ultimos: dict[str, list[str]] = {}
        intentos = 0
        while pendientes and intentos <= MAX_REINTENTOS_VALIDACION:
            if intentos:
                inf.reintentos += 1
            intentos += 1
            actual = modulo_lotes.Lote(
                id=lote.id, seccion=lote.seccion,
                items=[i for i in lote.items if i.id in pendientes])
            try:
                respuesta = cliente.traducir(ctx["sistema"],
                                             mensaje_usuario(prep, actual, anterior))
            except ErrorTransitorio:
                continue
            except ErrorFatal:
                raise                      # clave/saldo/modelo: aborta ya
            except ErrorCliente as exc:
                inf.avisos.append(f"lote {lote.id}: {exc}")
                break
            bloques = modulo_lotes.parsear_respuesta(respuesta)
            inf.trazas.append({"lote": lote.id, "intento": intentos,
                               "ids": [i.id for i in actual.items],
                               "respuesta": respuesta})
            resultados = validar.validar_respuesta(
                bloques, pendientes, destino=prep["destino"],
                glosario=prep["glosario"])
            for pid, res in resultados.items():
                if res.ok:
                    elegido[pid] = bloques[pid]
                    pendientes.pop(pid, None)
                    inf.validaciones[pid] = []
                else:
                    ultimos[pid] = res.problemas

        # los que siguen fallando: se piden uno a uno
        for pid in list(pendientes):
            ser = esperados[pid]
            individual = modulo_lotes.Lote(
                id=f"{lote.id}-{pid}", seccion=lote.seccion,
                items=[i for i in lote.items if i.id == pid])
            bloque = None
            if individual.items:
                try:
                    respuesta = cliente.traducir(
                        ctx["sistema"], mensaje_usuario(prep, individual, anterior))
                    bloque = modulo_lotes.parsear_respuesta(respuesta).get(pid)
                except ErrorFatal:
                    raise
                except ErrorCliente:
                    bloque = None
            if bloque is None:
                inf.contar_problemas(ultimos.get(pid, ["SIN_RESPUESTA"]))
                inf.validaciones[pid] = ["SIN_RESPUESTA"]
                inf.a_revisar.append(
                    (pid, ["sin respuesta válida: se deja el original"]))
                continue
            result = validar.validar_bloque(
                ser.plano, bloque, destino=prep["destino"], ser=ser,
                glosario=prep["glosario"])
            if not result.ok:
                inf.contar_problemas(result.problemas)
                inf.validaciones[pid] = result.problemas
                inf.a_revisar.append((pid, result.problemas))
                continue
            elegido[pid] = bloque
            pendientes.pop(pid, None)
            inf.validaciones[pid] = []
        for pid, problemas in ultimos.items():
            if pid in pendientes and pid not in {p for p, _ in inf.a_revisar}:
                inf.contar_problemas(problemas)

        for pid, bloque in elegido.items():
            ser = esperados[pid]
            par = por_id[pid]
            problemas = aplicar_bloque(par, bloque, ser,
                                       inf.formato_simplificado)
            if problemas:
                inf.a_revisar.append((pid, problemas))
            modificadas.add(par.parte)
            inf.parrafos_traducidos += 1
            if par.contenedor == "tabla":
                inf.celdas_traducidas += 1
            anterior = marcado.texto_plano(bloque)
            textos_en[pid] = anterior

        if on_progreso is not None:
            on_progreso(k / total, f"Lote {k}/{total}")
        if cancelar is not None and cancelar.is_set():
            raise TraduccionCancelada("Traducción cancelada por la persona usuaria.")

    return modificadas, textos_en


# --------------------------------------------------------------- traducción


def traducir_documento(ruta_entrada: str | Path,
                       carpeta_salida: str | Path | None = None, *,
                       de: str | None = None, a: str | None = None,
                       modelo: str = MODELO_POR_DEFECTO,
                       solo: str | set[str] | None = None,
                       glosario: str | Path | None = None,
                       cliente: str = "real",
                       dry_run: bool = False,
                       on_progreso: Progreso | None = None,
                       cancelar: threading.Event | None = None,
                       reanudar: bool = False,
                       pdf: bool = False,
                       max_caracteres: int = 4000,
                       max_parrafos: int = 12,
                       precio_millon: float = 0.0) -> ResultadoTraduccion:
    """Traduce un ``.docx`` y escribe los resultados.

    ``carpeta_salida`` vacío significa «junto al archivo de entrada» (el
    comportamiento del CLI).  El original nunca se modifica.
    """
    archivo = Path(ruta_entrada)
    ids_solo = leer_solo(solo)
    prep = preparar(archivo, de=de, a=a, glosario_ruta=glosario,
                    max_caracteres=max_caracteres, max_parrafos=max_parrafos,
                    solo=ids_solo)
    tanda = prep["lotes"]

    res = ResultadoTraduccion(
        dry_run=dry_run, origen=prep["origen"], destino=prep["destino"],
        modelo=modelo, parrafos_totales=len(prep["parrafos"]),
        parrafos_para_traducir=len(prep["items"]),
        omitidos=prep["omitidos"] + prep["fuera_de_solo"],
        protegidos=prep["protegidos"], lotes=len(tanda),
        inexistentes=prep["inexistentes"], precio_millon=precio_millon,
    )

    if dry_run:
        res.hay_lotes = bool(tanda)
        res.caracteres = sum(l.caracteres for l in tanda)
        res.tokens_estimados = int(res.caracteres / 4)
        res.coste_estimado = res.tokens_estimados / 1_000_000 * precio_millon
        if tanda:
            ctx = contexto(prep)
            ejemplo = next((l for l in tanda
                            if "⟦" in "".join(i.texto for i in l.items)),
                           tanda[0])
            res.prompt_ejemplo = prompts.prompt_completo(
                ctx["sistema"], mensaje_usuario(prep, ejemplo, None))
        return res

    ctx = contexto(prep)

    if cliente == "falso":
        cliente_obj = ClienteFalso(idioma=prep["destino"],
                                   glosario=prep["glosario"])
        etiqueta_cliente = "cliente falso (sin red)"
    else:
        cliente_obj = ClienteDeepSeek(modelo=modelo,
                                      de=prep["origen"], a=prep["destino"],
                                      cache=Cache(".cache") if reanudar else None)
        etiqueta_cliente = "DeepSeek"
    res.cliente = etiqueta_cliente

    nombre = nombre_salida(archivo, prep["destino"],
                           simulado=cliente == "falso",
                           prueba=ids_solo is not None)
    destino_docx = (Path(carpeta_salida) / nombre if carpeta_salida
                    else archivo.with_name(nombre))
    if carpeta_salida:
        Path(carpeta_salida).mkdir(parents=True, exist_ok=True)

    inf = modulo_informe.Informe(
        archivo=str(archivo), salida=str(destino_docx),
        origen=prep["origen"], destino=prep["destino"], modelo=modelo,
        cliente=etiqueta_cliente,
        parrafos_totales=len(prep["parrafos"]),
        omitidos=prep["omitidos"] + prep["fuera_de_solo"],
        protegidos=prep["protegidos"],
        lotes=len(tanda),
    )
    if ids_solo:
        inf.avisos.append(
            f"Prueba parcial: solo se tradujeron {len(prep['items'])} párrafos "
            f"({', '.join(i.id for i in prep['items'])}); el resto queda en el "
            f"idioma original.")
    inf.trazas = []
    res.informe = inf

    modificadas, textos_en = _procesar_lotes(
        prep, tanda, cliente_obj, ctx, inf, on_progreso, cancelar)

    for parte in modificadas:
        prep["doc"].marcar_sucio(parte)

    salida = prep["doc"].guardar(destino_docx)
    inf.salida = str(salida)
    inf.llamadas = cliente_obj.stats.llamadas
    inf.aciertos_cache = cliente_obj.stats.aciertos_cache
    inf.reintentos += cliente_obj.stats.reintentos
    inf.tokens_entrada = cliente_obj.stats.tokens_entrada
    inf.tokens_salida = cliente_obj.stats.tokens_salida

    diferencias = verificar_formato.comparar(archivo, salida)
    inf.fidelidad = ("OK: esqueleto XML idéntico (salvo w:t/w:lang) y partes "
                     "no textuales byte a byte" if not diferencias
                     else "FALLA: " + "; ".join(diferencias))
    inf.imagenes_con_texto = prep["imagenes"]
    inf.erratas = prep["erratas"]
    inf.consistencia = modulo_informe.consistencia(
        prep["glosario"], textos_en, destino=prep["destino"])
    if pdf:
        inf.pdf, inf.paginas_original, inf.paginas_traducido = exportar_pdf(
            archivo, salida)
        if not inf.pdf:
            inf.avisos.append(
                "LibreOffice no disponible: no se generó el PDF comparativo.")

    ruta_informe = salida.with_suffix(salida.suffix + ".informe.md")
    inf.escribir(ruta_informe)

    ruta_comparacion = salida.with_suffix(".comparacion.md")
    res.filas = filas_comparacion(prep, inf, textos_en)
    modulo_informe.escribir_comparacion(
        ruta_comparacion, res.filas,
        {"archivo": archivo.name, "salida": salida.name,
         "cliente": etiqueta_cliente, "modelo": modelo,
         "origen": prep["origen"], "destino": prep["destino"],
         "fecha": inf.inicio.strftime("%Y-%m-%d %H:%M")})

    ruta_respuestas = None
    if ids_solo:
        ruta_respuestas = volcar_trazas(salida, inf)

    # traslado de los resultados al objeto público
    res.ruta_docx = salida
    res.ruta_informe = ruta_informe
    res.ruta_comparacion = ruta_comparacion
    res.ruta_respuestas = ruta_respuestas
    res.parrafos_traducidos = inf.parrafos_traducidos
    res.celdas_traducidas = inf.celdas_traducidas
    res.reintentos = inf.reintentos
    res.llamadas = inf.llamadas
    res.aciertos_cache = inf.aciertos_cache
    res.tokens_entrada = inf.tokens_entrada
    res.tokens_salida = inf.tokens_salida
    res.formato_simplificado = list(inf.formato_simplificado)
    res.a_revisar = list(inf.a_revisar)
    res.avisos = list(inf.avisos)
    res.fidelidad = inf.fidelidad
    res.diferencias = list(diferencias)
    res.pdf = inf.pdf
    res.paginas_original = inf.paginas_original
    res.paginas_traducido = inf.paginas_traducido
    return res


# ---------------------------------------------------------------- auxiliares


def volcar_trazas(salida: Path, inf) -> Path:
    ruta = salida.with_suffix(salida.suffix + ".respuestas.txt")
    lineas = []
    for t in inf.trazas:
        lineas.append(f"===== LOTE {t['lote']} · intento {t['intento']} · "
                      f"IDs {', '.join(t['ids'])} =====")
        lineas.append(t["respuesta"])
        lineas.append("")
    ruta.write_text("\n".join(lineas), encoding="utf-8")
    return ruta


def imagenes_con_texto(parrafos) -> list[str]:
    salida = []
    for k, par in enumerate(parrafos):
        if par.elemento.find(".//" + q("w:drawing")) is None:
            continue
        leyenda = next((p.texto for p in parrafos[k + 1:k + 3]
                        if p.texto.strip().startswith(("Fig", "Figure"))), "")
        salida.append(f"`{par.id}` — {leyenda or '(sin leyenda)'}")
    return salida


def erratas(parrafos) -> list[str]:
    salida = []
    for par in parrafos:
        for fragmento, nota in ERRATAS_CONOCIDAS:
            if fragmento in par.texto:
                salida.append(f"`{par.id}`: «{fragmento}» — {nota}")
    return salida


def exportar_pdf(original: Path, traducido: Path):
    import shutil
    import subprocess

    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return "", None, None
    try:
        for f in (original, traducido):
            subprocess.run([soffice, "--headless", "--convert-to", "pdf",
                            "--outdir", str(f.parent), str(f)],
                           check=True, capture_output=True, timeout=180)
    except Exception:
        return "", None, None
    pdf_orig = original.with_suffix(".pdf")
    pdf_trad = traducido.with_suffix(".pdf")
    if not (pdf_orig.exists() and pdf_trad.exists()):
        return "", None, None
    return (f"{pdf_trad.name} (comparar con {pdf_orig.name})",
            paginas_pdf(pdf_orig), paginas_pdf(pdf_trad))


def paginas_pdf(ruta: Path) -> int | None:
    try:
        datos = Path(ruta).read_bytes()
    except OSError:
        return None
    return len(re.findall(rb"/Type\s*/Page[^s]", datos)) or None


def filas_comparacion(prep: dict, inf, textos_en: dict[str, str]) -> list[dict]:
    """Una fila por párrafo: original, traducción y estado de validación."""
    filas = []
    solo = prep.get("solo")
    for par in prep["parrafos"]:
        es = prep["originales"].get(par.id, "")
        if par.id in textos_en:
            problemas = inf.validaciones.get(par.id) or []
            estado = "OK" if not problemas else "; ".join(problemas)
            en = textos_en[par.id]
        else:
            en = "—"
            ser = prep["sers"].get(par.id)
            if par.contenedor == "pie":
                estado = "no traducido (pie)"
            elif par.extra.get("protegido"):
                estado = "no traducido (bibliografía)"
            elif solo is not None and par.id not in solo:
                estado = "fuera de --solo"
            elif ser is not None and ser.protegido:
                estado = "sin texto traducible (imagen/campo)"
            elif not es.strip():
                estado = "vacío"
            else:
                estado = "no traducido"
        filas.append({"id": par.id, "es": es, "en": en, "estado": estado,
                      "traducido": par.id in textos_en,
                      "orden": par.orden, "final": par.texto,
                      "validacion": inf.validaciones.get(par.id)})
    return filas
