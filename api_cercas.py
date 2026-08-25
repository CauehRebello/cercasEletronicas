#!/usr/bin/env python3
"""
Bloco C — API REST fina sobre cercas_v2.py (FastAPI)
Transportadora Transleone Ltda  |  v4  |  2026-07-13

Só importa e invoca funções de `cercas_v2.py` (Módulos 1–9) — nenhuma
assinatura ou comportamento dessas funções é alterado. Espelha o mesmo
padrão de reuso já usado por `cercas_gui.py` (FAT-171), mas chamando as
funções de módulo diretamente em vez de `main()`/argparse, já que uma API
precisa de dados estruturados e códigos HTTP, não de stdout/exit codes.

Endpoints (Briefing_BlocoC_APIRestFina_v4.md, DEC-V4-07/13/14/15):
  POST /cercas            cerca avulsa (Bloco A — duplicidade — aplicado;
                           SEM checagem de sobreposição, DEC-V4-14: o modo
                           single-cerca do CLI nunca fez essa checagem, e
                           não há geometria completa persistida para
                           comparar contra execuções passadas).
  POST /cercas/lote       lote via upload de CSV (Bloco A + Bloco B, igual
                           ao --batch de hoje).
  GET  /cercas/historico  consulta à tabela central cercas_central (Postgres),
                           mesma conexão do Bloco A/health — DEC-V4-16.
  POST /cercas/override   stateless (DEC-V4-15): reenvia o mesmo CSV do
                           lote + overrides (codigo_existente, codigo_novo,
                           justificativa, confirmado_por) e reprocessa tudo
                           numa única chamada — não há persistência de
                           overrides pendentes entre chamadas.
  GET  /health            conectividade com o banco central.

Configuração via variáveis de ambiente (mesmo padrão de CERCAS_DB_PASSWORD):
  CERCAS_API_PG_DSN               DSN do banco central (sem senha — ver
                                   cercas_v2._pg_conectar/CERCAS_DB_PASSWORD).
  CERCAS_API_PG_FAKE               "1"/"true" usa _FakeCentralConn (nunca em
                                   produção — só para demonstração/teste).
  CERCAS_API_LIMIAR_SOBREPOSICAO  opcional, default 0.90 (Bloco B).
  CERCAS_API_OSM_MAX_TENTATIVAS / CERCAS_API_OSM_ESPERA_BASE_S /
  CERCAS_API_OSM_TIMEOUT_S        orçamento de retry/timeout do Overpass,
                                   mais conservador que os defaults do CLI.
  CERCAS_API_MAX_UPLOAD_BYTES     limite de tamanho do upload de lote
                                   (default 5 MiB).

Rodar localmente: uvicorn api_cercas:app --host 0.0.0.0 --port 8000
(host/porta livres para configurar; exposição pública fica fora de escopo —
Seção 4 do briefing, responsabilidade de infraestrutura).
"""
import json
import os
import tempfile
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field, ValidationError

import cercas_v2 as cv

app = FastAPI(
    title="Cercas Eletrônicas — API (Bloco C)",
    description="API REST fina sobre cercas_v2.py — uso interno da Transportadora Transleone Ltda.",
    version="4.0.0",
)


# ─────────────────────────────────────────────────────────────────────────────
# Configuração via variáveis de ambiente (lidas a cada chamada, não em import,
# para que testes possam ajustar via monkeypatch/env sem precisar recarregar
# o módulo).
# ─────────────────────────────────────────────────────────────────────────────

def _pg_dsn_config() -> Optional[str]:
    return os.environ.get("CERCAS_API_PG_DSN") or None


def _pg_fake_config() -> bool:
    return os.environ.get("CERCAS_API_PG_FAKE", "").strip().lower() in ("1", "true", "yes")


def _limiar_sobreposicao_config() -> float:
    valor = os.environ.get("CERCAS_API_LIMIAR_SOBREPOSICAO")
    return float(valor) if valor else cv.LIMIAR_SOBREPOSICAO_BLOQUEIO_PADRAO


def _osm_retry_config() -> Tuple[int, float, int]:
    return (
        int(os.environ.get("CERCAS_API_OSM_MAX_TENTATIVAS", "3")),
        float(os.environ.get("CERCAS_API_OSM_ESPERA_BASE_S", "3.0")),
        int(os.environ.get("CERCAS_API_OSM_TIMEOUT_S", str(cv.OVERPASS_TIMEOUT))),
    )


def _max_upload_bytes_config() -> int:
    return int(os.environ.get("CERCAS_API_MAX_UPLOAD_BYTES", str(5 * 1024 * 1024)))


# ─────────────────────────────────────────────────────────────────────────────
# Conexão com o banco central — dois padrões distintos, ver plano/briefing:
#   - /cercas e /health precisam de um `conn` já aberto (funções do Bloco A
#     que recebem `conn` como parâmetro).
#   - /cercas/lote e /cercas/override passam pg_dsn/pg_fake direto para
#     `processar_lote`, que já abre/fecha sua própria conexão — não usam
#     esta dependency.
# ─────────────────────────────────────────────────────────────────────────────

def _get_raw_conn():
    dsn = _pg_dsn_config()
    fake = _pg_fake_config()
    if not dsn and not fake:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Banco central não configurado (defina CERCAS_API_PG_DSN ou CERCAS_API_PG_FAKE=1).",
        )
    try:
        conn = cv._obter_conexao_central(dsn, usar_fake=fake, verbose=False)
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))
    try:
        yield conn
    finally:
        conn.close()


def get_pg_conn(conn=Depends(_get_raw_conn)):
    """Garante a tabela `cercas_central` (idempotente) antes de usar a conexão."""
    cv._pg_criar_tabela(conn)
    yield conn


# ─────────────────────────────────────────────────────────────────────────────
# Modelos Pydantic
# ─────────────────────────────────────────────────────────────────────────────

class CercaRequest(BaseModel):
    modo: str = Field(..., description="'A' (início+fim) ou 'B' (início+comprimento)")
    via: Optional[str] = Field(None, description="Referência/nome da via no OSM (ex.: 'BR-116')")
    polilinha: Optional[str] = Field(None, description="Fallback manual: 'lat1,lon1;lat2,lon2;...'")
    inicio: str = Field(..., description="'lat,lon'")
    fim: Optional[str] = Field(None, description="'lat,lon' — obrigatório no Modo A")
    comprimento: Optional[float] = Field(None, description="Metros — obrigatório no Modo B")
    pre: float = 0.0
    pos: float = 0.0
    buffer: float = 50.0
    rodovia: str
    cidade: str
    uf: str
    velocidade: int
    seq: int = 1


class OverrideItem(BaseModel):
    codigo_existente: str
    codigo_novo: str
    justificativa: str
    confirmado_por: str


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de orquestração (só chamam cercas_v2, nunca duplicam lógica)
# ─────────────────────────────────────────────────────────────────────────────

def _validar_cerca_request(body: CercaRequest) -> None:
    """Mesma validação do fluxo single-cerca do CLI (main(), fora do --batch)."""
    if body.via and body.polilinha:
        raise ValueError("'via' e 'polilinha' são mutuamente exclusivos.")
    if not body.via and not body.polilinha:
        raise ValueError("Informe 'via' ou 'polilinha'.")
    if body.modo not in ("A", "B"):
        raise ValueError("'modo' deve ser 'A' ou 'B'.")
    if body.modo == "A" and not body.fim:
        raise ValueError("Modo A requer 'fim'.")
    if body.modo == "B" and (not body.comprimento or body.comprimento <= 0):
        raise ValueError("Modo B requer 'comprimento' > 0.")
    if not 1 <= body.seq <= 999:
        raise ValueError("'seq' deve estar entre 1 e 999.")
    if len(body.uf) != 2:
        raise ValueError("'uf' deve ter exatamente 2 letras (ex.: MG, SC).")
    cv._validar_rodovia(body.rodovia)


def _registros_estruturados(cercas: Dict[str, dict], body: CercaRequest) -> List[Dict]:
    registros = []
    for chave, dados in cercas.items():
        vertices = dados.get("vertices", [])
        if not vertices:
            continue
        codigo = cv._montar_codigo(chave, body.rodovia, body.cidade, body.uf, body.velocidade, body.seq)
        registros.append({
            "codigo": codigo, "tipo": chave, "seq": body.seq,
            "vertices": vertices, "extensao_m": dados.get("extensao_m", 0),
            "rodovia": body.rodovia, "cidade": body.cidade, "uf": body.uf,
            "velocidade": body.velocidade,
        })
    return registros


_COLUNAS_CERCAS_CENTRAL = (
    "codigo", "tipo", "rodovia", "cidade", "uf", "velocidade", "seq",
    "extensao_m", "vertice_inicial", "vertice_final", "num_vertices",
    "data_criacao", "execucao_id", "status", "superado_em", "superado_motivo",
    "geometria_wkt",
)


def _wkt_linestring(vertices: List) -> Optional[str]:
    """Converte a lista de vértices (lat, lon) já calculada pelo Módulo 4
    (mesma lista usada para vertice_inicial/vertice_final) em WKT LINESTRING
    (lon lat, ...). Sem cálculo geométrico novo — só formatação. DEC-V4-21."""
    if not vertices or len(vertices) < 2:
        return None
    pontos = ", ".join(f"{lon:.6f} {lat:.6f}" for lat, lon in vertices)
    return f"LINESTRING({pontos})"


def _gravar_geometria_wkt(conn, registros: List[Dict]) -> None:
    """Grava geometria_wkt para cada registro já inserido por
    `cv.registrar_cerca_central`/`cv.processar_lote` (núcleo, intocado) —
    UPDATE separado, pois o INSERT do núcleo não conhece essa coluna
    (DEC-V4-27). Mesmo tratamento de `_FakeCentralConn` que
    `_consultar_cercas_central` (ver docstring lá): o parser de SQL do fake
    (`cercas_v2._FakeCentralCursor`) só reconhece os 4 padrões do Bloco A,
    então em modo fake a atualização é feita direto em `conn.rows`."""
    for registro in registros:
        wkt = _wkt_linestring(registro.get("vertices", []))
        if wkt is None:
            continue
        if isinstance(conn, cv._FakeCentralConn):
            for row in conn.rows:
                if row["codigo"] == registro["codigo"] and row["status"] == "ativo":
                    row["geometria_wkt"] = wkt
            continue
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE cercas_central SET geometria_wkt = %s WHERE codigo = %s AND status = 'ativo'",
            (wkt, registro["codigo"]),
        )
        conn.commit()


def _consultar_cercas_central(conn, filtros: Dict[str, str]) -> List[Dict]:
    """Consulta `cercas_central` (Postgres) diretamente — substitui o antigo
    `consultar_historico()` (SQLite local), eliminado por DEC-V4-16. Retorna
    registros de QUALQUER status (ativo/superado): preserva o espírito do
    histórico anterior (log de tudo já registrado), não só o estado atual.
    Mesmo contrato de filtros do endpoint (rodovia/uf/codigo, todos opcionais).

    `_FakeCentralConn` (usado por --pg-fake) só simula os 4 padrões de SQL
    que o Bloco A já emite (CREATE TABLE/SELECT codigo.../SELECT seq.../
    UPDATE/INSERT) — uma query nova (mesmo que só leitura) colidiria com o
    prefixo "SELECT codigo" da checagem de duplicidade e quebraria com
    ValueError ao tentar desempacotar os parâmetros errados. Em vez de
    alterar `_FakeCentralConn`/`_FakeCentralCursor` em cercas_v2.py (Módulo
    protegido) para reconhecer mais um padrão, filtra `conn.rows`
    diretamente em memória quando a conexão é a fake — mesmo resultado,
    sem SQL nenhum.
    """
    if isinstance(conn, cv._FakeCentralConn):
        linhas = [
            r for r in conn.rows
            if all(r.get(coluna) == valor for coluna, valor in filtros.items())
        ]
        return [{c: r.get(c) for c in _COLUNAS_CERCAS_CENTRAL} for r in linhas]

    condicoes = []
    valores = []
    for coluna in ("rodovia", "uf", "codigo"):
        valor = filtros.get(coluna)
        if valor:
            condicoes.append(f"{coluna} = %s")
            valores.append(valor)

    query = f"SELECT {', '.join(_COLUNAS_CERCAS_CENTRAL)} FROM cercas_central"
    if condicoes:
        query += " WHERE " + " AND ".join(condicoes)

    cursor = conn.cursor()
    cursor.execute(query, valores)
    return [dict(zip(_COLUNAS_CERCAS_CENTRAL, row)) for row in cursor.fetchall()]


def _processar_lote_arquivo(
    conteudo: bytes,
    overrides_sobreposicao: Optional[Dict[Tuple[str, str], Dict]] = None,
) -> dict:
    """Lógica compartilhada por /cercas/lote e /cercas/override — só monta
    caminhos temporários e chama `processar_lote`/`validar_csv`, sem
    duplicar nenhuma regra de negócio."""
    max_bytes = _max_upload_bytes_config()
    if len(conteudo) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Arquivo de lote excede o limite de {max_bytes} bytes.",
        )

    max_tentativas, espera_base_s, timeout_s = _osm_retry_config()

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        caminho_entrada = os.path.join(tmpdir, "entrada.csv")
        caminho_saida = os.path.join(tmpdir, "saida.csv")
        caminho_relatorio = os.path.join(tmpdir, "relatorio.csv")

        with open(caminho_entrada, "wb") as f:
            f.write(conteudo)

        dsn, fake = _pg_dsn_config(), _pg_fake_config()
        try:
            total, registros, sobreposicoes, bloqueios = cv.processar_lote(
                caminho_entrada, caminho_saida, verbose=False,
                caminho_relatorio=caminho_relatorio,
                max_tentativas=max_tentativas, espera_base_s=espera_base_s, timeout_s=timeout_s,
                pg_dsn=dsn, pg_fake=fake,
                limiar_sobreposicao=_limiar_sobreposicao_config(),
                overrides_sobreposicao=overrides_sobreposicao,
            )
        except cv.DuplicidadeCodigoCentralError as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"erro": "duplicidade_codigo", "mensagem": str(e)},
            )
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
        except RuntimeError as e:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))

        erros = cv.validar_csv(caminho_saida, verbose=False)
        if erros:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"erro": "validacao_saida", "detalhes": erros},
            )

        with open(caminho_relatorio, "r", encoding="utf-8") as f:
            relatorio_csv = f.read()

        if dsn or fake:
            conn = cv._obter_conexao_central(dsn, usar_fake=fake, verbose=False)
            try:
                _gravar_geometria_wkt(conn, registros)
            finally:
                conn.close()

    bloqueios_ativos = [b for b in bloqueios if b["bloqueado"]]
    resposta = {
        "total_gravado": total,
        "sobreposicoes_alerta": [
            {"codigo_a": a, "codigo_b": b} for a, b in sobreposicoes
        ],
        "bloqueios_sobreposicao": bloqueios,
        "relatorio_csv": relatorio_csv,
    }

    if bloqueios_ativos:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "erro": "bloqueio_sobreposicao",
                "mensagem": (
                    f"{len(bloqueios_ativos)} par(es) bloqueado(s) por sobreposição "
                    f"acima do limiar. Use POST /cercas/override para resolver."
                ),
                "pares_bloqueados": [
                    {
                        "codigo_existente": b["codigo_existente"],
                        "codigo_novo": b["codigo_novo"],
                        "percentual": b["percentual"],
                    }
                    for b in bloqueios_ativos
                ],
                **resposta,
            },
        )

    return resposta


# ─────────────────────────────────────────────────────────────────────────────
# POST /cercas — cerca avulsa (Bloco A aplicado; SEM Bloco B, DEC-V4-14)
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/cercas", status_code=status.HTTP_201_CREATED)
def criar_cerca(body: CercaRequest, conn=Depends(get_pg_conn)):
    try:
        _validar_cerca_request(body)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))

    # Bloco A — duplicidade de CÓDIGO, checada antes da geometria (falha
    # rápida, mesma ordem do CLI single-cerca). [FAT-181/182/183, DEC-V4-01]
    try:
        cv._checar_duplicidade_central(conn, body.rodovia, body.cidade, body.uf, body.velocidade, body.seq)
    except cv.DuplicidadeCodigoCentralError:
        # `DuplicidadeCodigoCentralError` só carrega mensagem formatada, sem
        # atributo estruturado — recomputa o SEQ sugerido (mesma chamada que
        # `_checar_duplicidade_central` já fez internamente antes de levantar
        # a exceção). Pequena janela de corrida (TOCTOU) entre requisições
        # concorrentes, aceitável nesta escala (uso interno).
        seq_sugerido = cv.sugerir_proximo_seq_livre(conn, body.rodovia, body.cidade, body.uf, body.velocidade)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "erro": "duplicidade_codigo",
                "mensagem": (
                    f"CÓDIGO já ativo na base central para {body.rodovia}/"
                    f"{body.cidade}_{body.uf.upper()}/{body.velocidade} KmH / SEQ {body.seq:03d}."
                ),
                "seq_sugerido": seq_sugerido,
            },
        )

    inicio = cv.parse_coord(body.inicio)
    fim = cv.parse_coord(body.fim) if body.modo == "A" else None

    if body.polilinha:
        try:
            polilinha = cv.parse_polilinha_manual(body.polilinha)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    else:
        max_tentativas, espera_base_s, timeout_s = _osm_retry_config()
        ponto_bbox_fim = fim if fim else inicio
        polilinha = cv.buscar_geometria_osm(
            body.via, inicio, ponto_bbox_fim, verbose=False,
            max_tentativas=max_tentativas, espera_base_s=espera_base_s, timeout_s=timeout_s,
        )
        if not polilinha:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Geometria não encontrada no OSM para a via informada. Use 'polilinha' para fallback manual.",
            )

    try:
        cercas = cv.gerar_cercas(
            polilinha=polilinha, modo=body.modo, inicio=inicio, fim=fim,
            comprimento_m=body.comprimento if body.modo == "B" else None,
            pre_m=body.pre, pos_m=body.pos, buffer_m=body.buffer, verbose=False,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        caminho_saida = os.path.join(tmpdir, "saida.csv")
        cv.exportar_csv(
            cercas=cercas, rodovia=body.rodovia, cidade=body.cidade, uf=body.uf,
            velocidade=body.velocidade, seq=body.seq, caminho=caminho_saida, verbose=False,
        )
        erros = cv.validar_csv(caminho_saida, verbose=False)
        if erros:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"erro": "validacao_saida", "detalhes": erros},
            )

    registros = _registros_estruturados(cercas, body)
    execucao_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
    for registro in registros:
        cv.registrar_cerca_central(conn, registro, execucao_id)
    _gravar_geometria_wkt(conn, registros)

    return {
        "execucao_id": execucao_id,
        "cercas": [
            {
                "codigo": r["codigo"], "tipo": r["tipo"],
                "extensao_m": r["extensao_m"], "num_vertices": len(r["vertices"]),
            }
            for r in registros
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# POST /cercas/lote — Bloco A + Bloco B, igual ao --batch de hoje
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/cercas/lote")
async def criar_lote(arquivo: UploadFile = File(...)):
    conteudo = await arquivo.read()
    return _processar_lote_arquivo(conteudo)


# ─────────────────────────────────────────────────────────────────────────────
# GET /cercas/historico — consulta cercas_central (Postgres), DEC-V4-16
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/cercas/historico")
def historico(
    rodovia: Optional[str] = None, uf: Optional[str] = None, codigo: Optional[str] = None,
    conn=Depends(get_pg_conn),
):
    filtros = {k: v for k, v in {"rodovia": rodovia, "uf": uf, "codigo": codigo}.items() if v}
    registros = _consultar_cercas_central(conn, filtros)
    return {"total": len(registros), "registros": registros}


# ─────────────────────────────────────────────────────────────────────────────
# POST /cercas/override — stateless (DEC-V4-15)
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/cercas/override")
async def override_sobreposicao(arquivo: UploadFile = File(...), overrides: str = Form(...)):
    try:
        overrides_bruto = json.loads(overrides)
        overrides_items = [OverrideItem(**item) for item in overrides_bruto]
    except (json.JSONDecodeError, ValidationError, TypeError) as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Campo 'overrides' inválido — esperado JSON com lista de "
                   f"{{codigo_existente, codigo_novo, justificativa, confirmado_por}}: {e}",
        )

    overrides_dict: Dict[Tuple[str, str], Dict] = {}
    for item in overrides_items:
        if not item.justificativa.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Justificativa obrigatória e não pode ser vazia. [DEC-V4-06]",
            )
        # `confirmado_por` vem do corpo da requisição (não de getpass.getuser()
        # como no CLI) — não há autenticação nesta fase (DEC-V4-07 Opção A)
        # para derivar "quem" de uma sessão de usuário; `quando` é carimbado
        # pelo servidor. Reaproveita o mesmo mecanismo de auditoria já
        # existente em gerar_relatorio() (Módulo 8), sem inventar um novo.
        overrides_dict[(item.codigo_existente, item.codigo_novo)] = {
            "justificativa": item.justificativa.strip(),
            "confirmado_por": item.confirmado_por,
            "quando": datetime.now().isoformat(timespec="seconds"),
        }

    conteudo = await arquivo.read()
    return _processar_lote_arquivo(conteudo, overrides_sobreposicao=overrides_dict)


# ─────────────────────────────────────────────────────────────────────────────
# GET /health
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health")
def health(conn=Depends(get_pg_conn)):
    return {"status": "ok"}
