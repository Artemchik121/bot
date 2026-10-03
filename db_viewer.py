import csv
import os
import sqlite3
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk


# ============================================================
# НАСТРОЙКИ
# ============================================================

# bot.db должен лежать рядом с этим файлом.
DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.db")

AUTO_REFRESH_MS = 5000


# ============================================================
# ТЕМА / СТИЛИ
# ============================================================

BG = "#f4f6f8"
CARD = "#ffffff"
TEXT = "#1f2937"
MUTED = "#6b7280"
BORDER = "#d9dee5"


# ============================================================
# БАЗА ДАННЫХ
# ============================================================

def get_connection():
    """
    Открывает SQLite в режиме только для чтения.
    Просмотрщик не сможет случайно изменить bot.db.
    """
    if not os.path.exists(DB_FILE):
        raise FileNotFoundError(
            f"Файл базы не найден:\n{DB_FILE}"
        )

    uri = f"file:{DB_FILE.replace(chr(92), '/')}"
    return sqlite3.connect(
        uri + "?mode=ro",
        uri=True,
        timeout=5
    )


def table_exists(cursor, table_name):
    cursor.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type='table' AND name=?
        """,
        (table_name,)
    )
    return cursor.fetchone() is not None


# ============================================================
# СЛУЖЕБНЫЕ ФУНКЦИИ
# ============================================================

def clear_tree(tree):
    for item in tree.get_children():
        tree.delete(item)


def set_status(text):
    status_var.set(text)


def format_number(value):
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


# ============================================================
# СТАТИСТИКА
# ============================================================

def load_statistics():
    try:
        with get_connection() as connection:
            cursor = connection.cursor()

            if not table_exists(cursor, "users"):
                raise RuntimeError("Таблица users не найдена.")

            cursor.execute("SELECT COUNT(*) FROM users")
            users_count = cursor.fetchone()[0]

            cursor.execute(
                "SELECT COALESCE(SUM(size), 0) FROM users"
            )
            total_size = cursor.fetchone()[0]

            cursor.execute(
                "SELECT COALESCE(AVG(size), 0) FROM users"
            )
            average_size = cursor.fetchone()[0]

            if table_exists(cursor, "growth_history"):
                cursor.execute(
                    "SELECT COUNT(*) FROM growth_history"
                )
                growth_count = cursor.fetchone()[0]
            else:
                growth_count = 0

            cursor.execute(
                """
                SELECT COALESCE(MAX(size), 0)
                FROM users
                """
            )
            max_size = cursor.fetchone()[0]

        users_label.config(
            text=f"Пользователи\n{users_count}"
        )
        total_label.config(
            text=f"Общий размер\n{total_size} см"
        )
        average_label.config(
            text=f"Средний размер\n{average_size:.1f} см"
        )
        max_label.config(
            text=f"Максимальный размер\n{max_size} см"
        )
        growth_label.config(
            text=f"Всего ростов\n{growth_count}"
        )

    except Exception as error:
        set_status(f"Ошибка статистики: {error}")


# ============================================================
# ПОЛЬЗОВАТЕЛИ
# ============================================================

def load_users():
    clear_tree(users_tree)

    search = search_var.get().strip()

    try:
        with get_connection() as connection:
            cursor = connection.cursor()

            if not table_exists(cursor, "users"):
                raise RuntimeError("Таблица users не найдена.")

            if table_exists(cursor, "growth_history"):
                growth_join = """
                    LEFT JOIN growth_history g
                        ON u.vk_id = g.vk_id
                """
                growth_count_sql = "COUNT(g.id)"
            else:
                growth_join = ""
                growth_count_sql = "0"

            query = f"""
                SELECT
                    u.vk_id,
                    u.name,
                    u.size,
                    u.registered_at,
                    u.last_growth,
                    {growth_count_sql} AS growth_count
                FROM users u
                {growth_join}
            """

            params = []

            if search:
                query += """
                    WHERE
                        CAST(u.vk_id AS TEXT) LIKE ?
                        OR LOWER(u.name) LIKE LOWER(?)
                """
                params = [
                    f"%{search}%",
                    f"%{search}%"
                ]

            query += """
                GROUP BY
                    u.vk_id,
                    u.name,
                    u.size,
                    u.registered_at,
                    u.last_growth
                ORDER BY
                    u.size DESC,
                    u.vk_id ASC
            """

            cursor.execute(query, params)
            rows = cursor.fetchall()

        for row in rows:
            users_tree.insert(
                "",
                tk.END,
                values=row
            )

        set_status(
            f"Показано пользователей: {len(rows)} | "
            f"Обновлено: {datetime.now().strftime('%H:%M:%S')}"
        )

    except Exception as error:
        set_status(f"Ошибка загрузки пользователей: {error}")
        messagebox.showerror(
            "Ошибка базы данных",
            str(error)
        )


# ============================================================
# ИСТОРИЯ
# ============================================================

def load_history():
    clear_tree(history_tree)

    try:
        with get_connection() as connection:
            cursor = connection.cursor()

            if not table_exists(cursor, "growth_history"):
                set_status("Таблица growth_history не найдена.")
                return

            cursor.execute(
                """
                SELECT
                    g.id,
                    g.vk_id,
                    COALESCE(u.name, '—'),
                    g.growth_date,
                    g.old_size,
                    g.new_size
                FROM growth_history g
                LEFT JOIN users u
                    ON g.vk_id = u.vk_id
                ORDER BY g.id DESC
                """
            )

            rows = cursor.fetchall()

        for row in rows:
            history_tree.insert(
                "",
                tk.END,
                values=row
            )

    except Exception as error:
        set_status(f"Ошибка истории: {error}")


# ============================================================
# ПОДРОБНАЯ ИНФОРМАЦИЯ О ПОЛЬЗОВАТЕЛЕ
# ============================================================

def get_selected_user_id():
    selected = users_tree.selection()

    if not selected:
        return None

    values = users_tree.item(
        selected[0],
        "values"
    )

    return values[0] if values else None


def show_user_details(event=None):
    vk_id = get_selected_user_id()

    if not vk_id:
        return

    try:
        with get_connection() as connection:
            cursor = connection.cursor()

            cursor.execute(
                """
                SELECT
                    vk_id,
                    name,
                    size,
                    registered_at,
                    last_growth
                FROM users
                WHERE vk_id = ?
                """,
                (vk_id,)
            )

            user = cursor.fetchone()

            if not user:
                return

            if table_exists(cursor, "growth_history"):
                cursor.execute(
                    """
                    SELECT
                        growth_date,
                        old_size,
                        new_size
                    FROM growth_history
                    WHERE vk_id = ?
                    ORDER BY id DESC
                    """,
                    (vk_id,)
                )
                history = cursor.fetchall()
            else:
                history = []

        window = tk.Toplevel(root)
        window.title(f"Пользователь {vk_id}")
        window.geometry("760x520")
        window.minsize(600, 400)

        header = tk.Frame(
            window,
            bg=CARD,
            highlightbackground=BORDER,
            highlightthickness=1
        )
        header.pack(
            fill=tk.X,
            padx=12,
            pady=12
        )

        tk.Label(
            header,
            text=f"VK ID: {user[0]}",
            font=("Segoe UI", 16, "bold"),
            bg=CARD,
            fg=TEXT
        ).pack(
            anchor="w",
            padx=14,
            pady=(12, 2)
        )

        tk.Label(
            header,
            text=f"Имя: {user[1]}",
            font=("Segoe UI", 11),
            bg=CARD,
            fg=TEXT
        ).pack(
            anchor="w",
            padx=14,
            pady=2
        )

        tk.Label(
            header,
            text=f"Размер: {user[2]} см",
            font=("Segoe UI", 11, "bold"),
            bg=CARD,
            fg=TEXT
        ).pack(
            anchor="w",
            padx=14,
            pady=2
        )

        tk.Label(
            header,
            text=f"Регистрация: {user[3]}    Последний рост: {user[4]}",
            font=("Segoe UI", 10),
            bg=CARD,
            fg=MUTED
        ).pack(
            anchor="w",
            padx=14,
            pady=(2, 12)
        )

        tk.Label(
            window,
            text=f"История роста: {len(history)} записей",
            font=("Segoe UI", 12, "bold")
        ).pack(
            anchor="w",
            padx=14,
            pady=(4, 6)
        )

        columns = (
            "date",
            "old",
            "new"
        )

        tree = ttk.Treeview(
            window,
            columns=columns,
            show="headings"
        )

        tree.heading("date", text="Дата")
        tree.heading("old", text="Было")
        tree.heading("new", text="Стало")

        tree.column("date", width=220)
        tree.column("old", width=120)
        tree.column("new", width=120)

        scrollbar = ttk.Scrollbar(
            window,
            orient="vertical",
            command=tree.yview
        )
        tree.configure(
            yscrollcommand=scrollbar.set
        )

        scrollbar.pack(
            side=tk.RIGHT,
            fill=tk.Y,
            padx=(0, 12),
            pady=(0, 12)
        )

        tree.pack(
            side=tk.LEFT,
            fill=tk.BOTH,
            expand=True,
            padx=(12, 0),
            pady=(0, 12)
        )

        for row in history:
            tree.insert(
                "",
                tk.END,
                values=row
            )

    except Exception as error:
        messagebox.showerror(
            "Ошибка",
            str(error)
        )


# ============================================================
# СОРТИРОВКА КОЛОНОК
# ============================================================

def sort_tree(tree, column, reverse):
    rows = [
        (tree.set(item, column), item)
        for item in tree.get_children("")
    ]

    def sort_key(pair):
        value = pair[0]

        try:
            return float(value)
        except (ValueError, TypeError):
            return str(value).lower()

    rows.sort(
        key=sort_key,
        reverse=reverse
    )

    for index, (_, item) in enumerate(rows):
        tree.move(
            item,
            "",
            index
        )

    tree.heading(
        column,
        command=lambda: sort_tree(
            tree,
            column,
            not reverse
        )
    )


def setup_sorting(tree, columns):
    for column in columns:
        tree.heading(
            column,
            command=lambda c=column: sort_tree(
                tree,
                c,
                False
            )
        )


# ============================================================
# ЭКСПОРТ В CSV
# ============================================================

def export_users_csv():
    try:
        rows = [
            users_tree.item(item, "values")
            for item in users_tree.get_children()
        ]

        if not rows:
            messagebox.showinfo(
                "Экспорт",
                "Нет данных для экспорта."
            )
            return

        filename = filedialog.asksaveasfilename(
            title="Сохранить пользователей",
            defaultextension=".csv",
            filetypes=[
                ("CSV файл", "*.csv")
            ],
            initialfile="users.csv"
        )

        if not filename:
            return

        with open(
            filename,
            "w",
            newline="",
            encoding="utf-8-sig"
        ) as file:
            writer = csv.writer(file, delimiter=";")

            writer.writerow([
                "VK ID",
                "Имя",
                "Размер",
                "Регистрация",
                "Последний рост",
                "Количество ростов"
            ])

            writer.writerows(rows)

        set_status(f"Экспортировано: {filename}")

        messagebox.showinfo(
            "Готово",
            "Пользователи экспортированы в CSV."
        )

    except Exception as error:
        messagebox.showerror(
            "Ошибка экспорта",
            str(error)
        )


def export_history_csv():
    try:
        rows = [
            history_tree.item(item, "values")
            for item in history_tree.get_children()
        ]

        if not rows:
            messagebox.showinfo(
                "Экспорт",
                "Нет данных для экспорта."
            )
            return

        filename = filedialog.asksaveasfilename(
            title="Сохранить историю",
            defaultextension=".csv",
            filetypes=[
                ("CSV файл", "*.csv")
            ],
            initialfile="growth_history.csv"
        )

        if not filename:
            return

        with open(
            filename,
            "w",
            newline="",
            encoding="utf-8-sig"
        ) as file:
            writer = csv.writer(file, delimiter=";")

            writer.writerow([
                "ID",
                "VK ID",
                "Имя",
                "Дата",
                "Было",
                "Стало"
            ])

            writer.writerows(rows)

        set_status(f"Экспортировано: {filename}")

        messagebox.showinfo(
            "Готово",
            "История экспортирована в CSV."
        )

    except Exception as error:
        messagebox.showerror(
            "Ошибка экспорта",
            str(error)
        )


# ============================================================
# ОБНОВЛЕНИЕ
# ============================================================

def refresh_all():
    load_statistics()
    load_users()
    load_history()


def auto_refresh():
    refresh_all()
    root.after(
        AUTO_REFRESH_MS,
        auto_refresh
    )


# ============================================================
# ЗАКРЫТИЕ
# ============================================================

def close_app():
    root.destroy()


# ============================================================
# GUI
# ============================================================

root = tk.Tk()
root.title("VK Bot — Панель базы данных")
root.geometry("1250x760")
root.minsize(950, 600)
root.configure(bg=BG)


style = ttk.Style()

try:
    style.theme_use("clam")
except tk.TclError:
    pass

style.configure(
    "Treeview",
    background=CARD,
    foreground=TEXT,
    rowheight=30,
    fieldbackground=CARD,
    borderwidth=0,
    font=("Segoe UI", 10)
)

style.configure(
    "Treeview.Heading",
    font=("Segoe UI", 10, "bold"),
    padding=7
)

style.configure(
    "TNotebook",
    background=BG
)

style.configure(
    "TNotebook.Tab",
    padding=(16, 8),
    font=("Segoe UI", 10, "bold")
)


# ============================================================
# HEADER
# ============================================================

header = tk.Frame(
    root,
    bg=BG
)

header.pack(
    fill=tk.X,
    padx=18,
    pady=(16, 8)
)

tk.Label(
    header,
    text="VK BOT",
    font=("Segoe UI", 22, "bold"),
    bg=BG,
    fg=TEXT
).pack(
    side=tk.LEFT
)

tk.Label(
    header,
    text="  Панель базы данных",
    font=("Segoe UI", 14),
    bg=BG,
    fg=MUTED
).pack(
    side=tk.LEFT,
    pady=(6, 0)
)

button_frame = tk.Frame(
    header,
    bg=BG
)

button_frame.pack(
    side=tk.RIGHT
)

tk.Button(
    button_frame,
    text="↻ Обновить",
    command=refresh_all,
    padx=12,
    pady=7
).pack(
    side=tk.LEFT,
    padx=4
)

tk.Button(
    button_frame,
    text="⤓ CSV",
    command=export_users_csv,
    padx=12,
    pady=7
).pack(
    side=tk.LEFT,
    padx=4
)

tk.Button(
    button_frame,
    text="✕ Закрыть",
    command=close_app,
    padx=12,
    pady=7
).pack(
    side=tk.LEFT,
    padx=4
)


# ============================================================
# КАРТОЧКИ СТАТИСТИКИ
# ============================================================

stats_frame = tk.Frame(
    root,
    bg=BG
)

stats_frame.pack(
    fill=tk.X,
    padx=18,
    pady=8
)


def create_stat_card(parent, initial_text):
    frame = tk.Frame(
        parent,
        bg=CARD,
        highlightbackground=BORDER,
        highlightthickness=1
    )

    label = tk.Label(
        frame,
        text=initial_text,
        font=("Segoe UI", 11, "bold"),
        bg=CARD,
        fg=TEXT,
        justify=tk.LEFT
    )

    label.pack(
        fill=tk.X,
        padx=16,
        pady=12
    )

    return frame, label


users_card, users_label = create_stat_card(
    stats_frame,
    "Пользователи\n0"
)

total_card, total_label = create_stat_card(
    stats_frame,
    "Общий размер\n0 см"
)

average_card, average_label = create_stat_card(
    stats_frame,
    "Средний размер\n0 см"
)

max_card, max_label = create_stat_card(
    stats_frame,
    "Максимальный размер\n0 см"
)

growth_card, growth_label = create_stat_card(
    stats_frame,
    "Всего ростов\n0"
)


for card in (
    users_card,
    total_card,
    average_card,
    max_card,
    growth_card
):
    card.pack(
        side=tk.LEFT,
        fill=tk.X,
        expand=True,
        padx=4
    )


# ============================================================
# ПОИСК
# ============================================================

search_frame = tk.Frame(
    root,
    bg=BG
)

search_frame.pack(
    fill=tk.X,
    padx=18,
    pady=(8, 4)
)

tk.Label(
    search_frame,
    text="Поиск по VK ID или имени:",
    font=("Segoe UI", 10),
    bg=BG,
    fg=TEXT
).pack(
    side=tk.LEFT
)

search_var = tk.StringVar()

search_entry = tk.Entry(
    search_frame,
    textvariable=search_var,
    font=("Segoe UI", 11),
    width=38
)

search_entry.pack(
    side=tk.LEFT,
    padx=10
)

tk.Button(
    search_frame,
    text="Найти",
    command=load_users,
    padx=12
).pack(
    side=tk.LEFT
)

tk.Button(
    search_frame,
    text="Сбросить",
    command=lambda: (
        search_var.set(""),
        load_users()
    ),
    padx=12
).pack(
    side=tk.LEFT,
    padx=5
)

search_entry.bind(
    "<Return>",
    lambda event: load_users()
)


# ============================================================
# NOTEBOOK
# ============================================================

notebook = ttk.Notebook(root)

notebook.pack(
    fill=tk.BOTH,
    expand=True,
    padx=18,
    pady=10
)


# ============================================================
# ПОЛЬЗОВАТЕЛИ
# ============================================================

users_frame = tk.Frame(
    notebook,
    bg=CARD
)

notebook.add(
    users_frame,
    text="  👥 Пользователи  "
)

user_columns = (
    "vk_id",
    "name",
    "size",
    "registered",
    "last_growth",
    "growth_count"
)

users_tree = ttk.Treeview(
    users_frame,
    columns=user_columns,
    show="headings",
    selectmode="browse"
)

headings = {
    "vk_id": "VK ID",
    "name": "Имя",
    "size": "Размер",
    "registered": "Регистрация",
    "last_growth": "Последний рост",
    "growth_count": "Ростов"
}

widths = {
    "vk_id": 130,
    "name": 220,
    "size": 110,
    "registered": 160,
    "last_growth": 160,
    "growth_count": 100
}

for column in user_columns:
    users_tree.heading(
        column,
        text=headings[column]
    )
    users_tree.column(
        column,
        width=widths[column],
        anchor=tk.W
    )

users_tree.column(
    "size",
    anchor=tk.CENTER
)

users_tree.column(
    "growth_count",
    anchor=tk.CENTER
)

users_scroll = ttk.Scrollbar(
    users_frame,
    orient="vertical",
    command=users_tree.yview
)

users_tree.configure(
    yscrollcommand=users_scroll.set
)

users_scroll.pack(
    side=tk.RIGHT,
    fill=tk.Y,
    padx=(0, 8),
    pady=8
)

users_tree.pack(
    side=tk.LEFT,
    fill=tk.BOTH,
    expand=True,
    padx=(8, 0),
    pady=8
)

users_tree.bind(
    "<Double-1>",
    show_user_details
)

setup_sorting(
    users_tree,
    user_columns
)


# ============================================================
# ИСТОРИЯ
# ============================================================

history_frame = tk.Frame(
    notebook,
    bg=CARD
)

notebook.add(
    history_frame,
    text="  📈 История роста  "
)

history_columns = (
    "id",
    "vk_id",
    "name",
    "date",
    "old",
    "new"
)

history_tree = ttk.Treeview(
    history_frame,
    columns=history_columns,
    show="headings"
)

history_headings = {
    "id": "ID",
    "vk_id": "VK ID",
    "name": "Имя",
    "date": "Дата",
    "old": "Было",
    "new": "Стало"
}

history_widths = {
    "id": 70,
    "vk_id": 130,
    "name": 220,
    "date": 180,
    "old": 110,
    "new": 110
}

for column in history_columns:
    history_tree.heading(
        column,
        text=history_headings[column]
    )
    history_tree.column(
        column,
        width=history_widths[column]
    )

history_tree.column(
    "id",
    anchor=tk.CENTER
)

history_tree.column(
    "old",
    anchor=tk.CENTER
)

history_tree.column(
    "new",
    anchor=tk.CENTER
)

history_scroll = ttk.Scrollbar(
    history_frame,
    orient="vertical",
    command=history_tree.yview
)

history_tree.configure(
    yscrollcommand=history_scroll.set
)

history_scroll.pack(
    side=tk.RIGHT,
    fill=tk.Y,
    padx=(0, 8),
    pady=8
)

history_tree.pack(
    side=tk.LEFT,
    fill=tk.BOTH,
    expand=True,
    padx=(8, 0),
    pady=8
)

setup_sorting(
    history_tree,
    history_columns
)

history_menu = tk.Menu(
    root,
    tearoff=False
)

history_menu.add_command(
    label="⤓ Экспорт истории в CSV",
    command=export_history_csv
)


def show_history_menu(event):
    try:
        history_menu.tk_popup(
            event.x_root,
            event.y_root
        )
    finally:
        history_menu.grab_release()


history_tree.bind(
    "<Button-3>",
    show_history_menu
)


# ============================================================
# STATUS BAR
# ============================================================

status_var = tk.StringVar(
    value=f"База: {DB_FILE}"
)

status_bar = tk.Label(
    root,
    textvariable=status_var,
    anchor="w",
    bg="#e9edf2",
    fg=MUTED,
    padx=12,
    pady=6,
    font=("Segoe UI", 9)
)

status_bar.pack(
    fill=tk.X,
    side=tk.BOTTOM
)


# ============================================================
# ЗАПУСК
# ============================================================

refresh_all()

root.after(
    AUTO_REFRESH_MS,
    auto_refresh
)

root.protocol(
    "WM_DELETE_WINDOW",
    close_app
)

root.mainloop()
