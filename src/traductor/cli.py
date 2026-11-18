"""CLI del traductor (capa delgada sobre ``pipeline``).

    traductor inspeccionar archivo.docx
    traductor archivo.docx [--de es --a en] [--dry-run] [--reanudar]
    traductor archivo.docx --modelo deepseek-v4-pro --glosario otro.yaml --pdf
    traductor archivo.docx --cliente falso        (pruebas, sin red)

``--cliente falso`` usa un doble determinista: sirve para ensayar todo el flujo
sin llamar a la API.

Toda la lógica vive en :mod:`traductor.pipeline`; aquí solo se leen argumentos,
se imprime el resultado y se traducen los errores a códigos de salida.
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from . import pipeline
from .deepseek import MODELO_POR_DEFECTO, ErrorCliente

app = typer.Typer(add_completion=False,
                  help="Traduce artículos .docx ES⇄EN conservando el formato.")


def ejecutar(archivo: Path, *,
             de: str | None = None, a: str | None = None,
             modelo: str = MODELO_POR_DEFECTO,
             dry_run: bool = False, reanudar: bool = False,
             glosario_ruta: Path | None = None, pdf: bool = False,
             cliente_nombre: str = "real",
             max_caracteres: int = 4000, max_parrafos: int = 12,
             precio_millon: float = 0.0,
             solo: str | None = None):
    """Traduce e imprime el resumen. Devuelve ``ResultadoTraduccion``."""
    res = pipeline.traducir_documento(
        archivo, de=de, a=a, modelo=modelo, solo=solo, glosario=glosario_ruta,
        cliente=cliente_nombre, dry_run=dry_run, reanudar=reanudar, pdf=pdf,
        max_caracteres=max_caracteres, max_parrafos=max_parrafos,
        precio_millon=precio_millon)

    if res.inexistentes:
        typer.secho(f"Aviso: no existen estos IDs: {', '.join(res.inexistentes)}",
                    err=True)

    if dry_run:
        typer.echo(f"Lotes: {res.lotes} · párrafos: "
                   f"{res.parrafos_para_traducir} · "
                   f"caracteres: {res.caracteres:,}")
        typer.echo(f"Dirección: {res.origen} → {res.destino} · "
                   f"modelo: {res.modelo}")
        if precio_millon:
            typer.echo(f"Tokens de entrada (aprox.): {res.tokens_estimados:,} · "
                       f"coste estimado: {res.coste_estimado:.4f} (a "
                       f"{precio_millon:.2f}/M tok)")
        else:
            typer.echo(f"Tokens de entrada (aprox.): {res.tokens_estimados:,} · "
                       f"coste: configura --precio-millon para estimarlo")
        if not res.hay_lotes:
            typer.echo("No hay nada que traducir.")
            return res
        typer.echo("\n" + "=" * 72)
        typer.echo("PROMPT COMPLETO DE UN LOTE DE EJEMPLO")
        typer.echo("=" * 72 + "\n")
        typer.echo(res.prompt_ejemplo)
        return res

    typer.echo(f"Listo: {res.ruta_docx.name}")
    typer.echo(f"Fidelidad: {res.fidelidad}")
    typer.echo(f"Traducidos: {res.parrafos_traducidos} · "
               f"reintentos: {res.reintentos} · "
               f"formato simplificado: {len(res.formato_simplificado)}")
    if res.tokens_entrada or res.tokens_salida:
        typer.echo(f"Tokens: entrada {res.tokens_entrada:,} · "
                   f"salida {res.tokens_salida:,} · "
                   f"total {res.tokens_total:,}")
    typer.echo(f"Informe: {res.ruta_informe.name}")

    if res.ruta_respuestas is not None:
        _mostrar_comparacion(res.filas, pipeline.leer_solo(solo) or set())
    return res


def _mostrar_comparacion(filas: list[dict], ids: set[str]) -> None:
    por_id = {f["id"]: f for f in filas}
    typer.echo("\n" + "=" * 78)
    typer.echo("ORIGINAL vs TRADUCCIÓN")
    typer.echo("=" * 78)
    for pid in sorted(ids, key=lambda x: por_id[x]["orden"] if x in por_id else 0):
        if pid not in por_id:
            continue
        fila = por_id[pid]
        validacion = fila["validacion"]
        estado = ("OK" if validacion == [] else
                  "NO TRADUCIDO" if validacion is None else
                  "; ".join(validacion))
        typer.echo(f"\n--- {pid} ---  validación: {estado}")
        typer.echo(f"ES: {fila['es']}")
        typer.echo(f"EN: {fila['final']}")


@app.command("inspeccionar")
def inspeccionar(archivo: Path = typer.Argument(..., exists=True)):
    """Muestra la estructura del documento (Paso 0)."""
    from .inspeccion import main as inspeccion_main
    codigo = inspeccion_main([str(archivo)])
    raise typer.Exit(code=codigo)


@app.command("interfaz")
def interfaz(puerto: int = typer.Option(7860, "--puerto", min=1, max=65535)):
    """Abre la interfaz web local para cargar y traducir un DOCX."""
    from .interfaz import lanzar_interfaz
    lanzar_interfaz(puerto)


@app.command("traducir")
def traducir(archivo: Path = typer.Argument(..., exists=True),
             de: str = typer.Option(None, "--de", help="idioma de origen"),
             a: str = typer.Option(None, "--a", help="idioma de destino"),
             modelo: str = typer.Option(MODELO_POR_DEFECTO, "--modelo"),
             dry_run: bool = typer.Option(False, "--dry-run",
                                          help="lotes y prompt, sin llamar a la API"),
             reanudar: bool = typer.Option(False, "--reanudar",
                                           help="usa la caché si se interrumpió"),
             glosario: Path = typer.Option(None, "--glosario"),
             pdf: bool = typer.Option(False, "--pdf",
                                      help="PDF comparativo con LibreOffice"),
             cliente: str = typer.Option("real", "--cliente",
                                         help="real | falso (sin red)"),
             solo: str = typer.Option(None, "--solo",
                                      help="traduce solo estos IDs (coma) y escribe _prueba"),
             max_caracteres: int = typer.Option(4000, "--max-caracteres"),
             max_parrafos: int = typer.Option(12, "--max-parrafos"),
             precio_millon: float = typer.Option(0.0, "--precio-millon")):
    """Traduce un .docx y escribe <nombre>_<EN|ES>.docx + informe."""
    try:
        ejecutar(archivo, de=de, a=a, modelo=modelo, dry_run=dry_run,
                 reanudar=reanudar, glosario_ruta=glosario, pdf=pdf,
                 cliente_nombre=cliente, max_caracteres=max_caracteres,
                 max_parrafos=max_parrafos, precio_millon=precio_millon,
                 solo=solo)
    except FileNotFoundError as exc:
        typer.secho(f"No existe: {exc}", err=True, fg="red")
        raise typer.Exit(2)
    except ErrorCliente as exc:
        typer.secho(f"Error del cliente: {exc}", err=True, fg="red")
        raise typer.Exit(3)


def main(argv: list[str] | None = None) -> None:
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in ("inspeccionar", "traducir", "interfaz", "-h", "--help"):
        argv.insert(0, "traducir")
    app(args=argv)


if __name__ == "__main__":
    main()
