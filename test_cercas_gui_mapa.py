"""Teste da função pura de parse de WKT usada pela aba 'Mapa' da GUI
(DEC-V4-23/25/26). Não instancia `CercasGUI`/Tk — só a função de parsing,
que não depende de display."""
from cercas_gui import _parse_wkt_linestring


def test_parse_wkt_linestring_basico():
    pontos = _parse_wkt_linestring("LINESTRING(-49.000000 -25.000000, -49.000000 -25.001000)")
    assert pontos == [(-25.0, -49.0), (-25.001, -49.0)]


def test_parse_wkt_linestring_none_ou_vazio():
    assert _parse_wkt_linestring(None) == []
    assert _parse_wkt_linestring("") == []


def test_parse_wkt_linestring_formato_invalido():
    assert _parse_wkt_linestring("POLYGON((1 2, 3 4))") == []
    assert _parse_wkt_linestring("LINESTRING") == []
