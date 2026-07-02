#!/usr/bin/env python3
"""
Protótipo Funcional — Sistema de Cercas Eletrônicas Padronizadas
Transportadora Transleone Ltda  |  v2  |  2026-07-01

Base: cercas.py v1.4 (2026-06-27). Todos os invariantes 🔒 e parâmetros
⚙️ da v1.4 são preservados sem alteração (Termo de Compromisso, cláusula I —
FAT-52). Nenhuma lógica dos 6 módulos originais foi modificada.

Evolução da v2 (escopo definido em DEC-3, FAT-64):
  MÓDULO 1B — Geração em lote (batch)  [FAT-63, S2]
    Permite importar uma planilha/CSV com múltiplos trechos e exportar
    um único arquivo consolidado com todas as cercas (PRI + PRE de cada
    linha). O fluxo single-cerca original (CLI de uma cerca por execução)
    permanece disponível e inalterado — retrocompatibilidade (cláusula V,
    FAT-56).

Referências do Registro de Fatos (Método RASTRO):
  FAT-5  Objetivo geral do sistema
  FAT-14 Buffer PRI: 50 m/lado (100 m total)
  FAT-15 Modos de origem/fim (A = par de coords; B = início + comprimento)
  FAT-20 Layout SASCAR — tipo POL verbatim
  FAT-21 Tipo POL adotado para todas as cercas
  FAT-24 Fonte da geometria: OpenStreetMap (costura de ways + fallback manual)
  FAT-28 Modo B corrigido: início + comprimento; ponto final calculado pelo sistema
  FAT-29 Précerca: polígono único de início−X a fim+Y; PRI sobrepõe PRE; POS eliminado (DEC-14)
  FAT-30 Prefixo POS eliminado; apenas PRI e PRE são gerados (DEC-15)
  FAT-31 Campo CÓDIGO SASCAR sem limite fixo de caracteres (confirmado SASCAR)
  FAT-32 CÓDIGO: TIPO - RODOVIA - CIDADE_UF - VELOCIDADE KmH - SEQ (DEC-16, DEC-19)
  FAT-33 DESCRIÇÃO auto-gerada: Extensao: Nm - Criacao AAAAMMDD_HH:MM;
         extensão reflete o comprimento real de cada polígono (DEC-17)
  FAT-34 Saída disponível em .csv ou .txt — conteúdo idêntico (DEC-18)
  FAT-35 Proibido '/' nos campos 2 e 3 do arquivo de exportação;
         CIDADE_UF com underscore; velocidade em KmH (DEC-19)
  FAT-36 Buffer PRE = buffer PRI + 5 m/lado (padrão: PRE=55m, PRI=50m) (DEC-20)

Fluxo (6 módulos):
  1. Entrada do operador
  2. Busca e costura de ways OSM  [FAT-24]
  3. Projeção UTM e buffer         [FAT-14]
  4. Recorte e variantes           [FAT-15, FAT-28, FAT-29, FAT-30]
  5. Exportação CSV/TXT SASCAR     [FAT-20, FAT-21, FAT-32, FAT-33, FAT-34]
  6. Validação do arquivo gerado
"""

import argparse
import csv
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import requests
from pyproj import Transformer
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import substring as line_substring

# ─────────────────────────────────────────────────────────────────────────────
# MÓDULO 1 — ENTRADA
# ─────────────────────────────────────────────────────────────────────────────

def parse_coord(s: str) -> Tuple[float, float]:
    """Converte 'lat,lon' em tupla (lat, lon)."""
    partes = s.strip().split(',')
    if len(partes) != 2:
        raise ValueError(f"Formato inválido '{s}'. Use 'lat,lon'.")
    return float(partes[0]), float(partes[1])


def parse_polilinha_manual(s: str) -> List[Tuple[float, float]]:
    """
    Converte 'lat1,lon1;lat2,lon2;...' em lista de (lat, lon).
    Fallback quando OSM não tem cobertura. [FAT-24]
    """
    resultado = []
    for par in s.strip().split(';'):
        par = par.strip()
        if par:
            resultado.append(parse_coord(par))
    if len(resultado) < 2:
        raise ValueError("Polilinha manual precisa de ao menos 2 pontos.")
    return resultado


# ─────────────────────────────────────────────────────────────────────────────
# MÓDULO 1B — LEITURA DE LOTE (BATCH)  [FAT-63, DEC-3]
# ─────────────────────────────────────────────────────────────────────────────

COLUNAS_BATCH_OBRIGATORIAS = [
    "modo", "inicio", "rodovia", "cidade", "uf", "velocidade", "seq",
]


def ler_lote(caminho: str) -> List[Dict[str, str]]:
    """
    Lê um arquivo CSV de lote e retorna uma lista de dicionários,
    um por linha/cerca a gerar.  [FAT-63]

    Colunas esperadas (cabeçalho obrigatório na 1ª linha):
      via, polilinha, modo, inicio, fim, comprimento, pre, pos, buffer,
      rodovia, cidade, uf, velocidade, seq

    Regras:
      - Cada linha deve informar 'via' OU 'polilinha' (nunca ambos vazios). [FAT-24]
      - 'modo' deve ser 'A' ou 'B'. [FAT-85]
      - 'modo' = A exige 'fim'; 'modo' = B exige 'comprimento'. [FAT-48]
      - Colunas ausentes usam os mesmos padrões do modo single-cerca:
        pre=0, pos=0, buffer=50.0. [FAT-43, FAT-44]
      - 'seq' deve ser um inteiro entre 1 e 999 (unicidade não verificada). [FAT-45, FAT-86]
      - 'velocidade' deve ser um inteiro maior que zero. [FAT-86]
    """
    linhas: List[Dict[str, str]] = []
    with open(caminho, "r", encoding="utf-8", newline="") as f:
        leitor = csv.DictReader(f)
        if leitor.fieldnames is None:
            raise ValueError(f"Arquivo de lote '{caminho}' está vazio ou sem cabeçalho.")

        faltantes = [c for c in COLUNAS_BATCH_OBRIGATORIAS if c not in leitor.fieldnames]
        if faltantes:
            raise ValueError(
                f"Arquivo de lote '{caminho}' não tem as colunas obrigatórias: {faltantes}."
            )

        for num_linha, row in enumerate(leitor, start=2):  # linha 1 = cabeçalho
            via        = (row.get("via") or "").strip()
            polilinha  = (row.get("polilinha") or "").strip()
            if not via and not polilinha:
                raise ValueError(
                    f"Lote, linha {num_linha}: informe 'via' ou 'polilinha'."
                )
            # Sanidade: 'inicio'/'fim' devem ser um único campo "lat,lon" entre
            # aspas duplas; vírgula sem aspas quebra o CSV em campos extras e
            # csv.DictReader nunca vê essa vírgula aqui.  [FAT-67, DEC-5]
            for campo in ("inicio", "fim"):
                valor = row.get(campo)
                if valor and valor.strip().count(",") != 1:
                    raise ValueError(
                        f"Lote, linha {num_linha}: campo '{campo}' inválido ('{valor}'). "
                        f"Envolva as coordenadas em aspas duplas, ex.: \"-9.25049,-35.76806\"."
                    )
            # UF deve ter exatamente 2 letras — mesma regra do fluxo
            # single-cerca (CLI). Validado aqui, na leitura, em vez de só
            # no final via validar_csv (que só falharia depois de
            # processar o lote inteiro).  [FAT-82]
            uf_valor = (row.get("uf") or "").strip()
            if len(uf_valor) != 2:
                raise ValueError(
                    f"Lote, linha {num_linha}: campo 'uf' deve ter exatamente 2 letras "
                    f"(ex.: MG, SC). Valor encontrado: '{uf_valor}'. [FAT-82]"
                )
            # 'modo' deve ser 'A' ou 'B' — mesma regra do fluxo single-cerca
            # (CLI usa argparse choices=["A","B"]). Sem esta checagem, um
            # valor inválido (vazio, typo) só falhava mais tarde dentro de
            # gerar_cercas, tratado silenciosamente como Modo B e quebrando
            # com TypeError se 'comprimento' também estivesse vazio.  [FAT-85]
            modo_valor = (row.get("modo") or "").strip().upper()
            if modo_valor not in ("A", "B"):
                raise ValueError(
                    f"Lote, linha {num_linha}: campo 'modo' deve ser 'A' ou 'B'. "
                    f"Valor encontrado: '{modo_valor or '(vazio)'}'. [FAT-85]"
                )
            # 'velocidade' e 'seq' devem ser inteiros válidos (seq 1–999,
            # mesma faixa do fluxo single-cerca). Validado aqui para citar o
            # número da linha — sem isso, um valor não numérico só falhava
            # mais tarde em processar_linha_lote com erro genérico do Python
            # (ex.: "invalid literal for int()"), sem indicar a linha. [FAT-86]
            velocidade_valor = (row.get("velocidade") or "").strip()
            try:
                velocidade_int = int(velocidade_valor)
            except ValueError:
                raise ValueError(
                    f"Lote, linha {num_linha}: campo 'velocidade' deve ser um número "
                    f"inteiro (KmH). Valor encontrado: '{velocidade_valor}'. [FAT-86]"
                )
            if velocidade_int <= 0:
                raise ValueError(
                    f"Lote, linha {num_linha}: campo 'velocidade' deve ser maior que "
                    f"zero. Valor encontrado: '{velocidade_valor}'. [FAT-86]"
                )

            seq_valor = (row.get("seq") or "").strip()
            try:
                seq_int = int(seq_valor)
            except ValueError:
                raise ValueError(
                    f"Lote, linha {num_linha}: campo 'seq' deve ser um número inteiro "
                    f"entre 1 e 999. Valor encontrado: '{seq_valor}'. [FAT-86]"
                )
            if not 1 <= seq_int <= 999:
                raise ValueError(
                    f"Lote, linha {num_linha}: campo 'seq' deve estar entre 1 e 999. "
                    f"Valor encontrado: '{seq_valor}'. [FAT-86]"
                )
            row["_num_linha"] = str(num_linha)
            linhas.append(row)

    if not linhas:
        raise ValueError(f"Arquivo de lote '{caminho}' não contém nenhuma cerca.")

    return linhas


# ─────────────────────────────────────────────────────────────────────────────
# MÓDULO 2 — GEOMETRIA OSM (costura de ways)  [FAT-24]
# ─────────────────────────────────────────────────────────────────────────────

OVERPASS_URL = "https://lz4.overpass-api.de/api/interpreter"
OVERPASS_TIMEOUT = 30

# Distância máxima (m) de início/fim ao componente escolhido — 2x o buffer_m
# padrão (50 m). Acima disso, a via encontrada não é a pedida.  [FAT-68, DEC-6]
_COSTURA_DIST_MAX_M = 100.0


def _haversine_m(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
    """Distância em metros entre dois pontos (lat, lon)."""
    R = 6_371_000.0
    lat1, lon1 = math.radians(p1[0]), math.radians(p1[1])
    lat2, lon2 = math.radians(p2[0]), math.radians(p2[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _overpass_query(
    q: str,
    max_tentativas: int = 3,
    espera_base_s: float = 3.0,
    timeout_s: int = OVERPASS_TIMEOUT,
) -> dict:
    """Executa uma consulta no Overpass API com tentativas automáticas em caso de sobrecarga.

    `max_tentativas`, `espera_base_s` e `timeout_s` são configuráveis
    (padrões idênticos ao comportamento fixo anterior: 3 tentativas, 3s de
    espera, 30s de timeout).  [FAT-119, S7]
    """
    headers = {
        'User-Agent': 'ProjetoCercasTransleone/1.0 (caueh.rebello@transleone.com.br)',
        'Accept': 'application/json, text/javascript, */*; q=0.01'
    }

    tentativas = max_tentativas
    for tentativa in range(tentativas):
        try:
            resp = requests.post(
                OVERPASS_URL,
                data={"data": q},
                headers=headers,
                timeout=timeout_s
            )
            resp.raise_for_status()
            return resp.json()

        except requests.exceptions.HTTPError as e:
            if resp.status_code in [429, 502, 503, 504] and tentativa < tentativas - 1:
                print(f"  ⚠  Servidor ocupado (Status {resp.status_code}). Aguardando {espera_base_s} segundos...")
                time.sleep(espera_base_s)
                continue
            raise e

        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            # Erros de rede transitórios (conexão caiu, DNS falhou, timeout de
            # resposta) não chegam a gerar um HTTPError — ocorrem antes de haver
            # resposta HTTP. Sem este bloco, escapavam do loop de tentativas e
            # abortavam na primeira falha, ao contrário do que a docstring da
            # função promete ("tentativas automáticas").  [FAT-84]
            if tentativa < tentativas - 1:
                print(f"  ⚠  Falha de rede ({type(e).__name__}). Tentando novamente em {espera_base_s} segundos... "
                      f"(Tentativa {tentativa + 1}/{tentativas})")
                time.sleep(espera_base_s)
                continue
            raise Exception(
                f"Falha de rede persistente ao acessar o Overpass API após "
                f"{tentativas} tentativas: {e}  [FAT-84]"
            )

        except ValueError as e:
            if tentativa < tentativas - 1:
                print(f"  ⚠  Resposta inválida do servidor. Tentando novamente em {espera_base_s} segundos... "
                      f"(Tentativa {tentativa + 1}/{tentativas})")
                time.sleep(espera_base_s)
                continue
            raise Exception(
                f"O servidor retornou um formato inválido "
                f"(Provavelmente erro de cota ou sobrecarga textual): {resp.text[:100]}"
            )


def _costura_ways(ways: list, nodes: dict) -> List[List[int]]:
    """
    Costura de ways: encadeia ways OSM por nós compartilhados.
    Retorna TODOS os componentes conexos encontrados (cada um uma lista
    ordenada de node IDs) — a via pode estar fragmentada em vários trechos
    desconexos dentro do bbox de busca.  [FAT-68, DEC-6]

    Algoritmo greedy, repetido até esgotar os ways:
      1. Seleciona ways com nós conhecidos.
      2. Inicializa uma cadeia com o primeiro way restante.
      3. A cada passo, encontra o way cuja extremidade coincide com a
         extremidade atual da cadeia, revertendo-o se necessário.
      4. Ao travar (nada mais encaixa), fecha o componente e inicia outro
         com o que sobrou, até não restar nenhum way.
    """
    validos = [w for w in ways if all(n in nodes for n in w["nodes"])]
    componentes: List[List[int]] = []
    restantes = list(validos)

    while restantes:
        cadeia = [restantes.pop(0)]

        while restantes:
            ultimo_no   = cadeia[-1]["nodes"][-1]
            primeiro_no = cadeia[0]["nodes"][0]
            encaixou = False

            for i, w in enumerate(restantes):
                if w["nodes"][0] == ultimo_no:
                    cadeia.append(restantes.pop(i)); encaixou = True; break
                elif w["nodes"][-1] == ultimo_no:
                    w["nodes"] = list(reversed(w["nodes"]))
                    cadeia.append(restantes.pop(i)); encaixou = True; break
                elif w["nodes"][-1] == primeiro_no:
                    cadeia.insert(0, restantes.pop(i)); encaixou = True; break
                elif w["nodes"][0] == primeiro_no:
                    w["nodes"] = list(reversed(w["nodes"]))
                    cadeia.insert(0, restantes.pop(i)); encaixou = True; break

            if not encaixou:
                break

        nos = []
        for w in cadeia:
            for nid in w["nodes"]:
                if not nos or nos[-1] != nid:
                    nos.append(nid)
        componentes.append(nos)

    return componentes


# Cache local de geometrias OSM (opt-in via --cache).  [FAT-154, S4]
_CACHE_PATH = ".osm_cache.json"


def _cache_key(
    ref_ou_nome: str,
    ponto_inicio: Tuple[float, float],
    ponto_fim: Tuple[float, float],
) -> str:
    """Chave de cache: hash de (ref_ou_nome, bbox arredondado a 3 casas)."""
    lats = [ponto_inicio[0], ponto_fim[0]]
    lons = [ponto_inicio[1], ponto_fim[1]]
    bbox_arred = (
        round(min(lats) - 0.2, 3), round(min(lons) - 0.2, 3),
        round(max(lats) + 0.2, 3), round(max(lons) + 0.2, 3),
    )
    chave_str = f"{ref_ou_nome}|{bbox_arred}"
    return hashlib.sha256(chave_str.encode("utf-8")).hexdigest()


def _cache_get(chave: str, caminho: Optional[str] = None) -> Optional[List[Tuple[float, float]]]:
    """Retorna o valor em cache para `chave`, ou None se ausente/expirado/corrompido."""
    if caminho is None:
        caminho = _CACHE_PATH
    if not os.path.exists(caminho):
        return None
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            dados = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    entrada = dados.get(chave)
    if not entrada:
        return None
    if time.time() - entrada["timestamp"] > entrada["ttl_s"]:
        return None
    return [tuple(p) for p in entrada["valor"]]


def _cache_set(chave: str, valor: List[Tuple[float, float]], ttl_s: float, caminho: Optional[str] = None) -> None:
    """Grava `valor` em cache sob `chave`, com timestamp atual e `ttl_s`."""
    if caminho is None:
        caminho = _CACHE_PATH
    dados = {}
    if os.path.exists(caminho):
        try:
            with open(caminho, "r", encoding="utf-8") as f:
                dados = json.load(f)
        except (json.JSONDecodeError, OSError):
            dados = {}
    dados[chave] = {"valor": valor, "timestamp": time.time(), "ttl_s": ttl_s}
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(dados, f)


def buscar_geometria_osm(
    ref_ou_nome: str,
    ponto_inicio: Tuple[float, float],
    ponto_fim: Tuple[float, float],
    verbose: bool = True,
    max_tentativas: int = 3,
    espera_base_s: float = 3.0,
    timeout_s: int = OVERPASS_TIMEOUT,
    usar_cache: bool = False,
    cache_ttl_s: float = 86400,
) -> Optional[List[Tuple[float, float]]]:
    """
    Busca a geometria de uma via no OSM por referência (ex.: 'BR-116') ou nome.
    Retorna lista de (lat, lon) costurada e orientada. [FAT-24]
    Retorna None se não encontrar ou em caso de erro de rede.

    `max_tentativas`, `espera_base_s` e `timeout_s` são repassados a
    `_overpass_query` (padrões idênticos ao comportamento anterior).  [FAT-119, S7]

    `usar_cache`/`cache_ttl_s`: se `usar_cache=True`, tenta reaproveitar uma
    busca anterior para a mesma via/bbox (arquivo local `.osm_cache.json`)
    antes de consultar o Overpass API. Desabilitado por padrão — sem
    `usar_cache`, o comportamento é idêntico ao anterior (sempre busca na
    rede).  [FAT-154, S4]
    """
    chave_cache = None
    if usar_cache:
        chave_cache = _cache_key(ref_ou_nome, ponto_inicio, ponto_fim)
        em_cache = _cache_get(chave_cache)
        if em_cache is not None:
            if verbose:
                print(f"  ✓ Geometria de '{ref_ou_nome}' recuperada do cache local. [FAT-154, S4]")
            return em_cache

    lats = [ponto_inicio[0], ponto_fim[0]]
    lons = [ponto_inicio[1], ponto_fim[1]]
    bbox = f"{min(lats)-0.2},{min(lons)-0.2},{max(lats)+0.2},{max(lons)+0.2}"

    query = f"""
[out:json][timeout:{timeout_s}];
(
  way["ref"="{ref_ou_nome}"]["highway"]({bbox});
  way["name"="{ref_ou_nome}"]["highway"]({bbox});
);
(._;>;);
out body;
"""
    try:
        dados = _overpass_query(
            query,
            max_tentativas=max_tentativas,
            espera_base_s=espera_base_s,
            timeout_s=timeout_s,
        )
    except Exception as e:
        if verbose:
            print(f"  ⚠  Erro ao acessar o OSM: {e}")
        return None

    nodes: dict = {}
    ways: list = []
    for el in dados.get("elements", []):
        if el["type"] == "node":
            nodes[el["id"]] = (el["lat"], el["lon"])
        elif el["type"] == "way":
            ways.append(el)

    if not ways:
        if verbose:
            print(f"  ⚠  Nenhum way encontrado para '{ref_ou_nome}' no OSM.")
        return None

    if verbose:
        print(f"  → {len(ways)} way(s) encontrado(s), {len(nodes)} nó(s).")

    sequencias = _costura_ways(ways, nodes)
    if not sequencias:
        if verbose:
            print("  ⚠  Costura de ways falhou.")
        return None

    # Escolhe o componente mais próximo de início/fim — via pode estar
    # fragmentada em trechos desconexos dentro da bbox.  [FAT-68, DEC-6]
    melhor = None
    for nos in sequencias:
        coords_comp = [nodes[n] for n in nos]
        d_ini = min(_haversine_m(ponto_inicio, c) for c in coords_comp)
        d_fim = min(_haversine_m(ponto_fim, c) for c in coords_comp)
        if melhor is None or (d_ini + d_fim) < melhor[0]:
            melhor = (d_ini + d_fim, coords_comp, d_ini, d_fim)

    _, coords, d_ini, d_fim = melhor
    if d_ini > _COSTURA_DIST_MAX_M or d_fim > _COSTURA_DIST_MAX_M:
        if verbose:
            print(f"  ⚠  Nenhum trecho de '{ref_ou_nome}' passa perto de "
                  f"início/fim (mais próximo: início {d_ini:.0f} m, fim {d_fim:.0f} m).")
        return None

    d_primeiro = _haversine_m(ponto_inicio, coords[0])
    d_ultimo   = _haversine_m(ponto_inicio, coords[-1])
    if d_ultimo < d_primeiro:
        coords = list(reversed(coords))

    if verbose:
        print(f"  ✓ Polilinha: {len(coords)} pontos, "
              f"{sum(_haversine_m(coords[i], coords[i+1]) for i in range(len(coords)-1)):.0f} m totais.")

    if usar_cache:
        _cache_set(chave_cache, coords, cache_ttl_s)

    return coords


# ─────────────────────────────────────────────────────────────────────────────
# MÓDULO 3 — PROJEÇÃO UTM E BUFFER  [FAT-14]
# ─────────────────────────────────────────────────────────────────────────────

def _utm_epsg(lat: float, lon: float) -> str:
    """Retorna o código EPSG da zona UTM para a coordenada dada."""
    zona = int((lon + 180) / 6) + 1
    return f"EPSG:{32600 + zona}" if lat >= 0 else f"EPSG:{32700 + zona}"


def _poly_utm_to_wgs84(polygon, epsg: str) -> List[Tuple[float, float]]:
    """Converte o exterior de um polígono Shapely (UTM) para lista de (lat, lon)."""
    t = Transformer.from_crs(epsg, "EPSG:4326", always_xy=True)
    vertices = []
    for x, y in list(polygon.exterior.coords)[:-1]:   # remove vértice fechador
        lon, lat = t.transform(x, y)
        vertices.append((lat, lon))
    return vertices


# ─────────────────────────────────────────────────────────────────────────────
# MÓDULO 4 — RECORTE E VARIANTES  [FAT-15, FAT-28, FAT-29, FAT-30]
# ─────────────────────────────────────────────────────────────────────────────

def gerar_cercas(
    polilinha: List[Tuple[float, float]],
    modo: str,
    inicio: Tuple[float, float],
    fim: Optional[Tuple[float, float]],
    comprimento_m: Optional[float],
    pre_m: float,
    pos_m: float,
    buffer_m: float = 50.0,
    verbose: bool = True,
) -> Dict[str, dict]:
    """
    Gera os dois polígonos (PRI, PRE) a partir da polilinha da via.

    Modo A: início + fim (par de coordenadas)          [FAT-15]
    Modo B: início + comprimento (ponto fim calculado) [FAT-28]
    Buffer: 50 m/lado = 100 m total                    [FAT-14]
    Précerca: polígono único de início−X a fim+Y       [FAT-29]
    POS eliminado                                      [FAT-30]

    Retorna dict:
      {
        'PRI': {'vertices': [(lat,lon),...], 'extensao_m': int},
        'PRE': {'vertices': [(lat,lon),...], 'extensao_m': int},
      }
    extensao_m reflete o comprimento real de cada polígono.  [FAT-33]
    """
    epsg = _utm_epsg(inicio[0], inicio[1])
    t_fwd = Transformer.from_crs("EPSG:4326", epsg, always_xy=True)

    linha_utm = LineString([t_fwd.transform(lon, lat) for lat, lon in polilinha])
    L = linha_utm.length

    ix, iy = t_fwd.transform(inicio[1], inicio[0])
    inicio_m = linha_utm.project(Point(ix, iy))

    if modo == "A":
        fx, fy = t_fwd.transform(fim[1], fim[0])
        fim_m = linha_utm.project(Point(fx, fy))
        if fim_m < inicio_m:
            inicio_m, fim_m = fim_m, inicio_m
    else:   # Modo B [FAT-28]
        fim_m = min(L, inicio_m + comprimento_m)
        # Cobertura: a via encontrada precisa ter comprimento_m metros a
        # partir do início; se for mais curta, o corte por min(L, ...) acima
        # seria silencioso. Falha explícita em vez de cerca truncada sem
        # aviso.  [FAT-81]
        faltante_m = comprimento_m - (fim_m - inicio_m)
        if faltante_m > 1.0:  # tolerância de 1 m para erro de projeção/arredondamento
            raise ValueError(
                f"Modo B: a via encontrada tem apenas {L - inicio_m:.1f} m a partir "
                f"do início, mas foram pedidos {comprimento_m:.1f} m de cerca "
                f"(faltam {faltante_m:.1f} m). Cerca NÃO gerada — verifique a via "
                f"ou reduza --comprimento. [FAT-81]"
            )

    comprimento_cerca = fim_m - inicio_m
    if verbose:
        print(f"  → Trecho principal: {comprimento_cerca:.1f} m  "
              f"(início={inicio_m:.1f} m, fim={fim_m:.1f} m ao longo da via)")

    def segmento_para_poligono(a_m: float, b_m: float, buf_m: float = buffer_m) -> List[Tuple[float, float]]:
        """Recorta [a_m, b_m] da linha UTM e aplica buffer. [FAT-14]"""
        a_m = max(0.0, min(L, a_m))
        b_m = max(0.0, min(L, b_m))
        if b_m <= a_m:
            return []
        seg = line_substring(linha_utm, a_m, b_m)
        buf = seg.buffer(buf_m, cap_style=2)
        return _poly_utm_to_wgs84(buf, epsg)

    # PRI: início → fim  [FAT-14, FAT-15, FAT-28]
    pri_vertices = segmento_para_poligono(inicio_m, fim_m)
    pri_extensao = int(round(fim_m - inicio_m))

    # PRE: (início − X) → (fim + Y); buffer = buffer_m + 5 m/lado  [FAT-29, FAT-36]
    pre_buffer_m = buffer_m + 5.0
    if pre_m > 0 or pos_m > 0:
        pre_start    = inicio_m - pre_m
        pre_end      = fim_m + pos_m
        pre_vertices = segmento_para_poligono(pre_start, pre_end, pre_buffer_m)
        pre_extensao = int(round(min(L, pre_end) - max(0.0, pre_start)))
    else:
        pre_vertices = []
        pre_extensao = 0

    return {
        "PRI": {"vertices": pri_vertices, "extensao_m": pri_extensao},
        "PRE": {"vertices": pre_vertices, "extensao_m": pre_extensao},
    }


# ─────────────────────────────────────────────────────────────────────────────
# MÓDULO 5 — EXPORTAÇÃO CSV/TXT SASCAR  [FAT-20, FAT-21, FAT-32, FAT-33, FAT-34]
# ─────────────────────────────────────────────────────────────────────────────

# Rodovia válida: 2 letras maiúsculas + hífen + 3 dígitos  [FAT-32]
RODOVIA_RE    = re.compile(r'^[A-Z]{2}-\d{3}$')
CODIGO_VAL_RE = re.compile(r'^(PRI|PRE) - .+ - [^_]+_[A-Z]{2} - \d+ KmH - \d{3}$')


def _validar_rodovia(rodovia: str) -> None:
    """
    Valida o campo rodovia.  [FAT-32]
    Se contiver dígito: obrigatório formato XX-NNN (ex.: BR-116, SC-470).
    Se não contiver dígito: nome de rua, aceito livre.
    """
    if any(c.isdigit() for c in rodovia):
        if not RODOVIA_RE.match(rodovia):
            raise ValueError(
                f"Formato de rodovia inválido: '{rodovia}'. "
                f"Use 2 letras maiúsculas + hífen + 3 dígitos (ex.: BR-116, SC-470)."
            )


def _montar_codigo(tipo: str, rodovia: str, cidade: str, uf: str,
                   velocidade: int, seq: int) -> str:
    """
    Monta o campo CÓDIGO: TIPO - RODOVIA - CIDADE_UF - VELOCIDADE KmH - SEQ
    [FAT-32, FAT-35]
    Ex.: PRI - BR-116 - LUZ_MG - 60 KmH - 001
    Proibido '/' em qualquer subcampo. [FAT-35]
    """
    return f"{tipo} - {rodovia} - {cidade}_{uf.upper()[:2]} - {velocidade} KmH - {seq:03d}"


def _linha_sascar(tipo_pol: str, codigo: str, extensao_m: int,
                  vertices: List[Tuple[float, float]]) -> str:
    """
    Formata uma linha no layout SASCAR POL.  [FAT-20]
    "POL";"CÓDIGO";"Extensao: Nm - Criacao AAAAMMDD_HH:MM";lat1,lon1;...
    DESCRIÇÃO auto-gerada; extensão específica deste polígono.  [FAT-33]
    """
    criacao   = datetime.now().strftime("%Y%m%d_%H:%M")
    descricao = f"Extensao: {extensao_m}m - Criacao {criacao}"
    campos    = [f'"{tipo_pol}"', f'"{codigo}"', f'"{descricao}"']
    # Dedup de vértices consecutivos idênticos após arredondar p/ 6 casas
    # (buffer cap_style=2 pode gerar repetição no arredondamento).  [FAT-66, DEC-4]
    ultimo = None
    for lat, lon in vertices:
        par = (round(lat, 6), round(lon, 6))
        if par == ultimo:
            continue
        ultimo = par
        campos.append(f"{par[0]:.6f},{par[1]:.6f}")
    return ";".join(campos)


def _montar_linhas_cerca(
    cercas:     Dict[str, dict],
    rodovia:    str,
    cidade:     str,
    uf:         str,
    velocidade: int,
    seq:        int,
) -> List[str]:
    """
    Monta as linhas SASCAR (PRI + PRE) de UMA cerca, sem escrever em disco.
    Extraído de exportar_csv para reuso no modo batch.  [FAT-20, FAT-21, FAT-32, FAT-33]
    """
    _validar_rodovia(rodovia)

    # Garantir ausência de '/' nos campos 2 e 3  [FAT-35]
    for campo, valor in [("cidade", cidade), ("uf", uf)]:
        if "/" in str(valor):
            raise ValueError(f"Campo '{campo}' não pode conter '/': '{valor}'. [FAT-35]")

    linhas = []
    for chave in ("PRI", "PRE"):
        registro   = cercas.get(chave, {})
        vertices   = registro.get("vertices", [])
        extensao_m = registro.get("extensao_m", 0)
        if not vertices:
            continue
        codigo = _montar_codigo(chave, rodovia, cidade, uf, velocidade, seq)
        linhas.append(_linha_sascar("POL", codigo, extensao_m, vertices))
    return linhas


def exportar_csv(
    cercas:     Dict[str, dict],
    rodovia:    str,
    cidade:     str,
    uf:         str,
    velocidade: int,
    seq:        int,
    caminho:    str,
    verbose:    bool = True,
) -> int:
    """
    Escreve o arquivo de saída compatível com o layout SASCAR.
    Gera PRI e PRE (POS eliminado por FAT-30).
    [FAT-20, FAT-21, FAT-32, FAT-33, FAT-34]
    Retorna o número de registros gravados.

    Comportamento e assinatura inalterados em relação à v1.4 — usado pelo
    fluxo single-cerca. Reutiliza _montar_linhas_cerca internamente. [FAT-56]
    """
    linhas = _montar_linhas_cerca(cercas, rodovia, cidade, uf, velocidade, seq)

    with open(caminho, "w", encoding="utf-8", newline="\n") as f:
        for linha in linhas:
            f.write(linha + "\n")

    if verbose:
        print(f"  ✓ {len(linhas)} registro(s) gravado(s) em '{caminho}'.")
        for linha in linhas:
            preview = linha[:120] + ("..." if len(linha) > 120 else "")
            print(f"    {preview}")
    return len(linhas)


def exportar_lote(
    todas_linhas: List[str],
    caminho:      str,
    verbose:      bool = True,
) -> int:
    """
    Escreve um ÚNICO arquivo consolidado com as linhas SASCAR de todas as
    cercas do lote (PRI + PRE de cada linha do CSV de entrada).  [FAT-63]
    Mesmo formato de arquivo do modo single-cerca — conteúdo idêntico entre
    .csv e .txt (FAT-47) e mesma estrutura de registro POL (FAT-37).
    """
    with open(caminho, "w", encoding="utf-8", newline="\n") as f:
        for linha in todas_linhas:
            f.write(linha + "\n")

    if verbose:
        print(f"  ✓ {len(todas_linhas)} registro(s) consolidado(s) gravado(s) em '{caminho}'.")
    return len(todas_linhas)


# ─────────────────────────────────────────────────────────────────────────────
# MÓDULO 6 — VALIDAÇÃO  [FAT-20, FAT-32]
# ─────────────────────────────────────────────────────────────────────────────

def validar_csv(caminho: str, verbose: bool = True) -> List[str]:
    """
    Valida o arquivo de saída gerado contra o layout SASCAR.  [FAT-20, FAT-32]
    Retorna lista de erros (vazia = OK).
    """
    erros = []
    with open(caminho, "r", encoding="utf-8") as f:
        linhas = f.readlines()

    for i, linha in enumerate(linhas, 1):
        linha = linha.strip()
        if not linha:
            continue
        campos = linha.split(";")

        if len(campos) < 4:
            erros.append(f"Linha {i}: menos de 4 campos (encontrados: {len(campos)}).")
            continue

        tipo     = campos[0].strip('"')
        codigo   = campos[1].strip('"')
        vertices = campos[3:]

        if tipo != "POL":
            erros.append(f"Linha {i}: tipo '{tipo}' inválido (esperado 'POL').")

        if not CODIGO_VAL_RE.match(codigo):
            erros.append(
                f"Linha {i}: CÓDIGO '{codigo}' fora do formato "
                f"'PRI|PRE - RODOVIA - CIDADE_UF - N KmH - NNN'. [FAT-32, FAT-35]"
            )

        for j, v in enumerate(vertices, 1):
            v = v.strip()
            if "," not in v:
                erros.append(f"Linha {i}, vértice {j}: '{v}' não está no formato lat,lon.")
                continue
            try:
                lat_s, lon_s = v.split(",", 1)
                float(lat_s); float(lon_s)
            except ValueError:
                erros.append(f"Linha {i}, vértice {j}: '{v}' contém valor não numérico.")

    if verbose:
        if erros:
            print(f"  ✗ {len(erros)} erro(s) encontrado(s):")
            for e in erros:
                print(f"    - {e}")
        else:
            print("  ✓ Arquivo válido — pronto para importação SASCAR.")
    return erros


# ─────────────────────────────────────────────────────────────────────────────
# ORQUESTRAÇÃO DO LOTE (BATCH)  [FAT-63, DEC-3]
# ─────────────────────────────────────────────────────────────────────────────

def processar_linha_lote(
    row: Dict[str, str],
    verbose: bool = True,
    max_tentativas: int = 3,
    espera_base_s: float = 3.0,
    timeout_s: int = OVERPASS_TIMEOUT,
    usar_cache: bool = False,
    cache_ttl_s: float = 86400,
) -> Tuple[List[str], List[Dict]]:
    """
    Processa UMA linha do arquivo de lote: geometria → buffer/recorte →
    montagem das linhas SASCAR (sem escrever em disco).
    Reusa exatamente os módulos 2, 3 e 4 originais — nenhum invariante
    alterado. Retorna (linhas_sascar, registros_estruturados):
      - linhas_sascar: linhas SASCAR (PRI [+ PRE]) desta cerca — comportamento
        idêntico ao original.
      - registros_estruturados: mesma informação em forma de dict (codigo,
        tipo, seq, vertices, extensao_m), usada pelos Módulos 7/8
        (sobreposição/relatório, FAT-78) sem duplicar cálculo de geometria.

    `max_tentativas`, `espera_base_s` e `timeout_s` são repassados a
    `buscar_geometria_osm` (padrões idênticos ao comportamento anterior).  [FAT-119, S7]
    `usar_cache`/`cache_ttl_s` idem, para o cache local de geometrias.  [FAT-154, S4]
    """
    num_linha = row.get("_num_linha", "?")

    modo    = row["modo"].strip().upper()
    inicio  = parse_coord(row["inicio"])
    fim     = parse_coord(row["fim"]) if row.get("fim") else None
    comprimento_m = float(row["comprimento"]) if row.get("comprimento") else None
    pre_m   = float(row["pre"]) if row.get("pre") else 0.0
    pos_m   = float(row["pos"]) if row.get("pos") else 0.0
    buffer_m = float(row["buffer"]) if row.get("buffer") else 50.0

    if modo == "A" and fim is None:
        raise ValueError(f"Lote, linha {num_linha}: modo A requer coluna 'fim'.")
    if modo == "B" and comprimento_m is None:
        raise ValueError(f"Lote, linha {num_linha}: modo B requer coluna 'comprimento'.")

    if verbose:
        print(f"\n[LOTE — linha {num_linha}] modo {modo} | seq {row['seq']}")

    via       = (row.get("via") or "").strip()
    polilinha_manual = (row.get("polilinha") or "").strip()

    if polilinha_manual:
        polilinha = parse_polilinha_manual(polilinha_manual)
    else:
        ponto_bbox_fim = fim if fim else inicio
        polilinha = buscar_geometria_osm(
            via, inicio, ponto_bbox_fim, verbose,
            max_tentativas=max_tentativas, espera_base_s=espera_base_s, timeout_s=timeout_s,
            usar_cache=usar_cache, cache_ttl_s=cache_ttl_s,
        )
        if not polilinha:
            raise RuntimeError(
                f"Lote, linha {num_linha}: geometria não encontrada no OSM para '{via}'. "
                f"Use a coluna 'polilinha' para fallback manual. [FAT-24]"
            )

    cercas = gerar_cercas(
        polilinha=polilinha, modo=modo, inicio=inicio, fim=fim,
        comprimento_m=comprimento_m, pre_m=pre_m, pos_m=pos_m,
        buffer_m=buffer_m, verbose=verbose,
    )

    seq = int(row["seq"])
    linhas_sascar = _montar_linhas_cerca(
        cercas=cercas,
        rodovia=row["rodovia"], cidade=row["cidade"], uf=row["uf"],
        velocidade=int(row["velocidade"]), seq=seq,
    )

    registros_estruturados: List[Dict] = []
    for chave in ("PRI", "PRE"):
        dados = cercas.get(chave, {})
        vertices = dados.get("vertices", [])
        if not vertices:
            continue
        codigo = _montar_codigo(chave, row["rodovia"], row["cidade"], row["uf"], int(row["velocidade"]), seq)
        registros_estruturados.append({
            "codigo": codigo, "tipo": chave, "seq": seq,
            "vertices": vertices, "extensao_m": dados.get("extensao_m", 0),
            "rodovia": row["rodovia"], "cidade": row["cidade"], "uf": row["uf"],
            "velocidade": int(row["velocidade"]),
        })

    return linhas_sascar, registros_estruturados


def processar_lote(
    caminho_entrada: str,
    caminho_saida: str,
    verbose: bool = True,
    caminho_relatorio: Optional[str] = None,
    max_tentativas: int = 3,
    espera_base_s: float = 3.0,
    timeout_s: int = OVERPASS_TIMEOUT,
    usar_cache: bool = False,
    cache_ttl_s: float = 86400,
    caminho_historico: Optional[str] = None,
    paralelo: bool = False,
    max_workers: int = 4,
) -> Tuple[int, List[Dict], List[Tuple[str, str]]]:
    """
    Lê o arquivo de lote, gera todas as cercas e exporta UM único arquivo
    consolidado.  [FAT-63, DEC-3]
    Retorna (total_gravado, registros_estruturados, sobreposicoes):
      - total_gravado: número de registros SASCAR gravados (comportamento
        original, inalterado).
      - registros_estruturados: dados de cada cerca (codigo/tipo/seq/
        vertices/extensao_m), usados pelos Módulos 7/8 (FAT-78).
      - sobreposicoes: pares de código com sobreposição detectada (Módulo 7,
        S3) — lista vazia se `caminho_relatorio` não for informado ou se
        nenhuma sobreposição for encontrada. Não bloqueante (Opção A).
    Propaga o erro da primeira linha inválida (sem gravar arquivo parcial) —
    R6/R2: falha explícita, nunca resultado parcial silencioso.

    `max_tentativas`, `espera_base_s` e `timeout_s` são repassados a
    `processar_linha_lote` (padrões idênticos ao comportamento anterior).  [FAT-119, S7]
    `usar_cache`/`cache_ttl_s` idem, para o cache local de geometrias.  [FAT-154, S4]
    `caminho_historico`: se informado, grava `todos_registros` em um banco
    SQLite persistente entre execuções (`salvar_no_historico`).  Opcional;
    se omitido, nenhuma persistência ocorre.  [FAT-155, S5]

    `paralelo`/`max_workers`: se `paralelo=True`, processa as linhas do lote
    concorrentemente em até `max_workers` threads (útil quando a maior parte
    do tempo é gasta esperando o Overpass API). Desabilitado por padrão —
    sem `paralelo`, o processamento é sequencial, idêntico ao comportamento
    anterior. Em ambos os casos, `todas_linhas`/`todos_registros` preservam
    a ordem original das linhas do arquivo de lote, e o primeiro erro na
    ordem do arquivo é o que se propaga (mesma semântica de R6/R2).  [S9]
    """
    linhas_lote = ler_lote(caminho_entrada)
    if verbose:
        print(f"  ✓ {len(linhas_lote)} cerca(s) no arquivo de lote '{caminho_entrada}'.")

    todas_linhas: List[str] = []
    todos_registros: List[Dict] = []

    if paralelo:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(
                    processar_linha_lote, row, verbose=verbose,
                    max_tentativas=max_tentativas, espera_base_s=espera_base_s, timeout_s=timeout_s,
                    usar_cache=usar_cache, cache_ttl_s=cache_ttl_s,
                )
                for row in linhas_lote
            ]
            # `future.result()` na ordem de submissão (= ordem do arquivo) —
            # preserva ordem de saída e propaga o primeiro erro por linha,
            # não por ordem de conclusão das threads.
            for future in futures:
                linhas_sascar, registros = future.result()
                todas_linhas.extend(linhas_sascar)
                todos_registros.extend(registros)
    else:
        for row in linhas_lote:
            linhas_sascar, registros = processar_linha_lote(
                row, verbose=verbose,
                max_tentativas=max_tentativas, espera_base_s=espera_base_s, timeout_s=timeout_s,
                usar_cache=usar_cache, cache_ttl_s=cache_ttl_s,
            )
            todas_linhas.extend(linhas_sascar)
            todos_registros.extend(registros)

    total = exportar_lote(todas_linhas, caminho_saida, verbose=verbose)

    sobreposicoes: List[Tuple[str, str]] = []
    if caminho_relatorio:
        if verbose:
            print(f"\n[MÓDULO 7] Verificando sobreposição entre polígonos...  [FAT-78, S3]")
        sobreposicoes = detectar_sobreposicao(todos_registros)
        if verbose:
            print(f"\n[MÓDULO 8] Gerando relatório '{caminho_relatorio}'...  [FAT-78, S5]")
        gerar_relatorio(todos_registros, caminho_relatorio, sobreposicoes, verbose=verbose)

    if caminho_historico:
        if verbose:
            print(f"\n[MÓDULO 9] Gravando histórico '{caminho_historico}'...  [FAT-155, S5]")
        salvar_no_historico(todos_registros, caminho_historico, verbose=verbose)

    return total, todos_registros, sobreposicoes


# ─────────────────────────────────────────────────────────────────────────────
# MÓDULO 7 — DETECÇÃO DE SOBREPOSIÇÃO ENTRE POLÍGONOS  [FAT-78, S3]
# ─────────────────────────────────────────────────────────────────────────────
#
# Endereça a Premissa P7 (buffer PRE pode sobrepor polígonos de vias
# paralelas). Ação em caso de sobreposição = Opção A: apenas alerta,
# sem bloqueio e sem pausa interativa (compatível com --batch).
# Não altera geometria, buffer, recorte nem exportação — apenas analisa
# a saída já calculada pelos módulos 3/4. [Decisão registrada na SES-15]

def _construir_poligono(vertices: List[Tuple[float, float]]) -> Optional[Polygon]:
    """Constrói um Polygon Shapely (lon, lat) a partir dos vértices (lat, lon)
    de uma cerca já gerada. Usado apenas para análise de sobreposição —
    não participa do cálculo de buffer/recorte."""
    if len(vertices) < 3:
        return None
    try:
        return Polygon([(lon, lat) for lat, lon in vertices])
    except Exception:
        return None


def detectar_sobreposicao(registros: List[Dict]) -> List[Tuple[str, str]]:
    """
    Compara os polígonos de cercas DIFERENTES (SEQ diferente) e retorna os
    pares de CÓDIGO que se sobrepõem.  [FAT-78, S3]

    PRI e PRE do MESMO conjunto (mesmo SEQ) sempre se sobrepõem no segmento
    central por definição de arquitetura (FAT-39) — isso não é uma anomalia
    e é excluído da checagem.

    `registros`: lista de dicts com pelo menos 'codigo', 'seq', 'vertices'.
    Não bloqueia nem interrompe a execução — apenas retorna os pares
    encontrados para o chamador decidir o que fazer (alerta/relatório).
    """
    pares_sobrepostos: List[Tuple[str, str]] = []
    candidatos = [
        (r, _construir_poligono(r.get("vertices", [])))
        for r in registros if r.get("vertices")
    ]

    for i in range(len(candidatos)):
        r1, p1 = candidatos[i]
        if p1 is None:
            continue
        for j in range(i + 1, len(candidatos)):
            r2, p2 = candidatos[j]
            if p2 is None:
                continue
            if r1.get("seq") == r2.get("seq"):
                continue  # PRI/PRE do mesmo conjunto — sobreposição esperada [FAT-39]
            try:
                if p1.intersects(p2):
                    pares_sobrepostos.append((r1["codigo"], r2["codigo"]))
            except Exception:
                continue

    return pares_sobrepostos


# ─────────────────────────────────────────────────────────────────────────────
# MÓDULO 8 — RELATÓRIO DE CERCAS GERADAS  [FAT-78, S5]
# ─────────────────────────────────────────────────────────────────────────────
#
# Relatório tabular (CSV) resumindo as cercas geradas em uma execução
# (single-cerca ou lote). Consome apenas dados já produzidos pelos módulos
# 3/4/5 — não recalcula geometria nem altera o arquivo de exportação SASCAR.

def gerar_relatorio(
    registros: List[Dict],
    caminho: str,
    sobreposicoes: Optional[List[Tuple[str, str]]] = None,
    verbose: bool = True,
) -> int:
    """
    Grava um relatório CSV com o resumo das cercas geradas nesta execução.
    [FAT-78, S5]

    Colunas: codigo, tipo, extensao_m, vertice_inicial, vertice_final,
    num_vertices. Se houver sobreposições (Módulo 7), acrescenta uma seção
    de alertas ao final — apenas informativa, não bloqueante (Opção A).

    Retorna o número de cercas (linhas) registradas no relatório.
    """
    with open(caminho, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(
            ["codigo", "tipo", "extensao_m", "vertice_inicial", "vertice_final", "num_vertices"]
        )
        n = 0
        for r in registros:
            vertices = r.get("vertices", [])
            if not vertices:
                continue
            v_ini = f"{vertices[0][0]:.6f},{vertices[0][1]:.6f}"
            v_fim = f"{vertices[-1][0]:.6f},{vertices[-1][1]:.6f}"
            writer.writerow([
                r.get("codigo", ""), r.get("tipo", ""), r.get("extensao_m", 0),
                v_ini, v_fim, len(vertices),
            ])
            n += 1

        if sobreposicoes:
            writer.writerow([])
            writer.writerow(["ALERTA DE SOBREPOSICAO (S3) - nao bloqueante"])
            writer.writerow(["codigo_a", "relacao", "codigo_b"])
            for a, b in sobreposicoes:
                writer.writerow([a, "sobrepoe", b])

    if verbose:
        msg = f"  ✓ Relatório gravado em '{caminho}' ({n} cerca(s))."
        if sobreposicoes:
            msg += f"  ⚠ {len(sobreposicoes)} sobreposição(ões) detectada(s) — ver relatório."
        print(msg)

    return n


# ─────────────────────────────────────────────────────────────────────────────
# MÓDULO 9 — HISTÓRICO PERSISTENTE (SQLITE)  [FAT-155, S5]
# ─────────────────────────────────────────────────────────────────────────────
#
# Camada aditiva e opcional (--historico): mantém um registro das cercas
# geradas entre execuções distintas, sem alterar o formato de exportação
# SASCAR nem o relatório por execução (Módulo 8). Duplicidade de CÓDIGO
# entre execuções gera apenas um alerta não bloqueante (mesmo padrão do
# Módulo 7 — Opção A), nunca erro fatal.

def _historico_criar_tabela(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cercas (
            codigo           TEXT,
            tipo             TEXT,
            rodovia          TEXT,
            cidade           TEXT,
            uf               TEXT,
            velocidade       INTEGER,
            seq              INTEGER,
            extensao_m       REAL,
            vertice_inicial  TEXT,
            vertice_final    TEXT,
            num_vertices     INTEGER,
            data_criacao     TEXT,
            execucao_id      TEXT
        )
    """)


def salvar_no_historico(
    registros: List[Dict],
    caminho_db: str,
    execucao_id: Optional[str] = None,
    verbose: bool = True,
) -> int:
    """
    Grava `registros` (mesmo formato usado por `gerar_relatorio`, mais
    rodovia/cidade/uf/velocidade) na tabela `cercas` de `caminho_db`
    (SQLite, criado se não existir). [FAT-155, S5]

    Antes de gravar cada registro, checa se o CÓDIGO já existe no histórico
    (de qualquer execução anterior); se sim, imprime um alerta não
    bloqueante (Opção A, mesmo padrão do Módulo 7) e grava mesmo assim —
    o histórico é um log de execuções, não um índice único de CÓDIGO.

    Retorna o número de registros gravados.
    """
    if execucao_id is None:
        execucao_id = datetime.now().strftime("%Y%m%d%H%M%S")
    data_criacao = datetime.now().isoformat(timespec="seconds")

    conn = sqlite3.connect(caminho_db)
    try:
        _historico_criar_tabela(conn)
        n = 0
        for r in registros:
            vertices = r.get("vertices", [])
            if not vertices:
                continue
            codigo = r.get("codigo", "")

            existe = conn.execute(
                "SELECT 1 FROM cercas WHERE codigo = ? LIMIT 1", (codigo,)
            ).fetchone()
            if existe and verbose:
                print(f"  ⚠  ALERTA: CÓDIGO '{codigo}' já existe no histórico "
                      f"'{caminho_db}' (execução anterior). [FAT-155, S5]")

            v_ini = f"{vertices[0][0]:.6f},{vertices[0][1]:.6f}"
            v_fim = f"{vertices[-1][0]:.6f},{vertices[-1][1]:.6f}"
            conn.execute(
                "INSERT INTO cercas (codigo, tipo, rodovia, cidade, uf, velocidade, "
                "seq, extensao_m, vertice_inicial, vertice_final, num_vertices, "
                "data_criacao, execucao_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    codigo, r.get("tipo", ""), r.get("rodovia", ""), r.get("cidade", ""),
                    r.get("uf", ""), r.get("velocidade"), r.get("seq"),
                    r.get("extensao_m", 0), v_ini, v_fim, len(vertices),
                    data_criacao, execucao_id,
                ),
            )
            n += 1
        conn.commit()
    finally:
        conn.close()

    if verbose:
        print(f"  ✓ {n} registro(s) gravado(s) no histórico '{caminho_db}'.  [FAT-155, S5]")

    return n


def consultar_historico(caminho_db: str, filtros: Optional[Dict[str, str]] = None) -> List[Dict]:
    """
    Consulta a tabela `cercas` de `caminho_db`, filtrando por qualquer
    combinação de rodovia/uf/codigo (filtros ignorados se ausentes/vazios).
    [FAT-155, S5]

    Retorna uma lista de dicts, um por linha encontrada (mesmas colunas da
    tabela `cercas`). Retorna lista vazia se o arquivo não existir.
    """
    if not os.path.exists(caminho_db):
        return []

    filtros = filtros or {}
    condicoes = []
    valores = []
    for coluna in ("rodovia", "uf", "codigo"):
        valor = filtros.get(coluna)
        if valor:
            condicoes.append(f"{coluna} = ?")
            valores.append(valor)

    query = "SELECT * FROM cercas"
    if condicoes:
        query += " WHERE " + " AND ".join(condicoes)

    conn = sqlite3.connect(caminho_db)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(query, valores)
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# CLI — ORQUESTRAÇÃO DOS 6 MÓDULOS
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Cercas Eletrônicas — geração padronizada para importação SASCAR  [FAT-5]\n\n"
            "Exemplos:\n"
            "  # Modo A (coords): via OSM, saída CSV\n"
            "  python cercas.py --via BR-116 --modo A \\\n"
            "    --inicio -25.38,-49.19 --fim -25.40,-49.16 \\\n"
            "    --pre 300 --pos 300 \\\n"
            "    --rodovia BR-116 --cidade LUZ --uf MG --velocidade 60 --seq 1\n\n"
            "  # Saída esperada (campo 2): PRI - BR-116 - LUZ_MG - 60 KmH - 001\n\n"
            "  # Modo B (comprimento): polilinha manual, saída TXT\n"
            "  python cercas.py --polilinha '-25.38,-49.19;-25.39,-49.18;-25.40,-49.16' \\\n"
            "    --modo B --inicio -25.38,-49.19 --comprimento 2000 \\\n"
            "    --pre 500 --pos 500 \\\n"
            "    --rodovia 'Av Brasil' --cidade BLUMENAU --uf SC \\\n"
            "    --velocidade 40 --seq 1 --formato txt\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("--batch",
                        help="Caminho de um arquivo CSV de lote — gera múltiplas cercas "
                             "e exporta um único arquivo consolidado. Mutuamente exclusivo "
                             "com o fluxo single-cerca abaixo. [FAT-63, DEC-3]")

    grp_via = parser.add_mutually_exclusive_group(required=False)
    grp_via.add_argument("--via",
                         help="Referência ou nome da via no OSM (ex.: 'BR-116'). [FAT-24]")
    grp_via.add_argument("--polilinha",
                         help="Fallback manual: 'lat1,lon1;lat2,lon2;...' [FAT-24]")

    parser.add_argument("--modo", choices=["A", "B"],
                        help="A = par de coords (--inicio + --fim). "
                             "B = início + comprimento (--inicio + --comprimento). "
                             "Obrigatório fora do modo --batch. [FAT-15, FAT-28]")
    parser.add_argument("--inicio",
                        help="Coordenada de início 'lat,lon'. Obrigatório fora do modo --batch.")
    parser.add_argument("--fim",
                        help="Coordenada de fim 'lat,lon'. Obrigatório no Modo A. [FAT-15]")
    parser.add_argument("--comprimento", type=float,
                        help="Comprimento da cerca em metros. Obrigatório no Modo B. [FAT-28]")

    parser.add_argument("--pre", type=float, default=0.0,
                        help="Extensão da précerca antes do início (metros). Padrão: 0. [FAT-29]")
    parser.add_argument("--pos", type=float, default=0.0,
                        help="Extensão da précerca após o fim (metros). Padrão: 0. [FAT-29]")

    parser.add_argument("--buffer", type=float, default=50.0,
                        help="Meio-largura do buffer em metros. Padrão: 50 m (= 100 m total). "
                             "[FAT-14]")

    # Metadados do CÓDIGO  [FAT-32] — obrigatórios fora do modo --batch
    parser.add_argument("--rodovia",
                        help="Rodovia (ex.: BR-116, SC-470) ou nome de rua (livre). [FAT-32]")
    parser.add_argument("--cidade",
                        help="Cidade sem acentos (ex.: LUZ, BLUMENAU). [FAT-32]")
    parser.add_argument("--uf",
                        help="Sigla do estado, 2 letras (ex.: MG, SC). [FAT-32]")
    parser.add_argument("--velocidade", type=int,
                        help="Velocidade máxima em KmH (inteiro). [FAT-32, FAT-35]")
    parser.add_argument("--seq", type=int, default=1,
                        help="Sequencial 1–999; PRI e PRE do mesmo conjunto "
                             "compartilham o mesmo SEQ. Padrão: 1. [FAT-32]")

    # Formato de saída  [FAT-34]
    parser.add_argument("--formato", choices=["csv", "txt"], default="csv",
                        help="Extensão do arquivo de saída: csv ou txt. Padrão: csv. [FAT-34]")
    parser.add_argument("--saida", default=None,
                        help="Caminho completo do arquivo de saída. Se omitido, "
                             "usa 'cercas.csv' ou 'cercas.txt' conforme --formato.")

    parser.add_argument("--silencioso", action="store_true",
                        help="Suprime mensagens de progresso.")

    parser.add_argument("--relatorio", default=None,
                        help="Caminho de um relatório CSV com o resumo das cercas geradas "
                             "e alertas de sobreposição entre polígonos (não bloqueante). "
                             "Opcional; se omitido, nenhum relatório é gerado. [FAT-78, S5/S3]")

    # Retry de rede configurável (Overpass API)  [FAT-119, S7]
    parser.add_argument("--retry-tentativas", type=int, default=3,
                        help="Número de tentativas ao consultar o Overpass API. Padrão: 3. [S7]")
    parser.add_argument("--retry-espera", type=float, default=3.0,
                        help="Espera em segundos entre tentativas. Padrão: 3.0. [S7]")
    parser.add_argument("--retry-timeout", type=int, default=OVERPASS_TIMEOUT,
                        help=f"Timeout em segundos por requisição ao Overpass API. "
                             f"Padrão: {OVERPASS_TIMEOUT}. [S7]")

    # Cache local de geometrias OSM  [FAT-154, S4]
    parser.add_argument("--cache", action="store_true",
                        help="Reaproveita geometrias já buscadas no OSM (arquivo local "
                             "'.osm_cache.json'). Desabilitado por padrão. [S4]")
    parser.add_argument("--cache-ttl", type=int, default=86400,
                        help="Tempo de vida do cache em segundos. Padrão: 86400 (24h). [S4]")

    # Histórico persistente entre execuções  [FAT-155, S5]
    parser.add_argument("--historico", default=None,
                        help="Caminho de um banco SQLite onde as cercas geradas são "
                             "registradas entre execuções distintas. Opcional; se omitido, "
                             "nenhuma persistência ocorre. [S5]")

    # Processamento paralelo do lote  [S9]
    parser.add_argument("--paralelo", action="store_true",
                        help="Processa as linhas do --batch concorrentemente (ThreadPoolExecutor). "
                             "Desabilitado por padrão (processamento sequencial). [S9]")
    parser.add_argument("--paralelo-workers", type=int, default=4,
                        help="Número máximo de threads quando --paralelo está ativo. Padrão: 4. [S9]")

    args    = parser.parse_args()
    verbose = not args.silencioso

    # ── Bifurcação: modo --batch vs. modo single-cerca (v1.4 inalterado) ──────
    if args.batch:
        if any([args.via, args.polilinha, args.modo, args.inicio, args.fim,
                args.comprimento, args.rodovia, args.cidade, args.uf, args.velocidade]):
            parser.error("--batch é mutuamente exclusivo com os argumentos de cerca única "
                          "(--via/--polilinha/--modo/--inicio/--fim/--comprimento/"
                          "--rodovia/--cidade/--uf/--velocidade). [FAT-63]")

        caminho_saida = args.saida if args.saida else f"cercas_lote.{args.formato}"

        if verbose:
            print(f"\n[MÓDULO 1B] Lendo arquivo de lote '{args.batch}'...  [FAT-63]")
        try:
            n, _registros, _sobreposicoes = processar_lote(
                args.batch, caminho_saida, verbose=verbose,
                caminho_relatorio=args.relatorio,
                max_tentativas=args.retry_tentativas,
                espera_base_s=args.retry_espera,
                timeout_s=args.retry_timeout,
                usar_cache=args.cache,
                cache_ttl_s=args.cache_ttl,
                caminho_historico=args.historico,
                paralelo=args.paralelo,
                max_workers=args.paralelo_workers,
            )
        except (ValueError, RuntimeError) as e:
            print(f"\nERRO no processamento do lote: {e}", file=sys.stderr)
            sys.exit(1)

        if verbose:
            print(f"\n[MÓDULO 6] Validando '{caminho_saida}'...")
        erros = validar_csv(caminho_saida, verbose)
        if erros:
            sys.exit(2)

        if verbose:
            print(f"\n{'='*60}")
            print(f"  Arquivo de lote '{caminho_saida}' pronto para importação no SASCAR.")
            print(f"  Registros gerados: {n}")
            print(f"{'='*60}\n")
        return

    # ── A partir daqui: fluxo single-cerca, idêntico à v1.4 ───────────────────
    if not args.via and not args.polilinha:
        parser.error("Informe --via, --polilinha, ou --batch.")
    if not args.modo:
        parser.error("--modo é obrigatório fora do modo --batch.")
    if not args.inicio:
        parser.error("--inicio é obrigatório fora do modo --batch.")
    if not args.rodovia or not args.cidade or not args.uf or args.velocidade is None:
        parser.error("--rodovia, --cidade, --uf e --velocidade são obrigatórios "
                      "fora do modo --batch.")

    caminho_saida = args.saida if args.saida else f"cercas.{args.formato}"

    # ── Módulo 1: Validação ────────────────────────────────────────────────────
    if verbose:
        print("\n[MÓDULO 1] Validando entrada...")

    inicio = parse_coord(args.inicio)
    fim    = None

    if args.modo == "A":
        if not args.fim:
            parser.error("Modo A requer --fim.")
        fim = parse_coord(args.fim)
    elif args.modo == "B":
        if not args.comprimento or args.comprimento <= 0:
            parser.error("Modo B requer --comprimento > 0.")

    if not 1 <= args.seq <= 999:
        parser.error("--seq deve estar entre 1 e 999.")

    if len(args.uf) != 2:
        parser.error("--uf deve ter exatamente 2 letras (ex.: MG, SC).")

    try:
        _validar_rodovia(args.rodovia)
    except ValueError as e:
        parser.error(str(e))

    if verbose:
        print(f"  ✓ Modo {args.modo} | início: {inicio} | "
              f"{'fim: ' + str(fim) if fim else 'comprimento: ' + str(args.comprimento) + ' m'}")
        print(f"  ✓ Pré: {args.pre} m | Pós: {args.pos} m | "
              f"Buffer: {args.buffer} m/lado ({args.buffer*2:.0f} m total)")
        print(f"  ✓ CÓDIGO base: {args.rodovia} / {args.cidade}/{args.uf.upper()} / "
              f"{args.velocidade} KmH / SEQ {args.seq:03d}  [FAT-32, FAT-35]")

    # ── Módulo 2: Geometria ────────────────────────────────────────────────────
    if verbose:
        print("\n[MÓDULO 2] Obtendo geometria da via...")

    if args.polilinha:
        if verbose:
            print("  → Modo fallback: polilinha manual. [FAT-24]")
        polilinha = parse_polilinha_manual(args.polilinha)
        if verbose:
            print(f"  ✓ {len(polilinha)} ponto(s) carregado(s).")
    else:
        ponto_bbox_fim = fim if fim else inicio
        polilinha = buscar_geometria_osm(
            args.via, inicio, ponto_bbox_fim, verbose,
            max_tentativas=args.retry_tentativas,
            espera_base_s=args.retry_espera,
            timeout_s=args.retry_timeout,
            usar_cache=args.cache,
            cache_ttl_s=args.cache_ttl,
        )
        if not polilinha:
            print("\nERRO: geometria não encontrada no OSM. "
                  "Use --polilinha para inserir a polilinha manualmente.",
                  file=sys.stderr)
            sys.exit(1)

    # ── Módulos 3+4: Recorte e variantes ──────────────────────────────────────
    if verbose:
        print("\n[MÓDULO 3/4] Recortando trecho e gerando variantes...")

    try:
        cercas = gerar_cercas(
            polilinha     = polilinha,
            modo          = args.modo,
            inicio        = inicio,
            fim           = fim,
            comprimento_m = args.comprimento if args.modo == "B" else None,
            pre_m         = args.pre,
            pos_m         = args.pos,
            buffer_m      = args.buffer,
            verbose       = verbose,
        )
    except ValueError as e:
        print(f"\nERRO: {e}", file=sys.stderr)
        sys.exit(1)

    if verbose:
        for chave, dados in cercas.items():
            verts = dados.get("vertices", [])
            ext   = dados.get("extensao_m", 0)
            status = f"{len(verts)} vértices, {ext} m" if verts else "não gerada (pré/pós = 0)"
            print(f"  ✓ {chave}: {status}")

    # ── Módulo 5: Exportação ───────────────────────────────────────────────────
    if verbose:
        print(f"\n[MÓDULO 5] Exportando para '{caminho_saida}'...  [FAT-34]")

    n = exportar_csv(
        cercas     = cercas,
        rodovia    = args.rodovia,
        cidade     = args.cidade,
        uf         = args.uf,
        velocidade = args.velocidade,
        seq        = args.seq,
        caminho    = caminho_saida,
        verbose    = verbose,
    )

    # ── Módulo 6: Validação ────────────────────────────────────────────────────
    if verbose:
        print(f"\n[MÓDULO 6] Validando '{caminho_saida}'...")

    erros = validar_csv(caminho_saida, verbose)

    if erros:
        sys.exit(2)

    if args.relatorio or args.historico:
        registros_relatorio: List[Dict] = []
        for chave, dados in cercas.items():
            verts = dados.get("vertices", [])
            if not verts:
                continue
            codigo = _montar_codigo(chave, args.rodovia, args.cidade, args.uf, args.velocidade, args.seq)
            registros_relatorio.append({
                "codigo": codigo, "tipo": chave, "seq": args.seq,
                "vertices": verts, "extensao_m": dados.get("extensao_m", 0),
                "rodovia": args.rodovia, "cidade": args.cidade, "uf": args.uf,
                "velocidade": args.velocidade,
            })

        if args.relatorio:
            if verbose:
                print(f"\n[MÓDULO 7] Verificando sobreposição entre polígonos...  [FAT-78, S3]")
            sobreposicoes = detectar_sobreposicao(registros_relatorio)
            if verbose:
                print(f"\n[MÓDULO 8] Gerando relatório '{args.relatorio}'...  [FAT-78, S5]")
            gerar_relatorio(registros_relatorio, args.relatorio, sobreposicoes, verbose=verbose)

        if args.historico:
            if verbose:
                print(f"\n[MÓDULO 9] Gravando histórico '{args.historico}'...  [FAT-155, S5]")
            salvar_no_historico(registros_relatorio, args.historico, verbose=verbose)

    if verbose:
        print(f"\n{'='*60}")
        print(f"  Arquivo '{caminho_saida}' pronto para importação no SASCAR.")
        print(f"  Registros gerados: {n}")
        print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
