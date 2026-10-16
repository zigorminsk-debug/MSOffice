from __future__ import annotations

import socket
import sqlite3
import threading
import webbrowser
from datetime import date
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from .core import KINDS, Ledger, format_money
from .help_content import HELP_TEXT
from .security import SecurityConfig
from .web import create_app


class BalanceGUI(tk.Tk):
    def __init__(self, db_path: str | Path | None = None):
        super().__init__()
        self.withdraw()
        self.security = SecurityConfig()
        self.authorized = self._authenticate()
        if not self.authorized:
            self.destroy()
            return
        selected_db = db_path or self.security.database_path
        self.ledger = Ledger(selected_db)
        self.title("Сводный баланс — Игорь и Юра")
        self.geometry("1280x800")
        self.minsize(980, 650)
        self.configure(bg="#f1f4f0")
        self.web_started = False
        self._configure_styles()
        self._build()
        self.refresh()
        self.deiconify()

    def _authenticate(self) -> bool:
        if not self.security.configured:
            messagebox.showinfo("Первый запуск", "Создайте пароль для запуска приложения. Минимум 6 символов.\n\nПароль нельзя восстановить автоматически — сохраните его в надёжном месте.", parent=self)
            while True:
                password = simpledialog.askstring("Создание пароля", "Новый пароль:", show="*", parent=self)
                if password is None:
                    return False
                confirmation = simpledialog.askstring("Создание пароля", "Повторите пароль:", show="*", parent=self)
                if password != confirmation:
                    messagebox.showerror("Пароль", "Пароли не совпадают", parent=self)
                    continue
                try:
                    self.security.set_password(password)
                    self.security.set_database(self.security.database_path)
                    break
                except ValueError as error:
                    messagebox.showerror("Пароль", str(error), parent=self)
            if messagebox.askyesno("Сетевая база", "Использовать существующий файл balance.db из общей сетевой папки?", parent=self):
                selected = filedialog.askopenfilename(parent=self, title="Выберите сетевую базу", filetypes=[("База SQLite", "*.db"), ("Все файлы", "*.*")])
                if selected:
                    self.security.set_database(selected)
            return True
        for _ in range(5):
            password = simpledialog.askstring("Вход", "Введите пароль для запуска:", show="*", parent=self)
            if password is None:
                return False
            if self.security.verify(password):
                return True
            messagebox.showerror("Вход", "Неверный пароль", parent=self)
        messagebox.showerror("Вход", "Слишком много неверных попыток", parent=self)
        return False

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
        ttk.Button(actions, text="База и доступ", command=self.configure_access).pack(side="left", padx=5)
        ttk.Button(actions, text="Инструкция", command=self.show_help).pack(side="left", padx=5)
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

    def configure_access(self) -> None:
        window = tk.Toplevel(self)
        window.title("База данных и сетевой доступ")
        window.geometry("700x390")
        window.resizable(False, False)
        window.configure(bg="#fff")
        path_var = tk.StringVar(value=str(self.ledger.db_path))
        network_var = tk.BooleanVar(value=self.security.network_access)
        password_var = tk.StringVar()
        confirm_var = tk.StringVar()
        tk.Label(window, text="База данных и доступ", bg="#16221d", fg="white", font=("Segoe UI", 16, "bold"), padx=22, pady=16).pack(fill="x")
        body = tk.Frame(window, bg="#fff", padx=24, pady=18); body.pack(fill="both", expand=True)
        tk.Label(body, text="Путь к balance.db (можно указать сетевой путь, например \\\\SERVER\\Share\\balance.db)", bg="#fff", fg="#45534b", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        path_entry = ttk.Entry(body, textvariable=path_var); path_entry.pack(fill="x", pady=(5, 7))
        buttons = tk.Frame(body, bg="#fff"); buttons.pack(fill="x")
        def open_existing():
            selected = filedialog.askopenfilename(parent=window, title="Открыть базу", filetypes=[("База SQLite", "*.db"), ("Все файлы", "*.*")])
            if selected: path_var.set(selected)
        def create_new():
            selected = filedialog.asksaveasfilename(parent=window, title="Создать базу", defaultextension=".db", filetypes=[("База SQLite", "*.db")], initialfile="balance.db")
            if selected: path_var.set(selected)
        ttk.Button(buttons, text="Открыть существующую…", command=open_existing).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Создать новую…", command=create_new).pack(side="left")
        ttk.Checkbutton(body, text="Разрешить доступ к HTML-интерфейсу из локальной сети", variable=network_var).pack(anchor="w", pady=(18, 12))
        tk.Label(body, text="Новый пароль (оставьте пустым, чтобы не менять)", bg="#fff", fg="#45534b", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        password_row = tk.Frame(body, bg="#fff"); password_row.pack(fill="x", pady=5)
        ttk.Entry(password_row, textvariable=password_var, show="*").pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Entry(password_row, textvariable=confirm_var, show="*").pack(side="left", fill="x", expand=True)
        tk.Label(body, text="Новый пароль                                      Повторите пароль", bg="#fff", fg="#7b8780", font=("Segoe UI", 8)).pack(anchor="w")
        def save_settings():
            if password_var.get() or confirm_var.get():
                if password_var.get() != confirm_var.get():
                    messagebox.showerror("Настройки", "Новые пароли не совпадают", parent=window); return
                try: self.security.set_password(password_var.get())
                except ValueError as error:
                    messagebox.showerror("Настройки", str(error), parent=window); return
            try:
                new_ledger = Ledger(path_var.get())
            except (OSError, sqlite3.Error) as error:
                messagebox.showerror("Настройки", f"Не удалось открыть базу:\n{error}", parent=window); return
            self.security.set_database(path_var.get())
            self.security.set_network_access(network_var.get())
            self.ledger = new_ledger
            self.refresh()
            window.destroy()
            messagebox.showinfo("Настройки", "Настройки сохранены. Если HTML-интерфейс уже был запущен, перезапустите приложение.", parent=self)
        ttk.Button(body, text="Сохранить настройки", command=save_settings).pack(anchor="e", pady=(16, 0))
        window.transient(self); window.grab_set(); window.focus_set()

    def show_help(self) -> None:
        window = tk.Toplevel(self)
        window.title("Инструкция — Сводный баланс")
        window.geometry("820x700")
        window.minsize(620, 450)
        window.configure(bg="#f1f4f0")
        header = tk.Frame(window, bg="#16221d")
        header.pack(fill="x")
        tk.Label(header, text="Инструкция и примеры заполнения", bg="#16221d", fg="white",
                 font=("Segoe UI", 16, "bold"), padx=22, pady=16).pack(anchor="w")
        frame = tk.Frame(window, bg="#fff")
        frame.pack(fill="both", expand=True, padx=18, pady=18)
        scrollbar = ttk.Scrollbar(frame)
        scrollbar.pack(side="right", fill="y")
        text = tk.Text(frame, wrap="word", yscrollcommand=scrollbar.set, bg="#fff", fg="#26342d",
                       relief="flat", padx=24, pady=20, font=("Segoe UI", 10), spacing1=2, spacing3=5)
        text.pack(fill="both", expand=True)
        scrollbar.config(command=text.yview)
        text.insert("1.0", HELP_TEXT)
        text.config(state="disabled")
        ttk.Button(window, text="Закрыть", command=window.destroy).pack(pady=(0, 16))
        window.transient(self)
        window.focus_set()

    def open_web(self) -> None:
        if not self.web_started:
            app = create_app(self.ledger.db_path, self.security)
            host = "0.0.0.0" if self.security.network_access else "127.0.0.1"
            threading.Thread(target=lambda: app.run(host=host, port=8080, debug=False, use_reloader=False), daemon=True).start()
            self.web_started = True
            if self.security.network_access:
                try:
                    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    probe.connect(("8.8.8.8", 80))
                    local_ip = probe.getsockname()[0]
                    probe.close()
                except OSError:
                    local_ip = socket.gethostbyname(socket.gethostname())
                messagebox.showinfo("Сетевой доступ", f"На других компьютерах локальной сети откройте:\n\nhttp://{local_ip}:8080\n\nДля входа используется пароль приложения.", parent=self)
        webbrowser.open("http://127.0.0.1:8080")


def run_gui(db_path: str | Path | None = None) -> None:
    app = BalanceGUI(db_path)
    if app.authorized:
        app.mainloop()
