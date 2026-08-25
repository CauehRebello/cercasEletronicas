#!/usr/bin/env python3
"""
Backfill de geometria_wkt para cercas históricas — DEC-V4-22, FAT-326.

Script standalone (fora do CLI principal `cercas_v2.py`): percorre
`cercas_central` onde `geometria_wkt IS NULL`, rebusca a via no Overpass API
(Módulo 2, `cv.buscar_geometria_osm`, mesmo retry configurável do S7) usando
`vertice_inicial`/`vertice_final` como aproximação de início/fim, e grava o
WKT resultante. Não altera `cercas_v2.py`.

ATENÇÃO — limitação inerente ao schema histórico (ver plano/briefing): a
lista completa de vértices do polígono original (buffer aplicado) nunca foi
persistida — só os 2 extremos. Não há como reconstruir esse polígono exato.
O WKT gravado aqui é a LINHA DA VIA (Módulo 2), não o polígono com buffer —
diferente, portanto, do que `api_cercas.py` grava para cercas novas
(polígono completo). Isso é esperado e é logado por linha.

Uso:
    python backfill_geometria_wkt.py --pg-dsn "host=... dbname=..."
    python backfill_geometria_wkt.py --pg-fake   # smoke test, sem Postgres real
"""
import argparse
from typing import Dict, List, Optional, Tuple

import cercas_v2 as cv


def _wkt_linestring(pontos: List[Tuple[float, float]]) -> Optional[str]:
    if not pontos or len(pontos) < 2:
        return None
    return "LINESTRING(" + ", ".join(f"{lon:.6f} {lat:.6f}" for lat, lon in pontos) + ")"


def _linhas_pendentes(conn) -> List[Dict]:
    """Retorna as linhas sem geometria_wkt. Para `_FakeCentralConn`, filtra
    `conn.rows` diretamente (mesmos objetos, por referência) — o parser de
    SQL do fake (`cercas_v2._FakeCentralCursor`) só reconhece os 4 padrões
    do Bloco A e não suporta esta consulta nova."""
    if isinstance(conn, cv._FakeCentralConn):
        return [row for row in conn.rows if not row.get("geometria_wkt")]
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, codigo, rodovia, vertice_inicial, vertice_final "
        "FROM cercas_central WHERE geometria_wkt IS NULL"
    )
    colunas = ("id", "codigo", "rodovia", "vertice_inicial", "vertice_final")
    return [dict(zip(colunas, row)) for row in cursor.fetchall()]


def _gravar_wkt(conn, linha: Dict, wkt: str) -> None:
    if isinstance(conn, cv._FakeCentralConn):
        linha["geometria_wkt"] = wkt
        return
    cursor = conn.cursor()
    cursor.execute("UPDATE cercas_central SET geometria_wkt = %s WHERE id = %s", (wkt, linha["id"]))
    conn.commit()


def backfill(
    conn,
    max_tentativas: int = 3,
    espera_base_s: float = 3.0,
    timeout_s: int = cv.OVERPASS_TIMEOUT,
    verbose: bool = True,
) -> Tuple[int, int]:
    linhas = _linhas_pendentes(conn)
    if verbose:
        print(f"  {len(linhas)} cerca(s) sem geometria_wkt encontrada(s).")

    sucesso, falha = 0, 0
    for linha in linhas:
        identificador = linha.get("codigo") or linha.get("id") or "?"
        try:
            ponto_inicio = cv.parse_coord(linha["vertice_inicial"])
            ponto_fim = cv.parse_coord(linha["vertice_final"])
            polilinha = cv.buscar_geometria_osm(
                linha["rodovia"], ponto_inicio, ponto_fim, verbose=False,
                max_tentativas=max_tentativas, espera_base_s=espera_base_s, timeout_s=timeout_s,
            )
            if not polilinha:
                raise RuntimeError("Overpass não retornou geometria para a via.")
            wkt = _wkt_linestring(polilinha)
            if wkt is None:
                raise RuntimeError("Geometria com menos de 2 pontos — WKT não gerado.")
        except Exception as e:
            falha += 1
            print(f"[FALHA] {identificador}: {e}")
            continue

        _gravar_wkt(conn, linha, wkt)
        sucesso += 1
        if verbose:
            print(f"[OK]    {identificador}: geometria_wkt gravada ({len(polilinha)} pontos).")

    print(f"\nBackfill concluído: {sucesso} sucesso(s), {falha} falha(s), {len(linhas)} total.")
    return sucesso, falha


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill de geometria_wkt em cercas_central (DEC-V4-22)."
    )
    parser.add_argument("--pg-dsn", help="DSN do banco central (ex.: 'host=10.2.15.3 dbname=... user=...').")
    parser.add_argument("--pg-fake", action="store_true", help="Usa _FakeCentralConn (smoke test, sem Postgres real).")
    parser.add_argument("--retry-tentativas", type=int, default=3)
    parser.add_argument("--retry-espera", type=float, default=3.0)
    parser.add_argument("--retry-timeout", type=int, default=cv.OVERPASS_TIMEOUT)
    args = parser.parse_args()

    if not args.pg_dsn and not args.pg_fake:
        parser.error("Informe --pg-dsn ou --pg-fake.")

    conn = cv._obter_conexao_central(args.pg_dsn, usar_fake=args.pg_fake, verbose=True)
    try:
        backfill(conn, args.retry_tentativas, args.retry_espera, args.retry_timeout)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
