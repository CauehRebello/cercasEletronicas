"""
verificar_cobertura_testes.py — S8: Verificação Automatizada de Cobertura de Testes

Compara uma alegacao de contagem de testes (ex.: feita pelo Claude Code em
changelog/relatorio) contra a contagem real de funcoes `def test_*` em
test_cercas.py e test_cercas_pg_integration.py.

Ferramenta de auditoria de processo, isolada do sistema: nao importa nem
modifica cercas_v2.py, cercas_gui.py ou os arquivos de teste.

Uso:
    python verificar_cobertura_testes.py --alegacao "73"
    python verificar_cobertura_testes.py --alegacao "73 testes, 73 passando"
    python verificar_cobertura_testes.py --arquivo-alegacao changelog.txt

Exit codes:
    0 - contagem alegada bate com a contagem real
    1 - contagem alegada diverge da contagem real
    2 - erro de uso/entrada (nenhuma flag informada, numero nao encontrado, etc.)
"""

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path

ARQUIVOS_TESTE = ["test_cercas.py", "test_cercas_pg_integration.py"]


def contar_testes_ast(caminho: Path) -> int:
    arvore = ast.parse(caminho.read_text(encoding="utf-8"), filename=str(caminho))
    total = 0
    for no in ast.walk(arvore):
        if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)) and no.name.startswith("test_"):
            total += 1
    return total


def contar_testes_pytest(caminho: Path) -> int | None:
    try:
        resultado = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", str(caminho)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if resultado.returncode not in (0, 1, 5):
        # 0 = coletou e nao rodou nada de errado, 1 = houve erro de coleta em
        # algum teste (ainda assim pode ter coletado outros), 5 = nenhum item
        # coletado. Qualquer outro codigo indica falha inesperada.
        return None

    saida = resultado.stdout
    match = re.search(r"^(\d+) tests? collected", saida, flags=re.MULTILINE)
    if match:
        return int(match.group(1))

    match_erro = re.search(r"^(\d+) errors?", saida, flags=re.MULTILINE)
    if match_erro:
        return None

    if "no tests collected" in saida.lower() or "no tests ran" in saida.lower():
        return 0

    return None


def contar_testes_arquivo(caminho: Path) -> int:
    contagem = contar_testes_pytest(caminho)
    if contagem is not None:
        return contagem
    return contar_testes_ast(caminho)


def extrair_numero_alegacao(texto: str) -> int | None:
    numeros = re.findall(r"\d+", texto)
    if not numeros:
        return None
    return int(numeros[0])


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verifica uma alegacao de contagem de testes contra a contagem real "
        "de funcoes def test_* em test_cercas.py e test_cercas_pg_integration.py."
    )
    parser.add_argument("--alegacao", help='Numero ou frase livre contendo a alegacao, ex.: "73" ou "73 testes, 73 passando".')
    parser.add_argument("--arquivo-alegacao", help="Caminho de um arquivo de texto/changelog contendo a alegacao.")
    args = parser.parse_args()

    if not args.alegacao and not args.arquivo_alegacao:
        print("ERRO: informe --alegacao ou --arquivo-alegacao.", file=sys.stderr)
        return 2

    if args.alegacao and args.arquivo_alegacao:
        print("Aviso: ambas as flags foram informadas; usando --alegacao.", file=sys.stderr)

    if args.alegacao:
        texto_alegacao = args.alegacao
    else:
        caminho_alegacao = Path(args.arquivo_alegacao)
        if not caminho_alegacao.is_file():
            print(f"ERRO: arquivo de alegacao nao encontrado: {caminho_alegacao}", file=sys.stderr)
            return 2
        texto_alegacao = caminho_alegacao.read_text(encoding="utf-8")

    numero_alegado = extrair_numero_alegacao(texto_alegacao)
    if numero_alegado is None:
        print(f"ERRO: nenhum numero encontrado na alegacao: {texto_alegacao!r}", file=sys.stderr)
        return 2

    raiz_projeto = Path(__file__).resolve().parent
    contagens_por_arquivo = {}
    for nome_arquivo in ARQUIVOS_TESTE:
        caminho = raiz_projeto / nome_arquivo
        if not caminho.is_file():
            print(f"ERRO: arquivo de teste nao encontrado: {caminho}", file=sys.stderr)
            return 2
        contagens_por_arquivo[nome_arquivo] = contar_testes_arquivo(caminho)

    total_real = sum(contagens_por_arquivo.values())

    print("Contagem real de testes:")
    for nome_arquivo, contagem in contagens_por_arquivo.items():
        print(f"  {nome_arquivo}: {contagem}")
    print(f"  TOTAL: {total_real}")
    print(f"Alegacao: {numero_alegado}")

    if numero_alegado == total_real:
        print(f"OK: a alegacao ({numero_alegado}) confere com a contagem real ({total_real}).")
        return 0

    print(
        f"DIVERGENCIA: alegado={numero_alegado} vs real={total_real} "
        f"(diferenca={numero_alegado - total_real})."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
