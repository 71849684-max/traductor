import pytest

from traductor import deepseek, marcado
from traductor.deepseek import (Cache, ClienteDeepSeek, ClienteFalso,
                                ErrorCliente, ErrorFatal, ErrorTransitorio)


def test_falso_conserva_ids_y_marcadores():
    cliente = ClienteFalso()
    usuario = ("Contexto\n<<<P0001>>>\nTexto <b>uno</b>\u27e6X01\u27e7\n"
               "<<<P0002>>>\nTexto dos")
    salida = cliente.traducir("sistema", usuario)
    bloques = __import__("traductor.lotes", fromlist=["x"]).parsear_respuesta(salida)
    assert set(bloques) == {"P0001", "P0002"}
    assert "\u27e6X01\u27e7" in bloques["P0001"]
    assert "<b>" in bloques["P0001"]


def test_falso_produce_texto_en_ingles():
    cliente = ClienteFalso()
    ser = marcado.serializar(
        __import__("lxml.etree", fromlist=["x"]).fromstring(
            '<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:r><w:rPr/><w:t>Este es un parrafo de prueba con suficiente longitud para '
            'que la deteccion de idioma funcione bien.</w:t></w:r></w:p>'))
    salida = cliente._bloque(ser.texto)
    assert "simulated" in salida.lower()


def test_cache_escribe_y_lee(tmp_path):
    cache = Cache(tmp_path)
    clave = Cache.clave("s", "u", "deepseek-v4-flash")
    assert cache.leer(clave) is None
    cache.escribir(clave, "resultado")
    assert cache.leer(clave) == "resultado"


def test_cliente_real_sin_clave(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(deepseek, "cargar_env", lambda *a, **k: None)
    with pytest.raises(ErrorCliente):
        ClienteDeepSeek(api_key=None)


def test_modelo_no_permitido():
    with pytest.raises(ErrorCliente):
        ClienteDeepSeek(api_key="x", modelo="deepseek-chat")


def _cliente_con_fallos(monkeypatch, fallos, tmp_path):
    cliente = ClienteDeepSeek(api_key="x", espera_base=0, cache=None)
    estado = {"n": 0}

    def _llamar(sistema, usuario):
        estado["n"] += 1
        if estado["n"] <= fallos:
            raise ErrorTransitorio("429 simulado")
        return "<<<P0001>>>\nok"

    monkeypatch.setattr(cliente, "_llamar", _llamar)
    return cliente


def test_reintentos_con_espera_exponencial(monkeypatch):
    cliente = _cliente_con_fallos(monkeypatch, fallos=2, tmp_path=None)
    assert cliente.traducir("s", "u") == "<<<P0001>>>\nok"
    assert cliente.stats.reintentos == 2
    assert cliente.stats.llamadas == 3


def test_error_persistente_agota_reintentos(monkeypatch):
    cliente = _cliente_con_fallos(monkeypatch, fallos=99, tmp_path=None)
    cliente.reintentos = 2
    with pytest.raises(ErrorTransitorio):
        cliente.traducir("s", "u")
    assert cliente.stats.reintentos == 2


def test_cache_evita_llamadas(monkeypatch, tmp_path):
    cache = Cache(tmp_path)
    cliente = _cliente_con_fallos(monkeypatch, fallos=0, tmp_path=tmp_path)
    cliente.cache = cache
    assert cliente.traducir("s", "u") == "<<<P0001>>>\nok"
    llamadas = cliente.stats.llamadas
    assert cliente.traducir("s", "u") == "<<<P0001>>>\nok"
    assert cliente.stats.llamadas == llamadas
    assert cliente.stats.aciertos_cache == 1


def test_estadisticas_iniciales():
    c = ClienteFalso()
    assert c.stats.llamadas == 0 and c.stats.reintentos == 0


class _FalloHTTP(Exception):
    def __init__(self, estado: int, mensaje: str):
        super().__init__(mensaje)
        self.status_code = estado


def _cliente_con_error(estado: int, mensaje: str) -> ClienteDeepSeek:
    class Completions:
        def create(self, **kwargs):
            raise _FalloHTTP(estado, mensaje)

    class Chat:
        completions = Completions()

    class Stub:
        chat = Chat()

    cliente = ClienteDeepSeek(api_key="x")
    cliente._cliente = Stub()
    return cliente


def test_401_es_fatal_y_no_reintenta():
    cliente = _cliente_con_error(
        401, "Error code: 401 - Authentication Fails, your api key is invalid")
    with pytest.raises(ErrorFatal) as exc:
        cliente.traducir("s", "u")
    assert not isinstance(exc.value, ErrorTransitorio)
    assert cliente.stats.llamadas == 1          # ni un solo reintento
    assert cliente.stats.reintentos == 0


def test_error_fatal_sigue_siendo_error_cliente():
    assert issubclass(ErrorFatal, ErrorCliente)
    assert not issubclass(ErrorFatal, ErrorTransitorio)


def test_429_sigue_siendo_transitorio():
    cliente = _cliente_con_error(429, "Rate limit reached for requests")
    cliente.reintentos = 0
    with pytest.raises(ErrorTransitorio):
        cliente.traducir("s", "u")
    assert cliente.stats.llamadas == 1


def test_otro_error_sigue_siendo_cliente():
    cliente = _cliente_con_error(400, "context length exceeded")
    with pytest.raises(ErrorCliente) as exc:
        cliente.traducir("s", "u")
    assert not isinstance(exc.value, (ErrorFatal, ErrorTransitorio))


class _Uso:
    prompt_tokens = 120
    completion_tokens = 80


class _Mensaje:
    content = "<<<P1>>>\nhola"


class _Eleccion:
    message = _Mensaje()


class _Respuesta:
    choices = [_Eleccion()]
    usage = _Uso()


class _Completions:
    def create(self, **kwargs):
        return _Respuesta()


class _Chat:
    completions = _Completions()


class _Stub:
    chat = _Chat()


def test_tokens_se_acumulan(monkeypatch):
    cliente = ClienteDeepSeek(api_key="x", cache=None)
    cliente._cliente = _Stub()
    assert cliente.traducir("s", "u") == "<<<P1>>>\nhola"
    assert cliente.stats.tokens_entrada == 120
    assert cliente.stats.tokens_salida == 80
