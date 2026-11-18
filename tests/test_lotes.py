from traductor.lotes import Item, agrupar, parsear_respuesta, render


def item(i, texto="x" * 100, seccion="1. Introducción", titulo=False, **kw):
    return Item(id=f"P{i:04d}", texto=texto, seccion=seccion, titulo=titulo, **kw)


def test_ningun_parrafo_se_parte():
    items = [item(i) for i in range(1, 40)]
    ls = agrupar(items, max_caracteres=1000, max_parrafos=4)
    vistos = [i.id for lote in ls for i in lote.items]
    assert vistos == [i.id for i in items]
    for lote in ls:
        for i in lote.items:
            assert lote.items.count(i) == 1


def test_respeta_limites():
    items = [item(i, "x" * 300) for i in range(1, 30)]
    ls = agrupar(items, max_caracteres=1000, max_parrafos=3)
    for lote in ls:
        assert len(lote) <= 3
        assert lote.caracteres <= 1000 or len(lote) == 1


def test_parrafo_gigante_forma_su_propio_lote():
    items = [item(1, "a" * 50), item(2, "b" * 9000), item(3, "c" * 50)]
    ls = agrupar(items, max_caracteres=1000, max_parrafos=12)
    assert any([i.id for i in lote.items] == ["P0002"] for lote in ls)


def test_no_mezcla_secciones():
    items = [item(1, "a" * 10, "1. Introducción"),
             item(2, "b" * 10, "1. Introducción"),
             item(3, "c" * 10, "2. Metodología"),
             item(4, "d" * 10, "2. Metodología")]
    for lote in agrupar(items):
        assert len({i.seccion for i in lote.items}) == 1


def test_titulo_encabeza_su_lote():
    items = [item(1, "texto previo", "0. Portada"),
             item(2, "1. Introducción", "1. Introducción", titulo=True),
             item(3, "cuerpo", "1. Introducción")]
    ls = agrupar(items)
    lote_titulo = next(l for l in ls if any(i.titulo for i in l.items))
    assert lote_titulo.items[0].id == "P0002"
    assert lote_titulo.items[0].titulo


def test_se_saltan_protegidos_y_no_traducibles():
    items = [item(1), Item(id="P0002", texto="Referencias", seccion="Referencias",
                           protegido=True),
             Item(id="P0003", texto="[1] algo", seccion="Referencias",
                  traducible=False),
             item(4)]
    ids = [i.id for lote in agrupar(items) for i in lote.items]
    assert ids == ["P0001", "P0004"]


def test_render_y_parseo_de_respuesta():
    ls = agrupar([item(1, "uno"), item(2, "dos")], max_parrafos=12)
    texto = render(ls[0])
    assert texto.startswith("<<<P0001>>>")
    respuesta = (
        "<<<P0001>>>\ntexto uno traducido\n"
        "<<<P0002>>>\ntexto dos traducido\n"
    )
    bloques = parsear_respuesta(respuesta)
    assert bloques["P0001"] == "texto uno traducido"
    assert bloques["P0002"] == "texto dos traducido"


def test_ids_de_lote_unicos():
    ls = agrupar([item(i) for i in range(1, 30)], max_parrafos=2)
    ids = [l.id for l in ls]
    assert len(ids) == len(set(ids))
