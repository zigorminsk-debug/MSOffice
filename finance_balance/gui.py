from __future__ import annotations

import threading
import webbrowser
from datetime import date
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .core import KINDS, Ledger, format_money
from .web import create_app


class BalanceGUI(tk.Tk):
    def __init__(self, db_path: str | Path | None = None):
        super().__init__()
        self.ledger = Ledger(db_path)
        self.title("Сводный баланс — Игорь и Юра")
        self.geometry("1280x800")
        self.minsize(980, 650)
        self.configure(bg="#f1f4f0")
        self.web_started = False
        self._configure_styles()
        self._build()
        self.refresh()

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", rowheight=36, font=("Segoe UI", 9), background="#fff", fieldbackground="#fff")
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"), background="#e8eee9")
        style.map("Treeview", background=[("selected", "#dcecdf")], foreground=[("selected", "#16221d")])
        style.configure("TButton", font=("Segoe UI", 9, "bold"), padding=(12, 8))

    def _build(self) -> None:
        header = tk.Frame(self, bg="#16221d", height=112)
        header.pack(fill="x"); header.pack_propagate(False)
        left = tk.Frame(header, bg="#16221d"); left.pack(side="left", padx=28, pady=17)
        tk.Label(left, text="СВОДНЫЙ БАЛАНС · BYN", bg="#16221d", fg="#94b5a3", font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self.debt_title = tk.Label(left, bg="#16221d", fg="white", font=("Segoe UI", 12)); self.debt_title.pack(anchor="w")
        self.debt_value = tk.Label(left, bg="#16221d", fg="white", font=("Segoe UI", 24, "bold")); self.debt_value.pack(anchor="w")
        actions = tk.Frame(header, bg="#16221d"); actions.pack(side="right", padx=28)
        ttk.Button(actions, text="HTML-интерфейс", command=self.open_web).pack(side="left", padx=5)
        ttk.Button(actions, text="Экспорт CSV", command=self.export).pack(side="left", padx=5)

        form = tk.LabelFrame(self, text="  Новая операция  ", bg="#fff", fg="#16221d", font=("Segoe UI", 10, "bold"), padx=14, pady=10)
        form.pack(fill="x", padx=20, pady=(16, 8))
        names = ("date", "owner", "kind", "sign", "amount", "currency", "rate", "from", "to", "description")
        self.vars = {name: tk.StringVar() for name in names}
        self.vars.update({"date": tk.StringVar(value=date.today().isoformat()), "owner": tk.StringVar(value="Юра"),
                          "sign": tk.StringVar(value="+"), "currency": tk.StringVar(value="BYN")})
        self.kind_labels = {f"{value[1]} · {value[0]}": code for code, value in KINDS.items()}
        self.vars["kind"].set(next(iter(self.kind_labels)))

        def field(label: str, name: str, row: int, column: int, span: int = 1, values=None, width=14):
            cell = tk.Frame(form, bg="#fff"); cell.grid(row=row, column=column, columnspan=span, sticky="ew", padx=4, pady=3)
            tk.Label(cell, text=label, bg="#fff", fg="#66736c", font=("Segoe UI", 8)).pack(anchor="w")
            if values is not None:
                widget = ttk.Combobox(cell, textvariable=self.vars[name], values=values, state="readonly", width=width)
            else:
                widget = ttk.Entry(cell, textvariable=self.vars[name], width=width)
            widget.pack(fill="x", pady=(2, 0))
            return widget

        field("Дата", "date", 0, 0)
        field("На чей баланс", "owner", 0, 1, values=["Игорь", "Юра"])
        kind_box = field("Операция", "kind", 0, 2, span=2, values=list(self.kind_labels), width=30)
        kind_box.bind("<<ComboboxSelected>>", self._preset_sign)
        field("Действие", "sign", 0, 4, values=["+", "−"])
        field("Сумма", "amount", 0, 5)
        field("Валюта", "currency", 0, 6, values=["BYN", "USD"], width=8)
        field("Курс USD/BYN (пусто = НБРБ)", "rate", 0, 7, width=20)
        field("От кого", "from", 1, 0, span=2)
        field("Кому", "to", 1, 2, span=2)
        field("Пояснение", "description", 1, 4, span=3, width=35)
        ttk.Button(form, text="Добавить", command=self.add).grid(row=1, column=7, padx=4, pady=(18, 3), sticky="ew")
        for column in range(8): form.grid_columnconfigure(column, weight=1)

        content = tk.Frame(self, bg="#f1f4f0"); content.pack(fill="both", expand=True, padx=20, pady=(0, 18))
        self.stats = tk.Label(content, bg="#f1f4f0", fg="#506058", font=("Segoe UI", 9)); self.stats.pack(fill="x", pady=5)
        columns = ("date", "operation", "owner", "flow", "original", "byn", "effect", "description")
        self.table = ttk.Treeview(content, columns=columns, show="headings", selectmode="browse")
        headings = {"date":"Дата", "operation":"Операция", "owner":"Баланс", "flow":"От кого → кому", "original":"Исходная сумма", "byn":"Сумма BYN", "effect":"Влияние", "description":"Пояснение"}
        widths = {"date":85, "operation":175, "owner":65, "flow":155, "original":115, "byn":105, "effect":160, "description":220}
        for key in columns:
            self.table.heading(key, text=headings[key]); self.table.column(key, width=widths[key], anchor="e" if key in ("original", "byn") else "w")
        self.table.pack(fill="both", expand=True)
        self.table.tag_configure("plus", foreground="#ad4239"); self.table.tag_configure("minus", foreground="#1e6849")
        bottom = tk.Frame(content, bg="#f1f4f0"); bottom.pack(fill="x", pady=(8, 0))
        ttk.Button(bottom, text="Удалить выбранную запись", command=self.delete_selected).pack(side="right")
        tk.Label(bottom, text=f"База: {self.ledger.db_path}", bg="#f1f4f0", fg="#7b8780", font=("Segoe UI", 8)).pack(side="left")

    def _preset_sign(self, _event=None) -> None:
        code = self.kind_labels[self.vars["kind"].get()]
        self.vars["sign"].set("+" if KINDS[code][2] > 0 else "−")

    def add(self) -> None:
        try:
            self.ledger.add(operation_date=self.vars["date"].get(), kind=self.kind_labels[self.vars["kind"].get()],
                amount=self.vars["amount"].get(), sign=1 if self.vars["sign"].get() == "+" else -1,
                owner=self.vars["owner"].get(), currency=self.vars["currency"].get(), fx_rate=self.vars["rate"].get(),
                from_party=self.vars["from"].get(), to_party=self.vars["to"].get(), description=self.vars["description"].get())
        except ValueError as error:
            messagebox.showerror("Не удалось добавить", str(error), parent=self); return
        for key in ("amount", "rate", "from", "to", "description"): self.vars[key].set("")
        self.refresh()

    def refresh(self) -> None:
        for row in self.table.get_children(): self.table.delete(row)
        for item in self.ledger.list():
            if item.mode == "debt":
                effect = ("Юра → Игорю" if item.effect_cents > 0 else "Игорь → Юре") + f" {format_money(abs(item.effect_cents))}"
                tag = "plus" if item.effect_cents > 0 else "minus"
            else:
                effect = f"Депозит {'+' if item.deposit_effect_cents > 0 else '−'} {format_money(abs(item.deposit_effect_cents))}"
                tag = "minus" if item.deposit_effect_cents > 0 else "plus"
            original = format_money(item.amount_cents, item.currency)
            if item.currency == "USD": original += f" @ {item.fx_rate}"
            self.table.insert("", "end", iid=str(item.id), values=(item.operation_date, item.kind_label, item.owner,
                f"{item.from_party or '—'} → {item.to_party or '—'}", original, format_money(item.byn_cents), effect, item.description), tags=(tag,))
        summary = self.ledger.summary()
        if summary["debt"] > 0: title, value = "Юра должен Игорю", summary["debt"]
        elif summary["debt"] < 0: title, value = "Игорь должен Юре", -summary["debt"]
        else: title, value = "Расчёты закрыты", 0
        self.debt_title.config(text=title); self.debt_value.config(text=format_money(value))
        self.stats.config(text=f"Приход: {format_money(summary['incoming'])}   Расходы: {format_money(summary['expenses'])}   Депозит займа Игоря: {format_money(summary['igor_deposit'])}   Депозит займа Юры: {format_money(summary['yura_deposit'])}")

    def delete_selected(self) -> None:
        selected = self.table.selection()
        if not selected: messagebox.showinfo("Удаление", "Выберите запись в таблице", parent=self); return
        if messagebox.askyesno("Удаление", "Удалить выбранную операцию?", parent=self):
            self.ledger.delete(int(selected[0])); self.refresh()

    def export(self) -> None:
        target = filedialog.asksaveasfilename(parent=self, defaultextension=".csv", filetypes=[("CSV", "*.csv")], initialfile=f"balance-{date.today().isoformat()}.csv")
        if target: self.ledger.export_csv(target); messagebox.showinfo("Экспорт", "Файл успешно сохранён", parent=self)

    def open_web(self) -> None:
        if not self.web_started:
            app = create_app(self.ledger.db_path)
            threading.Thread(target=lambda: app.run(host="127.0.0.1", port=8080, debug=False, use_reloader=False), daemon=True).start()
            self.web_started = True
        webbrowser.open("http://127.0.0.1:8080")


def run_gui(db_path: str | Path | None = None) -> None:
    BalanceGUI(db_path).mainloop()
