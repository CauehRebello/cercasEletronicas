"""
GUI opcional para o sistema de Cercas Eletrônicas.  [FAT-153, S1]

Não duplica lógica de negócio: monta os mesmos argumentos que seriam
passados na linha de comando e invoca `cercas_v2.main()` diretamente —
o mesmo código que roda no fluxo CLI. `cercas_v2.py` continua funcionando
de forma independente e inalterada; esta GUI é apenas um consumidor
externo do módulo (Cláusula V — aditivo, não substitutivo).
"""
import contextlib
import io
import sys
import tkinter as tk
from tkinter import filedialog, scrolledtext, ttk

import cercas_v2


class CercasGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Cercas Eletrônicas — Transleone")
        self.resizable(True, True)

        self.usar_lote = tk.BooleanVar(value=False)
        self.campos = {}

        self._construir_form()

    # ── Construção do formulário ───────────────────────────────────────────
    def _construir_form(self):
        container = ttk.Frame(self, padding=10)
        container.pack(fill="both", expand=True)

        ttk.Checkbutton(
            container, text="Modo lote (arquivo CSV com --batch)",
            variable=self.usar_lote, command=self._atualizar_visibilidade,
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        linha = 1
        linha = self._campo_arquivo(container, linha, "batch", "Arquivo de lote (CSV)")

        linha = self._campo_texto(container, linha, "via", "Via (--via, ex.: BR-116)")
        linha = self._campo_texto(container, linha, "polilinha", "Polilinha manual (--polilinha)")
        linha = self._campo_combo(container, linha, "modo", "Modo", ["", "A", "B"])
        linha = self._campo_texto(container, linha, "inicio", "Início (lat,lon)")
        linha = self._campo_texto(container, linha, "fim", "Fim (lat,lon) — Modo A")
        linha = self._campo_texto(container, linha, "comprimento", "Comprimento (m) — Modo B")
        linha = self._campo_texto(container, linha, "pre", "Pré (m)")
        linha = self._campo_texto(container, linha, "pos", "Pós (m)")
        linha = self._campo_texto(container, linha, "buffer", "Buffer (m/lado)")
        linha = self._campo_texto(container, linha, "rodovia", "Rodovia")
        linha = self._campo_texto(container, linha, "cidade", "Cidade")
        linha = self._campo_texto(container, linha, "uf", "UF")
        linha = self._campo_texto(container, linha, "velocidade", "Velocidade (KmH)")
        linha = self._campo_texto(container, linha, "seq", "SEQ")

        self._linha_single_cerca_fim = linha

        linha = self._campo_combo(container, linha, "formato", "Formato de saída", ["csv", "txt"])
        linha = self._campo_arquivo(container, linha, "saida", "Arquivo de saída", salvar=True)
        linha = self._campo_arquivo(container, linha, "relatorio", "Relatório (CSV)", salvar=True)

        ttk.Label(container, text="Retry de rede (S7)").grid(row=linha, column=0, sticky="w", pady=(8, 0))
        linha += 1
        linha = self._campo_texto(container, linha, "retry_tentativas", "Tentativas")
        linha = self._campo_texto(container, linha, "retry_espera", "Espera entre tentativas (s)")
        linha = self._campo_texto(container, linha, "retry_timeout", "Timeout por requisição (s)")

        ttk.Label(container, text="Cache local de geometrias (S4)").grid(row=linha, column=0, sticky="w", pady=(8, 0))
        linha += 1
        self.campos["cache"] = tk.BooleanVar(value=False)
        ttk.Checkbutton(container, text="Habilitar cache", variable=self.campos["cache"]).grid(
            row=linha, column=0, columnspan=2, sticky="w"
        )
        linha += 1
        linha = self._campo_texto(container, linha, "cache_ttl", "TTL do cache (s)")

        ttk.Label(container, text="Histórico persistente (S5)").grid(row=linha, column=0, sticky="w", pady=(8, 0))
        linha += 1
        linha = self._campo_arquivo(container, linha, "historico", "Banco de histórico (.db)", salvar=True)

        ttk.Label(container, text="Base central — duplicidade de CÓDIGO (Bloco A v4)").grid(
            row=linha, column=0, sticky="w", pady=(8, 0)
        )
        linha += 1
        linha = self._campo_texto(container, linha, "pg_dsn", "DSN PostgreSQL (--pg-dsn)")
        self.campos["substituir"] = tk.BooleanVar(value=False)
        ttk.Checkbutton(container, text="Substituir cerca existente (--substituir)",
                        variable=self.campos["substituir"]).grid(
            row=linha, column=0, columnspan=2, sticky="w"
        )
        linha += 1
        self.campos["confirmar_substituicao"] = tk.BooleanVar(value=False)
        ttk.Checkbutton(container, text="Confirmar substituição (--confirmar-substituicao)",
                        variable=self.campos["confirmar_substituicao"]).grid(
            row=linha, column=0, columnspan=2, sticky="w"
        )
        linha += 1
        linha = self._campo_texto(container, linha, "motivo_substituicao", "Motivo da substituição (opcional)")

        ttk.Label(container, text="Sobreposição geométrica (Bloco B v4)").grid(
            row=linha, column=0, sticky="w", pady=(8, 0)
        )
        linha += 1
        linha = self._campo_texto(container, linha, "limiar_sobreposicao",
                                   "Limiar de bloqueio (0–1, padrão 0.90)")
        ttk.Label(container, text="Overrides (1 por linha: CODIGO:CODIGO:justificativa)").grid(
            row=linha, column=0, sticky="w"
        )
        linha += 1
        self.overrides_texto = scrolledtext.ScrolledText(container, width=60, height=3)
        self.overrides_texto.grid(row=linha, column=0, columnspan=3, sticky="we", padx=4)
        linha += 1

        botoes = ttk.Frame(container)
        botoes.grid(row=linha, column=0, columnspan=3, pady=10, sticky="w")
        ttk.Button(botoes, text="Gerar cerca(s)", command=self._executar).pack(side="left")
        linha += 1

        ttk.Label(container, text="Saída:").grid(row=linha, column=0, sticky="w")
        linha += 1
        self.saida_texto = scrolledtext.ScrolledText(container, width=90, height=20)
        self.saida_texto.grid(row=linha, column=0, columnspan=3, sticky="nsew")
        container.rowconfigure(linha, weight=1)

        self._atualizar_visibilidade()

    def _campo_texto(self, container, linha, chave, rotulo):
        ttk.Label(container, text=rotulo).grid(row=linha, column=0, sticky="w")
        var = tk.StringVar()
        entrada = ttk.Entry(container, textvariable=var, width=40)
        entrada.grid(row=linha, column=1, sticky="we", padx=4)
        self.campos[chave] = var
        setattr(self, f"_widget_{chave}", (entrada,))
        return linha + 1

    def _campo_combo(self, container, linha, chave, rotulo, opcoes):
        ttk.Label(container, text=rotulo).grid(row=linha, column=0, sticky="w")
        var = tk.StringVar()
        combo = ttk.Combobox(container, textvariable=var, values=opcoes, width=37, state="readonly")
        combo.grid(row=linha, column=1, sticky="we", padx=4)
        self.campos[chave] = var
        setattr(self, f"_widget_{chave}", (combo,))
        return linha + 1

    def _campo_arquivo(self, container, linha, chave, rotulo, salvar=False):
        ttk.Label(container, text=rotulo).grid(row=linha, column=0, sticky="w")
        var = tk.StringVar()
        entrada = ttk.Entry(container, textvariable=var, width=40)
        entrada.grid(row=linha, column=1, sticky="we", padx=4)

        def escolher():
            caminho = filedialog.asksaveasfilename() if salvar else filedialog.askopenfilename()
            if caminho:
                var.set(caminho)

        botao = ttk.Button(container, text="Procurar...", command=escolher)
        botao.grid(row=linha, column=2, sticky="w")
        self.campos[chave] = var
        setattr(self, f"_widget_{chave}", (entrada, botao))
        return linha + 1

    def _atualizar_visibilidade(self):
        """Mostra os campos de cerca única OU de lote, espelhando a exclusividade
        mútua já validada por `main()` (--batch vs --via/--modo/...). [FAT-63]"""
        lote = self.usar_lote.get()
        for w in getattr(self, "_widget_batch", ()):
            w.grid() if lote else w.grid_remove()
        for chave in ("via", "polilinha", "modo", "inicio", "fim", "comprimento",
                      "pre", "pos", "buffer", "rodovia", "cidade", "uf",
                      "velocidade", "seq"):
            for w in getattr(self, f"_widget_{chave}", ()):
                w.grid_remove() if lote else w.grid()

    # ── Montagem dos argumentos e execução ─────────────────────────────────
    def _montar_argv(self):
        """Monta a lista de argumentos exatamente como na CLI. Campos vazios
        são omitidos para que os defaults de `main()`/argparse se apliquem —
        garante paridade byte-a-byte com uma chamada equivalente da CLI."""
        argv = ["cercas_v2.py"]

        def add(flag, chave):
            valor = self.campos[chave].get().strip()
            if valor:
                argv.extend([flag, valor])

        if self.usar_lote.get():
            add("--batch", "batch")
        else:
            if self.campos["via"].get().strip():
                add("--via", "via")
            elif self.campos["polilinha"].get().strip():
                add("--polilinha", "polilinha")
            add("--modo", "modo")
            add("--inicio", "inicio")
            add("--fim", "fim")
            add("--comprimento", "comprimento")
            add("--pre", "pre")
            add("--pos", "pos")
            add("--buffer", "buffer")
            add("--rodovia", "rodovia")
            add("--cidade", "cidade")
            add("--uf", "uf")
            add("--velocidade", "velocidade")
            add("--seq", "seq")

        add("--formato", "formato")
        add("--saida", "saida")
        add("--relatorio", "relatorio")
        add("--retry-tentativas", "retry_tentativas")
        add("--retry-espera", "retry_espera")
        add("--retry-timeout", "retry_timeout")
        if self.campos["cache"].get():
            argv.append("--cache")
        add("--cache-ttl", "cache_ttl")
        add("--historico", "historico")

        add("--pg-dsn", "pg_dsn")
        if self.campos["substituir"].get():
            argv.append("--substituir")
        if self.campos["confirmar_substituicao"].get():
            argv.append("--confirmar-substituicao")
        add("--motivo-substituicao", "motivo_substituicao")

        add("--limiar-sobreposicao", "limiar_sobreposicao")
        for linha_override in self.overrides_texto.get("1.0", tk.END).splitlines():
            linha_override = linha_override.strip()
            if linha_override:
                argv.extend(["--override-sobreposicao", linha_override])

        return argv

    def _executar(self):
        argv = self._montar_argv()
        buffer_saida = io.StringIO()
        argv_original = sys.argv
        sys.argv = argv
        try:
            with contextlib.redirect_stdout(buffer_saida), contextlib.redirect_stderr(buffer_saida):
                cercas_v2.main()
            status = "\n✓ Concluído com sucesso."
        except SystemExit as e:
            status = f"\n✗ Encerrado com código {e.code}."
        except Exception as e:
            status = f"\n✗ ERRO: {e}"
        finally:
            sys.argv = argv_original

        self.saida_texto.delete("1.0", tk.END)
        self.saida_texto.insert(tk.END, f"Comando equivalente: {' '.join(argv[1:])}\n\n")
        self.saida_texto.insert(tk.END, buffer_saida.getvalue())
        self.saida_texto.insert(tk.END, status)


if __name__ == "__main__":
    CercasGUI().mainloop()
