"""Smoke test do script de migração de schema (DEC-V4-18/21) — confirma que
o arquivo realmente contém o ALTER TABLE esperado, sem depender de um
banco real (isso é conferido manualmente via \\d cercas_central, Cláusula VII)."""


def test_migracao_contem_alter_table_geometria_wkt():
    with open("migracao_geometria_wkt.sql", encoding="utf-8") as f:
        conteudo = f.read()
    assert "ALTER TABLE" in conteudo
    assert "ADD COLUMN" in conteudo
    assert "geometria_wkt" in conteudo
    assert "TEXT" in conteudo.upper()
