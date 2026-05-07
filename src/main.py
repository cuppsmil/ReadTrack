"""ReadTrack — консольный трекер чтения (процедурно-функциональный стиль)."""
import csv
import json
import logging
import os
import shutil
import sqlite3
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Tuple

from dotenv import load_dotenv

# ================= КОНФИГУРАЦИЯ И ЛОГИРОВАНИЕ =================
load_dotenv()
DB_PATH = Path(os.getenv("DB_PATH", "readtrack.db"))
BACKUP_DIR = Path("backups")
BACKUP_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    filename="app.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

# ================= УТИЛИТЫ И ФУНКЦИОНАЛЬНЫЕ КОНСТРУКЦИИ ========
def create_author_filter(author_name: str) -> Callable[[Dict], bool]:
    """Фабрика замыканий для фильтрации книг по автору."""
    return lambda book: author_name.lower() in book.get("author", "").lower()


def format_progress_bar(current: int, total: int) -> str:
    """Генерация ASCII-прогресс-бара."""
    if total == 0:
        return "[          ] 0%"
    percent = min(int(current / total * 10), 10)
    bar = "=" * percent + ">" + " " * (9 - percent)
    pct = int(current / total * 100)
    return f"[{bar}] {pct}%"


def safe_int(value: str, default: int = 0) -> int:
    """Безопасное преобразование строки в int."""
    try:
        return int(value.strip())
    except (ValueError, AttributeError):
        return default


def safe_date(value: str) -> str:
    """Валидация даты в формате YYYY-MM-DD."""
    try:
        datetime.strptime(value.strip(), "%Y-%m-%d")
        return value.strip()
    except (ValueError, AttributeError):
        return datetime.now().strftime("%Y-%m-%d")


# ================= РАБОТА С БАЗОЙ ДАННЫХ =====================
def init_db() -> None:
    """Инициализация таблицы books."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                author TEXT NOT NULL,
                year INTEGER,
                genre TEXT,
                total_pages INTEGER NOT NULL,
                start_date TEXT,
                current_page INTEGER DEFAULT 0,
                finish_date TEXT,
                rating INTEGER CHECK(rating BETWEEN 1 AND 10),
                status TEXT DEFAULT 'planned'
            )
        """)
        conn.commit()
    logging.info("База данных инициализирована.")


def _fetch_all() -> List[Dict]:
    """Получение всех книг в виде списка словарей."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM books").fetchall()
        return [dict(row) for row in rows]


def add_book(title: str, author: str, year: int, genre: str,
             total_pages: int, start_date: str = None,
             status: str = "planned") -> int:
    """Добавление новой книги."""
    if not title or not author:
        raise ValueError("Название и автор обязательны.")
    if total_pages <= 0:
        raise ValueError("Количество страниц должно быть > 0.")

    start_date = start_date or datetime.now().strftime("%Y-%m-%d")
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            """INSERT INTO books (title, author, year, genre, total_pages,
               start_date, current_page, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (title, author, year, genre, total_pages, start_date, 0, status)
        )
        conn.commit()
        logging.info(f"Добавлена книга: {title}")
        return cur.lastrowid


def update_book(book_id: int, **kwargs) -> None:
    """Обновление полей книги."""
    allowed = {
        "title", "author", "year", "genre", "total_pages",
        "current_page", "finish_date", "rating", "status"
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not fields:
        return

    set_clause = ", ".join(f"{k}=?" for k in fields)
    params = list(fields.values()) + [book_id]
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(f"UPDATE books SET {set_clause} WHERE id=?", params)
        conn.commit()
    logging.info(f"Обновлена книга #{book_id}: {fields}")


def delete_book(book_id: int) -> bool:
    """Удаление книги по ID."""
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute("DELETE FROM books WHERE id=?", (book_id,))
        conn.commit()
        if cur.rowcount > 0:
            logging.info(f"Удалена книга #{book_id}")
            return True
    return False


# ================= АНАЛИТИКА И ОТЧЁТЫ ========================
def get_weekly_report() -> Dict:
    """Расчёт еженедельной статистики."""
    books = _fetch_all()
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

    started_this_week = list(
        filter(lambda b: b["start_date"] and b["start_date"] >= week_ago, books)
    )
    completed = list(
        filter(lambda b: b["status"] == "completed", books)
    )

    pages_read = sum(b["current_page"] for b in started_this_week)
    avg_pace = 0
    if started_this_week:
        days = max(1, (datetime.now() - datetime.strptime(
            started_this_week[0]["start_date"], "%Y-%m-%d"
        )).days)
        avg_pace = round(sum(b["current_page"] for b in started_this_week) / days, 2)

    return {
        "pages": pages_read,
        "completed_count": len(completed),
        "avg_pace": avg_pace
    }


def get_recommendation() -> str:
    """Рекомендация жанра на основе завершённых книг."""
    books = _fetch_all()
    completed = filter(lambda b: b["status"] == "completed", books)
    genres = {}
    for b in completed:
        g = b.get("genre", "").strip().lower()
        if g:
            genres[g] = genres.get(g, 0) + 1

    if not genres:
        return "Нет завершённых книг для рекомендации."

    top_genre = max(genres, key=genres.get)
    return (f"Вы успешно завершили {genres[top_genre]} книг в жанре "
            f"«{top_genre}». Рекомендуем продолжить в этом направлении!")


def print_progress(books: List[Dict]) -> None:
    """Вывод прогресса по текущим книгам."""
    active = filter(lambda b: b["status"] == "reading", books)
    for b in sorted(active, key=lambda x: x.get("current_page", 0)):
        pct = format_progress_bar(b["current_page"], b["total_pages"])
        print(f"📖 {b['title']} {pct}")


# ================= ЭКСПОРТ / ИМПОРТ / БЭКАП ===================
def auto_backup() -> None:
    """Создание резервной копии БД с меткой времени."""
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        dst = BACKUP_DIR / f"backup_{ts}.sqlite"
        shutil.copy2(DB_PATH, dst)
        logging.info(f"Создан бэкап: {dst.name}")
    except Exception as exc:
        logging.error(f"Ошибка бэкапа: {exc}")


def export_data(mode: str = "zip") -> Path:
    """Экспорт данных в CSV, JSON или ZIP."""
    books = _fetch_all()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    if mode == "csv":
        path = Path(f"export_{ts}.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["title", "author", "status", "progress", "rating"])
            for b in books:
                prog = f"{b['current_page']}/{b['total_pages']}"
                writer.writerow([b["title"], b["author"], b["status"],
                                 prog, b.get("rating")])
        return path

    if mode == "zip":
        zip_path = Path(f"export_{ts}.zip")
        csv_path = Path("temp_export.csv")
        json_path = Path("temp_export.json")

        # CSV
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["title", "author", "status", "progress", "rating"])
            for b in books:
                writer.writerow([b["title"], b["author"], b["status"],
                                 f"{b['current_page']}/{b['total_pages']}",
                                 b.get("rating")])

        # JSON
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(books, f, ensure_ascii=False, indent=2)

        # ZIP архив
        auto_backup()
        latest_backup = max(BACKUP_DIR.glob("*.sqlite"), key=os.path.getmtime)

        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.write(csv_path, "books.csv")
            zf.write(json_path, "books.json")
            zf.write(latest_backup, latest_backup.name)

        csv_path.unlink()
        json_path.unlink()
        logging.info(f"Экспорт в ZIP завершён: {zip_path.name}")
        return zip_path
    raise ValueError("Неподдерживаемый формат экспорта.")


def import_data(zip_path: str) -> None:
    """Импорт данных из ZIP-архива."""
    zpath = Path(zip_path)
    if not zpath.exists() or not zipfile.is_zipfile(zpath):
        raise FileNotFoundError("Архив не найден или повреждён.")

    with zipfile.ZipFile(zpath, "r") as zf:
        if "books.json" not in zf.namelist():
            raise ValueError("В архиве отсутствует books.json")
        with zf.open("books.json") as f:
            books = json.load(f)

        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("DELETE FROM books")
            for b in books:
                conn.execute(
                    """INSERT INTO books (id, title, author, year, genre,
                       total_pages, start_date, current_page, finish_date,
                       rating, status) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (b.get("id"), b["title"], b["author"], b.get("year"),
                     b.get("genre"), b["total_pages"], b.get("start_date"),
                     b.get("current_page", 0), b.get("finish_date"),
                     b.get("rating"), b.get("status"))
                )
            conn.commit()
    logging.info(f"Данные импортированы из {zpath.name}")


# ================= КОНСОЛЬНЫЙ ИНТЕРФЕЙС =======================
def print_menu() -> None:
    menu = (
        "\n=== ReadTrack ===\n"
        "1. Добавить книгу\n"
        "2. Список книг (фильтр)\n"
        "3. Редактировать книгу\n"
        "4. Удалить книгу\n"
        "5. Обновить прогресс\n"
        "6. Отчёты и рекомендации\n"
        "7. Экспорт / Импорт\n"
        "8. Выход\n"
        "Выберите действие: "
    )
    print(menu, end="")


def handle_add() -> None:
    try:
        title = input("Название: ").strip()
        author = input("Автор: ").strip()
        year = safe_int(input("Год издания: "))
        genre = input("Жанр: ").strip()
        pages = safe_int(input("Всего страниц: "))
        if pages <= 0:
            raise ValueError("Страниц должно быть > 0")
        status = input("Статус (planned/reading/completed): ").strip() or "planned"
        add_book(title, author, year, genre, pages, status=status)
        print("✅ Книга добавлена.")
    except Exception as exc:
        print(f"❌ Ошибка: {exc}")


def handle_list() -> None:
    books = _fetch_all()
    if not books:
        print("📚 Каталог пуст.")
        return

    mode = input("Фильтр (status/genre/year/author/all): ").strip().lower()
    filtered = books

    if mode == "status":
        val = input("Статус: ").strip()
        filtered = list(filter(lambda b: b["status"] == val, books))
    elif mode == "genre":
        val = input("Жанр: ").strip()
        filtered = list(filter(lambda b: b.get("genre", "").lower() == val.lower(), books))
    elif mode == "year":
        val = safe_int(input("Год: "))
        filtered = list(filter(lambda b: b.get("year") == val, books))
    elif mode == "author":
        val = input("Автор: ").strip()
        filtered = list(filter(create_author_filter(val), books))

    # Использование map для форматирования вывода
    lines = map(lambda b: f"#{b['id']} | {b['title']} | {b['author']} "
                          f"| {b['status']} | {b['current_page']}/{b['total_pages']}",
                filtered)
    print("\n" + "\n".join(lines) + "\n")
    print_progress(filtered)


def handle_edit() -> None:
    try:
        bid = safe_int(input("ID книги: "))
        field = input("Поле для изменения: ").strip()
        value = input("Новое значение: ").strip()
        if field in ("year", "total_pages", "current_page", "rating"):
            value = safe_int(value)
        update_book(bid, **{field: value})
        print("✅ Обновлено.")
    except Exception as exc:
        print(f"❌ Ошибка: {exc}")


def handle_delete() -> None:
    try:
        bid = safe_int(input("ID для удаления: "))
        if delete_book(bid):
            print("✅ Книга удалена.")
        else:
            print("❌ Книга не найдена.")
    except Exception as exc:
        print(f"❌ Ошибка: {exc}")


def handle_progress() -> None:
    try:
        bid = safe_int(input("ID книги: "))
        page = safe_int(input("Текущая страница: "))
        book = next((b for b in _fetch_all() if b["id"] == bid), None)
        if not book:
            print("❌ Книга не найдена.")
            return
        if page > book["total_pages"]:
            print("⚠️ Страница больше общего количества.")
            return
        update_book(bid, current_page=page)
        if page == book["total_pages"]:
            update_book(bid, status="completed",
                        finish_date=datetime.now().strftime("%Y-%m-%d"))
            print("🎉 Книга завершена!")
        else:
            print(format_progress_bar(page, book["total_pages"]))
    except Exception as exc:
        print(f"❌ Ошибка: {exc}")


def handle_reports() -> None:
    report = get_weekly_report()
    print(f"\n📊 Еженедельный отчёт:")
    print(f"  Страниц прочитано: {report['pages']}")
    print(f"  Завершено книг:    {report['completed_count']}")
    print(f"  Средний темп:      {report['avg_pace']} стр/день")
    print(f"\n💡 {get_recommendation()}\n")


def handle_data() -> None:
    action = input("Экспорт (csv/zip) / Импорт (zip): ").strip().lower()
    try:
        if action in ("csv", "zip"):
            path = export_data(action)
            print(f"✅ Файл сохранён: {path}")
        elif action == "import" or action == "import_zip":
            zpath = input("Путь к ZIP: ").strip()
            import_data(zpath)
            print("✅ Импорт завершён.")
        else:
            print("❌ Неизвестная операция.")
    except Exception as exc:
        print(f"❌ Ошибка: {exc}")


def main() -> None:
    """Точка входа приложения."""
    init_db()
    auto_backup()
    print("📖 Добро пожаловать в ReadTrack!")

    while True:
        try:
            print_menu()
            choice = input().strip()
            if choice == "1":
                handle_add()
            elif choice == "2":
                handle_list()
            elif choice == "3":
                handle_edit()
            elif choice == "4":
                handle_delete()
            elif choice == "5":
                handle_progress()
            elif choice == "6":
                handle_reports()
            elif choice == "7":
                handle_data()
            elif choice == "8":
                print("👋 До встречи за чтением!")
                logging.info("Приложение завершено пользователем.")
                break
            else:
                print("⚠️ Неверный ввод.")
        except KeyboardInterrupt:
            print("\n👋 Работа прервана.")
            logging.warning("Принудительное завершение (Ctrl+C)")
            break
        except Exception as exc:
            print(f"❌ Критическая ошибка: {exc}")
            logging.error(f"Unhandled exception: {exc}")


if __name__ == "__main__":
    main()