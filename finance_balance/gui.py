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
        self.geometry("1200x760")
        self.minsize(940, 620)
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
        style.configure("Treeview", rowheight=34, font=("Segoe UI", 9), background="#ffffff", fieldbackground="#ffffff")
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"), background="#e8eee9")
        style.map("Treeview", background=[("selected", "#dcecdf")], foreground=[("selected", "#16221d")])
        style.configure("TButton", font=("Segoe UI", 9, "bold"), padding=(12, 8))

    def _build(self) -> None:
        header = tk.Frame(self, bg="#16221d", height=110)
        header.pack(fill="x")
        header.pack_propagate(False)
        left = tk.Frame(header, bg="#16221d")
        left.pack(side="left", padx=28, pady=18)
        tk.Label(left, text="СВОДНЫЙ БАЛАНС", bg="#16221d", fg="#94b5a3", font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self.debt_title = tk.Label(left, text="", bg="#16221d", fg="white", font=("Segoe UI", 12))
        self.debt_title.pack(anchor="w")
        self.debt_value = tk.Label(left, text="", bg="#16221d", fg="white", font=("Segoe UI", 24, "bold"))
        self.debt_value.pack(anchor="w")
        actions = tk.Frame(header, bg="#16221d")
        actions.pack(side="right", padx=28)
        ttk.Button(actions, text="HTML-интерфейс", command=self.open_web).pack(side="left", padx=5)
        ttk.Button(actions, text="Экспорт CSV", command=self.export).pack(side="left", padx=5)

        form = tk.LabelFrame(self, text="  Новая операция  ", bg="#ffffff", fg="#16221d", font=("Segoe UI", 10, "bold"), padx=14, pady=10)
        form.pack(fill="x", padx=20, pady=(18, 10))
        self.vars = {name: tk.StringVar() for name in ("date", "kind", "sign", "amount", "from", "to", "description")}
        self.vars["date"].set(date.today().isoformat())
        self.kind_labels = {f"{value[1]} · {value[0]}": code for code, value in KINDS.items()}
        self.vars["kind"].set(next(iter(self.kind_labels)))
        fields = [
            ("Дата", "date", 12), ("Операция", "kind", 28), ("Знак", "sign", 10),
            ("Сумма, ₽", "amount", 14), ("От кого", "from", 18), ("Кому", "to", 18), ("Пояснение", "description", 28),
        ]
        for column, (label, name, width) in enumerate(fields):
            cell = tk.Frame(form, bg="#ffffff")
            cell.grid(row=0, column=column, sticky="ew", padx=4)
            tk.Label(cell, text=label, bg="#ffffff", fg="#66736c", font=("Segoe UI", 8)).pack(anchor="w")
            if name == "kind":
                widget = ttk.Combobox(cell, textvariable=self.vars[name], values=list(self.kind_labels), state="readonly", width=width)
                widget.bind("<<ComboboxSelected>>", self._preset_sign)
            elif name == "sign":
                self.vars[name].set("+")
                widget = ttk.Combobox(cell, textvariable=self.vars[name], values=["+", "−"], state="readonly", width=width)
            else:
                widget = ttk.Entry(cell, textvariable=self.vars[name], width=width)
            widget.pack(fill="x", pady=(3, 0))
            form.grid_columnconfigure(column, weight=2 if name in ("kind", "description") else 1)
        ttk.Button(form, text="Добавить", command=self.add).grid(row=0, column=len(fields), padx=(10, 4), sticky="sew")

        content = tk.Frame(self, bg="#f1f4f0")
        content.pack(fill="both", expand=True, padx=20, pady=(0, 18))
        self.stats = tk.Label(content, text="", bg="#f1f4f0", fg="#506058", font=("Segoe UI", 9))
        self.stats.pack(fill="x", pady=5)
        columns = ("date", "operation", "flow", "amount", "effect", "description")
        self.table = ttk.Treeview(content, columns=columns, show="headings", selectmode="browse")
        headings = {"date":"Дата", "operation":"Операция", "flow":"От кого → кому", "amount":"Сумма", "effect":"Изменение долга", "description":"Пояснение"}
        widths = {"date":90, "operation":190, "flow":200, "amount":110, "effect":135, "description":300}
        for key in columns:
            self.table.heading(key, text=headings[key])
            self.table.column(key, width=widths[key], anchor="e" if key in ("amount", "effect") else "w")
        self.table.pack(fill="both", expand=True)
        self.table.tag_configure("plus", foreground="#ad4239")
        self.table.tag_configure("minus", foreground="#1e6849")
        bottom = tk.Frame(content, bg="#f1f4f0")
        bottom.pack(fill="x", pady=(8, 0))
        ttk.Button(bottom, text="Удалить выбранную запись", command=self.delete_selected).pack(side="right")

    def _preset_sign(self, _event=None) -> None:
        code = self.kind_labels[self.vars["kind"].get()]
        self.vars["sign"].set("+" if KINDS[code][2] > 0 else "−")

    def add(self) -> None:
        try:
            self.ledger.add(
                operation_date=self.vars["date"].get(), kind=self.kind_labels[self.vars["kind"].get()],
                amount=self.vars["amount"].get(), sign=1 if self.vars["sign"].get() == "+" else -1,
                from_party=self.vars["from"].get(), to_party=self.vars["to"].get(),
                description=self.vars["description"].get(),
            )
        except ValueError as error:
            messagebox.showerror("Не удалось добавить", str(error), parent=self)
            return
        for key in ("amount", "from", "to", "description"):
            self.vars[key].set("")
        self.refresh()

    def refresh(self) -> None:
        for row in self.table.get_children():
            self.table.delete(row)
        for item in self.ledger.list():
            self.table.insert("", "end", iid=str(item.id), values=(item.operation_date, item.kind_label,
                f"{item.from_party or '—'} → {item.to_party or '—'}", format_money(item.amount_cents),
                f"{'+' if item.sign > 0 else '−'} {format_money(item.amount_cents)}", item.description),
                tags=("plus" if item.sign > 0 else "minus",))
        summary = self.ledger.summary()
        if summary["debt"] > 0:
            title, value = "Юра должен Игорю", summary["debt"]
        elif summary["debt"] < 0:
            title, value = "Игорь должен Юре", -summary["debt"]
        else:
            title, value = "Расчёты закрыты", 0
        self.debt_title.config(text=title)
        self.debt_value.config(text=format_money(value))
        self.stats.config(text=f"Приход: {format_money(summary['incoming'])}     Расходы: {format_money(summary['expenses'])}     Погашено / зачтено: {format_money(summary['repayments'])}")

    def delete_selected(self) -> None:
        selected = self.table.selection()
        if not selected:
            messagebox.showinfo("Удаление", "Выберите запись в таблице", parent=self)
            return
        if messagebox.askyesno("Удаление", "Удалить выбранную операцию?", parent=self):
            self.ledger.delete(int(selected[0]))
            self.refresh()

    def export(self) -> None:
        target = filedialog.asksaveasfilename(parent=self, defaultextension=".csv", filetypes=[("CSV", "*.csv")], initialfile=f"balance-{date.today().isoformat()}.csv")
        if target:
            self.ledger.export_csv(target)
            messagebox.showinfo("Экспорт", "Файл успешно сохранён", parent=self)

    def open_web(self) -> None:
        if not self.web_started:
            app = create_app(self.ledger.db_path)
            threading.Thread(target=lambda: app.run(host="127.0.0.1", port=8080, debug=False, use_reloader=False), daemon=True).start()
            self.web_started = True
        webbrowser.open("http://127.0.0.1:8080")


def run_gui(db_path: str | Path | None = None) -> None:
    BalanceGUI(db_path).mainloop()
