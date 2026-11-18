"""Cliente de DeepSeek con reintentos y cache, mas un cliente falso.

Nada de este modulo llama a la API por si mismo: solo cuando se instancia
``ClienteDeepSeek`` y se invoca ``traducir``.  Las pruebas y ``--dry-run`` no
hacen ninguna llamada de red.
"""

from __future__ import annotations

import hashlib
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import marcado
from .lotes import parsear_respuesta

BASE_URL = "https://api.deepseek.com"
# Verificado en https://api-docs.deepseek.com/quick_start/pricing
# (2026-10-29): el nombre vigente es "deepseek-flash"; "deepseek-v4-flash"
# sigue aceptandose pero esta retirado y lo sirve el mismo modelo.
MODELO_POR_DEFECTO = "deepseek-flash"
MODELOS = ("deepseek-flash", "deepseek-v4-pro", "deepseek-v4-flash")


def cargar_env(ruta: str | Path = ".env") -> None:
    """Carga pares CLAVE=valor de un .env sin dependencias externas."""
    archivo = Path(ruta)
    if not archivo.exists():
        return
    for linea in archivo.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        clave = clave.strip()
        valor = valor.strip().strip('"').strip("'")
        if clave and clave not in os.environ:
            os.environ[clave] = valor


class ErrorCliente(Exception):
    """Error definitivo (no se reintenta)."""


class ErrorFatal(ErrorCliente):
    """Nada que reintentar: clave inválida, saldo o modelo inexistente.

    Se propaga hasta arriba y aborta la corrida en cuanto ocurre, para no
    seguir llamando ni escribir un .docx a medias.
    """


class ErrorTransitorio(ErrorCliente):
    """429 / 5xx / timeout: se reintenta con espera exponencial."""


@dataclass
class Estadisticas:
    llamadas: int = 0
    reintentos: int = 0
    aciertos_cache: int = 0
    errores: int = 0
    tokens_entrada: int = 0
    tokens_salida: int = 0


class Cache:
    """Cache en disco por hash del prompt (permite --reanudar)."""

    def __init__(self, directorio: str | Path = ".cache"):
        self.directorio = Path(directorio)

    def _ruta(self, clave: str) -> Path:
        return self.directorio / f"{clave}.txt"

    @staticmethod
    def clave(sistema: str, usuario: str, modelo: str,
              de: str | None = None, a: str | None = None) -> str:
        """Hash del prompt. ``de``/``a`` separan la caché por dirección.

        Sin ``de``/``a`` se obtiene la clave histórica, para poder reutilizar
        entradas creadas antes de separar la caché por dirección.
        """
        h = hashlib.sha256()
        h.update(modelo.encode())
        h.update(b"\0")
        if de or a:
            h.update(f"{de or '?'}>{a or '?'}".encode())
            h.update(b"\0")
        h.update(sistema.encode())
        h.update(b"\0")
        h.update(usuario.encode())
        return h.hexdigest()[:32]

    def leer(self, clave: str) -> str | None:
        ruta = self._ruta(clave)
        if ruta.exists():
            return ruta.read_text(encoding="utf-8")
        return None

    def escribir(self, clave: str, texto: str) -> None:
        self.directorio.mkdir(parents=True, exist_ok=True)
        self._ruta(clave).write_text(texto, encoding="utf-8")


@dataclass
class ClienteDeepSeek:
    """Cliente real (compatible OpenAI). No se usa en pruebas."""

    api_key: str | None = None
    modelo: str = MODELO_POR_DEFECTO
    de: str | None = None
    a: str | None = None
    temperatura: float = 0.2
    base_url: str = BASE_URL
    reintentos: int = 3
    espera_base: float = 1.0
    cache: Cache | None = None
    stats: Estadisticas = field(default_factory=Estadisticas)

    def __post_init__(self) -> None:
        cargar_env()
        if self.api_key is None:
            self.api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not self.api_key:
            raise ErrorCliente(
                "falta DEEPSEEK_API_KEY (definela en el entorno o en .env)")
        if self.modelo not in MODELOS:
            raise ErrorCliente(
                f"modelo no permitido: {self.modelo} (usa {', '.join(MODELOS)})")
        self._cliente = None

    def _crear(self):
        if self._cliente is None:
            try:
                from openai import OpenAI
            except ImportError as exc:      # pragma: no cover
                raise ErrorCliente("instala el paquete 'openai'") from exc
            self._cliente = OpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._cliente

    def _llamar(self, sistema: str, usuario: str) -> str:
        try:
            cliente = self._crear()
            respuesta = cliente.chat.completions.create(
                model=self.modelo,
                temperature=self.temperatura,
                messages=[{"role": "system", "content": sistema},
                          {"role": "user", "content": usuario}],
            )
            uso = getattr(respuesta, "usage", None)
            if uso is not None:
                self.stats.tokens_entrada += getattr(uso, "prompt_tokens", 0) or 0
                self.stats.tokens_salida += getattr(uso, "completion_tokens", 0) or 0
            return respuesta.choices[0].message.content or ""
        except Exception as exc:            # pragma: no cover - red real
            estado = getattr(exc, "status_code", None) or \
                getattr(getattr(exc, "response", None), "status_code", None)
            texto = str(exc).lower()
            if estado in (401, 402, 403, 404, 405, 410, 422) or any(
                    s in texto for s in ("authentication", "invalid api key",
                                         "insufficient balance",
                                         "model not found", "does not exist")):
                raise ErrorFatal(str(exc)) from exc
            if estado in (429, 500, 502, 503, 504) or "timeout" in texto \
                    or "rate limit" in texto or "overloaded" in texto:
                raise ErrorTransitorio(str(exc)) from exc
            raise ErrorCliente(str(exc)) from exc

    def traducir(self, sistema: str, usuario: str) -> str:
        clave = Cache.clave(sistema, usuario, self.modelo, self.de, self.a)
        heredada = None
        if self.de or self.a:
            heredada = Cache.clave(sistema, usuario, self.modelo)
        if self.cache is not None:
            guardado = self.cache.leer(clave)
            if guardado is None and heredada is not None:
                # entradas anteriores a separar la caché por dirección
                guardado = self.cache.leer(heredada)
                if guardado is not None:
                    self.cache.escribir(clave, guardado)
            if guardado is not None:
                self.stats.aciertos_cache += 1
                return guardado
        for intento in range(self.reintentos + 1):
            try:
                self.stats.llamadas += 1
                salida = self._llamar(sistema, usuario)
                if self.cache is not None:
                    self.cache.escribir(clave, salida)
                return salida
            except ErrorTransitorio:
                if intento >= self.reintentos:
                    self.stats.errores += 1
                    raise
                self.stats.reintentos += 1
                espera = self.espera_base * (2 ** intento)
                time.sleep(espera + random.uniform(0, espera / 2))
        raise ErrorCliente("sin respuesta tras los reintentos")


RELLENO = ("This section presents a simulated translation produced by the fake "
           "client for testing purposes only, keeping every tag and marker. ")
RELLENO_ES = ("Esta sección presenta una traducción simulada producida por el "
              "cliente falso únicamente para pruebas, conservando todas las "
              "etiquetas y los marcadores del original. ")


class ClienteFalso:
    """Doble de prueba determinista.

    Mantiene etiquetas, marcadores, citas, numeros e identificadores; el texto
    resultante es ingles reconocible y de longitud comparable, para que las
    validaciones se ejerciten de verdad.
    """

    def __init__(self, idioma: str = "en", fallos: int = 0,
                 cache: Cache | None = None, alterar: str | None = None,
                 glosario: dict | None = None):
        self.idioma = idioma
        self.fallos = fallos
        self.cache = cache
        self.alterar = alterar
        self.glosario = glosario or {}
        self.stats = Estadisticas()
        self.llamadas_recibidas: list[tuple[str, str]] = []

    def _glosario_para(self, texto: str) -> list[str]:
        """Traducciones exigidas por el glosario que aparecen en el original."""
        from .validar import contiene_termino

        return [b for a, b in self.glosario.get("pares", [])
                if contiene_termino(texto, a)]

    def _cuerpo(self, txt: str, largo: int | None = None) -> str:
        clave = txt.strip().lower()
        for a, b in self.glosario.get("pares", []):
            if clave == a.lower():
                return b
        desnudo = txt.strip()
        if (len(txt) < 30 or not any(c.isspace() for c in desnudo)
                or desnudo in self.glosario.get("no_traducir", [])):
            return txt
        # de inglés a español el texto crece; al contrario, se acorta
        if largo is None:
            factor = 1.15 if self.idioma == "es" else 0.8
            largo = int(len(txt) * factor)
        relleno = RELLENO_ES if self.idioma == "es" else RELLENO
        objetivo = max(20, largo)
        return (relleno * (objetivo // len(relleno) + 1))[:objetivo]

    def _faltantes(self, plano: str, generado: str) -> list[str]:
        """Tokens y terminos del original que el relleno no conservo.

        Usa las mismas funciones que el validador.  Primero repone terminos,
        literales y codigos (que ya arrastran digitos) y despues calcula el
        deficit real de numeros y citas, para no duplicarlos.
        """
        from collections import Counter

        from .validar import citas, contiene_termino, identificadores, numeros

        candidatos = list(dict.fromkeys(identificadores(plano)))
        candidatos += [b for a, b in self.glosario.get("pares", [])
                       if contiene_termino(plano, a)]
        candidatos += [t for t in self.glosario.get("no_traducir", [])
                       if t in plano]
        anadir = [c for c in candidatos if c not in generado]
        base = generado + " " + " ".join(anadir)

        faltan_num = list((Counter(numeros(plano))
                           - Counter(numeros(base))).elements())
        faltan_cita = list((Counter(citas(plano))
                            - Counter(citas(base))).elements())
        return anadir + faltan_num + faltan_cita

    def _bloque(self, texto: str) -> str:
        items = marcado.parsear(texto)
        if self.alterar == "sin_marcador":
            items = [i for i in items if i[0] != "marcador"][:1] or items
        plano = " ".join(i[2] for i in items if i[0] == "texto" and i[2])

        # Lo que el relleno no puede conservar (numeros, citas, identificadores,
        # terminos del glosario) se estima antes de repartir el presupuesto de
        # longitud; luego se recalcula sobre el texto ya generado para no
        # duplicar lo que quedo intacto en los tramos cortos.
        faltan = self._faltantes(plano, "") if plano.strip() else []
        extra = " ".join(faltan)
        factor = 1.15 if self.idioma == "es" else 0.8
        presupuesto = int(len(plano) * factor)
        disponible = max(20, presupuesto - len(extra)) if plano.strip() else 0
        base = max(len(plano), 1)

        partes: list[str] = []
        textos: list[str] = []
        ultimo = None
        for it in items:
            if it[0] == "marcador":
                partes.append(f"\u27e6{it[1]}\u27e7")
                continue
            tag, txt = it[1], it[2]
            if self.alterar == "sin_etiqueta":
                partes.append(txt)
                textos.append(txt)
                ultimo = len(partes) - 1
                continue
            largo = int(disponible * len(txt) / base) if disponible else None
            cuerpo = self._cuerpo(txt, largo)
            partes.append(f"<{tag}>{cuerpo}</{tag}>")
            textos.append(cuerpo)
            ultimo = len(partes) - 1
        faltan = self._faltantes(plano, " ".join(textos))
        if faltan and ultimo is not None:
            trozo = partes[ultimo]
            corte = trozo.rfind("</")
            partes[ultimo] = (trozo[:corte] + " " + " ".join(faltan)
                              + trozo[corte:])
        return "".join(partes)

    def traducir(self, sistema: str, usuario: str) -> str:
        self.llamadas_recibidas.append((sistema, usuario))
        if self.fallos > 0:
            self.fallos -= 1
            self.stats.reintentos += 1
            raise ErrorTransitorio("fallo simulado")
        self.stats.llamadas += 1
        bloques = parsear_respuesta(usuario)
        partes = []
        for pid, texto in bloques.items():
            if self.alterar == "cita":
                texto = texto.replace("[1]", "[99]")
            partes.append(f"<<<{pid}>>>\n{self._bloque(texto)}")
        return "\n".join(partes) + "\n"
