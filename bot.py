import os
import sqlite3
import time
from datetime import date, datetime, timedelta

import vk_api
from vk_api.longpoll import VkLongPoll, VkEventType

# ============================================================
# НАСТРОЙКИ
# ============================================================

TOKEN = os.getenv("BOT_TOKEN")

ADMIN_IDS = {
    739351270,
    604189481
}

START_SIZE = 10
START_COINS = 100

# Сколько монет давать за сообщение
MESSAGE_REWARD = 3

# КД на награду в секундах
MESSAGE_REWARD_COOLDOWN = 2

# Задержка между сообщениями при рассылке (сек)
BROADCAST_DELAY = 0.35


# ============================================================
# БАЗА ДАННЫХ
# ============================================================

db = sqlite3.connect(
    "bot.db",
    check_same_thread=False
)

cursor = db.cursor()


# Таблица пользователей
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    vk_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    size INTEGER NOT NULL DEFAULT 10,
    coins INTEGER NOT NULL DEFAULT 0,
    registered_at TEXT NOT NULL,
    last_growth TEXT NOT NULL,
    last_bonus TEXT NOT NULL DEFAULT '',
    coin_multiplier REAL NOT NULL DEFAULT 1.0,
    boost_until TEXT NOT NULL DEFAULT '',
    no_cooldown_until TEXT NOT NULL DEFAULT ''
)
""")

# Авто-миграция старых БД
def _safe_alter(sql):
    try:
        cursor.execute(sql)
        db.commit()
    except sqlite3.OperationalError:
        pass

_safe_alter("ALTER TABLE users ADD COLUMN coins INTEGER NOT NULL DEFAULT 0")
_safe_alter("ALTER TABLE users ADD COLUMN last_bonus TEXT NOT NULL DEFAULT ''")
_safe_alter("ALTER TABLE users ADD COLUMN coin_multiplier REAL NOT NULL DEFAULT 1.0")
_safe_alter("ALTER TABLE users ADD COLUMN boost_until TEXT NOT NULL DEFAULT ''")
_safe_alter("ALTER TABLE users ADD COLUMN no_cooldown_until TEXT NOT NULL DEFAULT ''")


# История роста
cursor.execute("""
CREATE TABLE IF NOT EXISTS growth_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vk_id INTEGER NOT NULL,
    growth_date TEXT NOT NULL,
    old_size INTEGER NOT NULL,
    new_size INTEGER NOT NULL
)
""")


# Таблица покупок
cursor.execute("""
CREATE TABLE IF NOT EXISTS purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vk_id INTEGER NOT NULL,
    item_id TEXT NOT NULL,
    item_name TEXT NOT NULL,
    price INTEGER NOT NULL,
    purchase_date TEXT NOT NULL
)
""")


db.commit()


# ============================================================
# VK
# ============================================================

vk_session = vk_api.VkApi(
    token=TOKEN
)

vk = vk_session.get_api()

longpoll = VkLongPoll(vk_session)


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def send_message(user_id, text):
    """
    Отправляет сообщение пользователю.
    """

    vk.messages.send(
        user_id=user_id,
        message=text,
        random_id=0
    )


def is_admin(user_id):
    """
    Проверяет, является ли пользователь администратором.
    """

    return user_id in ADMIN_IDS


def get_user(user_id):
    """
    Получает пользователя из БД.
    """

    cursor.execute("""
        SELECT
            vk_id,
            name,
            size,
            coins,
            registered_at,
            last_growth,
            last_bonus,
            coin_multiplier,
            boost_until,
            no_cooldown_until
        FROM users
        WHERE vk_id = ?
    """, (user_id,))

    return cursor.fetchone()


# ============================================================
# РЕГИСТРАЦИЯ
# ============================================================

def register_user(user_id):
    """
    Регистрирует нового пользователя.
    """

    # Проверяем, существует ли пользователь
    if get_user(user_id):
        return False

    # Получаем информацию из VK
    user_info = vk.users.get(
        user_ids=user_id
    )[0]

    name = user_info["first_name"]

    today = date.today().isoformat()

    cursor.execute("""
        INSERT INTO users (
            vk_id,
            name,
            size,
            coins,
            registered_at,
            last_growth,
            last_bonus,
            coin_multiplier,
            boost_until,
            no_cooldown_until
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id,
        name,
        START_SIZE,
        START_COINS,
        today,
        today,
        "",
        1.0,
        "",
        ""
    ))

    db.commit()

    return True


# ============================================================
# ЕЖЕДНЕВНЫЙ РОСТ
# ============================================================

def process_growth(user_id):
    """
    Проверяет, сколько дней прошло с последнего роста,
    и добавляет +1 см за каждый прошедший день.
    """

    user = get_user(user_id)

    if not user:
        return

    (
        vk_id,
        name,
        size,
        coins,
        registered_at,
        last_growth,
        last_bonus,
        coin_multiplier,
        boost_until,
        no_cooldown_until
    ) = user

    today = date.today()

    last_growth_date = datetime.strptime(
        last_growth,
        "%Y-%m-%d"
    ).date()

    days_passed = (
        today - last_growth_date
    ).days

    # Сегодня уже был рост
    if days_passed <= 0:
        return

    old_size = size

    new_size = (
        size + days_passed
    )

    # Обновляем пользователя
    cursor.execute("""
        UPDATE users
        SET
            size = ?,
            last_growth = ?
        WHERE vk_id = ?
    """, (
        new_size,
        today.isoformat(),
        user_id
    ))

    # Записываем каждый день в историю
    for i in range(1, days_passed + 1):

        growth_date = (
            last_growth_date.fromordinal(
                last_growth_date.toordinal() + i
            )
        )

        old_day_size = (
            old_size + i - 1
        )

        new_day_size = (
            old_size + i
        )

        cursor.execute("""
            INSERT INTO growth_history (
                vk_id,
                growth_date,
                old_size,
                new_size
            )
            VALUES (?, ?, ?, ?)
        """, (
            user_id,
            growth_date.isoformat(),
            old_day_size,
            new_day_size
        ))

    db.commit()


# ============================================================
# НАГРАДА ЗА СООБЩЕНИЯ
# ============================================================

def give_message_reward(user_id):
    """
    Даёт монеты за сообщение с учётом КД и множителя.
    Возвращает сумму начисления (0 — если не начислено).
    """

    user = get_user(user_id)

    if not user:
        return 0

    (
        vk_id,
        name,
        size,
        coins,
        registered_at,
        last_growth,
        last_bonus,
        coin_multiplier,
        boost_until,
        no_cooldown_until
    ) = user

    now = datetime.now()

    # Активен ли "Ускорение" (снятие КД)
    no_cooldown_active = False
    if no_cooldown_until:
        try:
            nc_dt = datetime.fromisoformat(no_cooldown_until)
            if now < nc_dt:
                no_cooldown_active = True
        except ValueError:
            pass

    # Проверка КД (только если нет Ускорения)
    if not no_cooldown_active and last_bonus:
        try:
            last_bonus_dt = datetime.fromisoformat(last_bonus)
        except ValueError:
            last_bonus_dt = None

        if last_bonus_dt is not None:
            if (now - last_bonus_dt).total_seconds() < MESSAGE_REWARD_COOLDOWN:
                return 0

    # Проверка буста дохода
    multiplier = 1.0
    if boost_until:
        try:
            boost_dt = datetime.fromisoformat(boost_until)
        except ValueError:
            boost_dt = None

        if boost_dt is not None and now < boost_dt:
            multiplier = coin_multiplier

    reward = int(MESSAGE_REWARD * multiplier)

    new_coins = coins + reward

    cursor.execute("""
        UPDATE users
        SET coins = ?, last_bonus = ?
        WHERE vk_id = ?
    """, (
        new_coins,
        now.isoformat(),
        user_id
    ))

    db.commit()

    return reward


# ============================================================
# СТАТИСТИКА ПОЛЬЗОВАТЕЛЯ
# ============================================================

def get_user_stats(user_id):
    """
    Возвращает статистику пользователя.
    """

    process_growth(user_id)

    user = get_user(user_id)

    if not user:
        return None

    (
        vk_id,
        name,
        size,
        coins,
        registered_at,
        last_growth,
        last_bonus,
        coin_multiplier,
        boost_until,
        no_cooldown_until
    ) = user

    registered_date = datetime.strptime(
        registered_at,
        "%Y-%m-%d"
    ).date()

    today = date.today()

    days_in_game = (
        today - registered_date
    ).days

    cursor.execute("""
        SELECT COUNT(*)
        FROM growth_history
        WHERE vk_id = ?
    """, (user_id,))

    growth_count = cursor.fetchone()[0]

    now = datetime.now()

    # Активен ли буст дохода
    boost_active = False
    boost_left_min = 0
    if boost_until:
        try:
            boost_dt = datetime.fromisoformat(boost_until)
            if now < boost_dt:
                boost_active = True
                boost_left_min = int(
                    (boost_dt - now).total_seconds() // 60
                ) + 1
        except ValueError:
            pass

    # Активно ли снятие КД
    no_cd_active = False
    no_cd_left_min = 0
    if no_cooldown_until:
        try:
            nc_dt = datetime.fromisoformat(no_cooldown_until)
            if now < nc_dt:
                no_cd_active = True
                no_cd_left_min = int(
                    (nc_dt - now).total_seconds() // 60
                ) + 1
        except ValueError:
            pass

    return {
        "vk_id": vk_id,
        "name": name,
        "size": size,
        "coins": coins,
        "registered_at": registered_at,
        "last_growth": last_growth,
        "days_in_game": days_in_game,
        "growth_count": growth_count,
        "coin_multiplier": coin_multiplier,
        "boost_active": boost_active,
        "boost_left_min": boost_left_min,
        "no_cd_active": no_cd_active,
        "no_cd_left_min": no_cd_left_min
    }


# ============================================================
# ТОП
# ============================================================

def get_top():
    """
    Получает топ-10 пользователей по размеру.
    """

    cursor.execute("""
        SELECT
            name,
            size,
            coins
        FROM users
        ORDER BY size DESC
        LIMIT 10
    """)

    return cursor.fetchall()


# ============================================================
# АДМИН: ОБЩАЯ СТАТИСТИКА
# ============================================================

def get_admin_stats():

    cursor.execute("""
        SELECT COUNT(*)
        FROM users
    """)

    users_count = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COALESCE(SUM(size), 0)
        FROM users
    """)

    total_size = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COALESCE(SUM(coins), 0)
        FROM users
    """)

    total_coins = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*)
        FROM growth_history
    """)

    growth_count = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COALESCE(AVG(size), 0)
        FROM users
    """)

    average_size = cursor.fetchone()[0]

    return (
        users_count,
        total_size,
        total_coins,
        growth_count,
        average_size
    )


# ============================================================
# АДМИН: СПИСОК ПОЛЬЗОВАТЕЛЕЙ
# ============================================================

def get_all_users():

    cursor.execute("""
        SELECT
            vk_id,
            name,
            size,
            coins,
            registered_at,
            last_growth
        FROM users
        ORDER BY size DESC
        LIMIT 50
    """)

    return cursor.fetchall()


# ============================================================
# АДМИН: РАССЫЛКА
# ============================================================

def get_all_user_ids():
    """
    Возвращает список всех VK ID пользователей.
    """
    cursor.execute("SELECT vk_id FROM users")
    return [row[0] for row in cursor.fetchall()]


def get_online_user_ids(hours=24):
    """
    Возвращает список VK ID пользователей,
    которые писали боту за последние N часов.
    """
    cursor.execute("""
        SELECT vk_id, last_bonus
        FROM users
        WHERE last_bonus != ''
    """)

    now = datetime.now()
    threshold = timedelta(hours=hours)

    result = []

    for vk_id, last_bonus in cursor.fetchall():
        try:
            dt = datetime.fromisoformat(last_bonus)
        except ValueError:
            continue

        if now - dt <= threshold:
            result.append(vk_id)

    return result


def do_broadcast(target_ids, text, admin_id):
    """
    Выполняет рассылку. Возвращает (успех, ошибок).
    """

    sent = 0
    failed = 0

    for uid in target_ids:

        try:
            send_message(uid, text)
            sent += 1

        except Exception:
            failed += 1

        time.sleep(BROADCAST_DELAY)

    return sent, failed


# ============================================================
# МАГАЗИН
# ============================================================
#
# Здесь настраиваются товары/услуги.
#
# item_id      — уникальный код товара (латиницей, без пробелов)
# name         — название, которое увидит игрок
# description  — описание товара/услуги
# price        — цена в МОНЕТАХ
# effect       — что делает товар (обрабатывается вручную ниже)
#
# ============================================================

SHOP_ITEMS = {

    # --------------------------------------------------------
    # ТОВАР 1
    # --------------------------------------------------------
    "vitamin": {
        "name": "💊 Виагра",
        "description": (
            "Ускоряет рост. Даёт +3 см мгновенно."
        ),
        "price": 50,
        "effect": "add_size_3"
    },

    # --------------------------------------------------------
    # ТОВАР 2
    # --------------------------------------------------------
    "fertilizer": {
        "name": "💊 Виагра в лошидиных дозах",
        "description": (
            "Усиленное удобрение. Даёт +10 см мгновенно."
        ),
        "price": 150,
        "effect": "add_size_10"
    },

    # --------------------------------------------------------
    # ТОВАР 3
    # --------------------------------------------------------
    "lubricant": {
        "name": "🔄 Смазка",
        "description": (
            "Повышает доход монет в 1.5 раза. "
            "Работает 1 час."
        ),
        "price": 300,
        "effect": "coin_boost_1_5_60min"
    },

    # --------------------------------------------------------
    # ТОВАР 4
    # --------------------------------------------------------
    "speed": {
        "name": "⚡ Ускорение",
        "description": (
            "Убирает КД на награду за сообщения. "
            "Работает 1 час."
        ),
        "price": 250,
        "effect": "no_cooldown_60min"
    },
}


def get_shop_item(item_id):
    """
    Возвращает товар по его ID.
    """
    return SHOP_ITEMS.get(item_id)


def get_user_purchases(user_id):
    """
    Возвращает список покупок пользователя.
    """
    cursor.execute("""
        SELECT
            item_id,
            item_name,
            price,
            purchase_date
        FROM purchases
        WHERE vk_id = ?
        ORDER BY id DESC
    """, (user_id,))

    return cursor.fetchall()


def buy_item(user_id, item_id):
    """
    Покупка товара за монеты.
    Возвращает (успех, сообщение).
    """

    item = get_shop_item(item_id)

    if not item:
        return False, "❌ Такого товара нет в магазине."

    user = get_user(user_id)

    if not user:
        return False, "❌ Сначала зарегистрируйся: /start"

    # Обновляем рост перед покупкой
    process_growth(user_id)

    user = get_user(user_id)

    (
        vk_id,
        name,
        size,
        coins,
        registered_at,
        last_growth,
        last_bonus,
        coin_multiplier,
        boost_until,
        no_cooldown_until
    ) = user

    price = item["price"]

    if coins < price:
        return (
            False,
            "❌ Недостаточно монет.\n\n"
            f"Нужно: {price} 🪙\n"
            f"У тебя: {coins} 🪙"
        )

    # Списываем монеты
    new_coins = coins - price

    # Применяем эффект
    effect = item["effect"]

    new_size = size
    new_multiplier = coin_multiplier
    new_boost_until = boost_until
    new_no_cd_until = no_cooldown_until

    now = datetime.now()

    if effect == "add_size_3":
        new_size += 3

    elif effect == "add_size_10":
        new_size += 10

    elif effect == "coin_boost_1_5_60min":
        new_multiplier = 1.5
        new_boost_until = (
            now + timedelta(hours=1)
        ).isoformat()

    elif effect == "no_cooldown_60min":
        new_no_cd_until = (
            now + timedelta(hours=1)
        ).isoformat()

    # Не позволяем уйти в минус
    if new_size < 0:
        new_size = 0

    cursor.execute("""
        UPDATE users
        SET
            size = ?,
            coins = ?,
            coin_multiplier = ?,
            boost_until = ?,
            no_cooldown_until = ?
        WHERE vk_id = ?
    """, (
        new_size,
        new_coins,
        new_multiplier,
        new_boost_until,
        new_no_cd_until,
        user_id
    ))

    # Записываем покупку
    cursor.execute("""
        INSERT INTO purchases (
            vk_id,
            item_id,
            item_name,
            price,
            purchase_date
        )
        VALUES (?, ?, ?, ?, ?)
    """, (
        user_id,
        item_id,
        item["name"],
        price,
        date.today().isoformat()
    ))

    db.commit()

    return (
        True,
        "✅ Покупка совершена!\n\n"
        f"Товар: {item['name']}\n"
        f"Цена: {price} 🪙\n"
        f"Осталось монет: {new_coins} 🪙\n"
        f"Размер: {new_size} см"
    )


# ============================================================
# ОСНОВНОЙ ЦИКЛ БОТА
# ============================================================

print("Бот запущен!")


for event in longpoll.listen():

    # Игнорируем всё кроме новых сообщений
    if event.type != VkEventType.MESSAGE_NEW:
        continue

    # Сообщение должно быть адресовано боту
    if not event.to_me:
        continue

    user_id = event.user_id

    raw_message = event.text.strip()
    message = raw_message.lower()


    # ========================================================
    # НАГРАДА ЗА СООБЩЕНИЕ
    # ========================================================

    # Если игрок написал "дилда" — награда + ответ
    if message == "дилда":

        if not get_user(user_id):

            send_message(
                user_id,
                "❌ Ты ещё не зарегистрирован.\n\n"
                "Напиши /start"
            )

            continue

        reward = give_message_reward(user_id)

        if reward > 0:

            send_message(
                user_id,
                f"🌱 Ты получил +{reward} 🪙!"
            )

        else:

            send_message(
                user_id,
                "⏳ Подожди немного перед следующим получением монет."
            )

        continue


    # Если игрок написал ЛЮБОЕ другое сообщение без "/" —
    # просто начисляем монеты (без ответа)
    if (
        get_user(user_id)
        and not message.startswith("/")
    ):
        give_message_reward(user_id)
        # И ничего не отправляем — чтобы не спамить


    # ========================================================
    # МАГАЗИН
    # ========================================================

    if message in ["/магазин", "/shop"]:

        text = "🛒 МАГАЗИН\n\n"

        for item_id, item in SHOP_ITEMS.items():

            text += (
                f"{item['name']}\n"
                f"{item['description']}\n"
                f"Цена: {item['price']} 🪙\n"
                f"Купить: /купить {item_id}\n\n"
            )

        send_message(user_id, text)
        continue


    if message.startswith("/купить "):

        parts = message.split()

        if len(parts) != 2:

            send_message(
                user_id,
                "❌ Использование:\n\n"
                "/купить ID\n\n"
                "Например:\n"
                "/купить vitamin"
            )

            continue

        item_id = parts[1]

        success, answer = buy_item(user_id, item_id)

        send_message(user_id, answer)
        continue


    if message in ["/покупки", "/myitems"]:

        purchases = get_user_purchases(user_id)

        if not purchases:

            send_message(
                user_id,
                "📦 У тебя пока нет покупок."
            )

            continue

        text = "📦 ТВОИ ПОКУПКИ\n\n"

        for (
            item_id,
            item_name,
            price,
            purchase_date
        ) in purchases:

            text += (
                f"{item_name}\n"
                f"Цена: {price} 🪙\n"
                f"Дата: {purchase_date}\n\n"
            )

        send_message(user_id, text)
        continue


    # ========================================================
    # БАЛАНС
    # ========================================================

    if message in ["/баланс", "/balance", "/монеты"]:

        stats = get_user_stats(user_id)

        if not stats:

            send_message(
                user_id,
                "❌ Ты ещё не зарегистрирован.\n\n"
                "Напиши /start"
            )

            continue

        boost_line = ""
        if stats["boost_active"]:
            boost_line += (
                f"⚡ Буст дохода: x{stats['coin_multiplier']} "
                f"({stats['boost_left_min']} мин.)\n"
            )

        if stats["no_cd_active"]:
            boost_line += (
                f"🚀 Без КД: "
                f"({stats['no_cd_left_min']} мин.)\n"
            )

        send_message(
            user_id,
            "💰 ТВОЙ БАЛАНС\n\n"
            f"🪙 Монет: {stats['coins']}\n"
            f"📏 Размер: {stats['size']} см\n"
            f"{boost_line}"
        )

        continue


    # ========================================================
    # АВТОМАТИЧЕСКИЙ РОСТ
    # ========================================================

    if get_user(user_id):
        process_growth(user_id)


    # ========================================================
    # /START
    # ========================================================

    if message == "/start":

        if get_user(user_id):

            send_message(
                user_id,
                "🌳 Ты уже зарегистрирован!\n\n"
                "Напиши /стата, чтобы посмотреть "
                "свою статистику."
            )

        else:

            register_user(user_id)

            send_message(
                user_id,
                "🌱 Регистрация завершена!\n\n"
                f"Твой дилдак начинается с "
                f"{START_SIZE} см.\n\n"
                f"Стартовый баланс: "
                f"{START_COINS} 🪙\n\n"
                "Каждый день он растёт "
                "на 1 см.\n\n"
                f"Напиши 'дилда', чтобы "
                f"получить +{MESSAGE_REWARD} 🪙 "
                f"(КД {MESSAGE_REWARD_COOLDOWN} сек).\n\n"
                "📚 Команды:\n"
                "/стата — статистика\n"
                "/баланс — монеты\n"
                "/топ — рейтинг\n"
                "/магазин — магазин\n"
                "/помощь — помощь"
            )

        continue


    # ========================================================
    # /СТАТА
    # ========================================================

    if message in [
        "/стата",
        "/stats"
    ]:

        stats = get_user_stats(user_id)

        if not stats:

            send_message(
                user_id,
                "❌ Ты ещё не зарегистрирован.\n\n"
                "Напиши /start"
            )

            continue

        boost_line = ""
        if stats["boost_active"]:
            boost_line += (
                f"⚡ Буст дохода: x{stats['coin_multiplier']} "
                f"({stats['boost_left_min']} мин.)\n"
            )

        if stats["no_cd_active"]:
            boost_line += (
                f"🚀 Без КД: "
                f"({stats['no_cd_left_min']} мин.)\n"
            )

        send_message(
            user_id,
            "🌳 ТВОЯ СТАТИСТИКА\n\n"

            f"👤 Игрок: {stats['name']}\n"

            f"📏 Размер дилдака: "
            f"{stats['size']} см\n"

            f"🪙 Монет: "
            f"{stats['coins']}\n"

            f"{boost_line}"

            f"📅 Дней в игре: "
            f"{stats['days_in_game']}\n"

            f"🌱 Всего ростов: "
            f"{stats['growth_count']}\n"

            f"🗓 Регистрация: "
            f"{stats['registered_at']}\n"

            f"📈 Последний рост: "
            f"{stats['last_growth']}"
        )

        continue


    # ========================================================
    # /ТОП
    # ========================================================

    if message in [
        "/топ",
        "/top"
    ]:

        top = get_top()

        if not top:

            send_message(
                user_id,
                "Пока никто не зарегистрирован."
            )

            continue

        text = (
            "🏆 ТОП ИГРОКОВ\n\n"
        )

        for index, (
            name,
            size,
            coins
        ) in enumerate(top, start=1):

            text += (
                f"{index}. "
                f"{name} — "
                f"{size} см "
                f"({coins} 🪙)\n"
            )

        send_message(
            user_id,
            text
        )

        continue


    # ========================================================
    # /ПОМОЩЬ
    # ========================================================

    if message in [
        "/помощь",
        "/help"
    ]:

        send_message(
            user_id,
            "📚 КОМАНДЫ БОТА\n\n"

            "💬 Напиши 'дилда' — "
            f"получить +{MESSAGE_REWARD} 🪙 "
            f"(КД {MESSAGE_REWARD_COOLDOWN} сек.)\n\n"

            "/start — регистрация\n"

            "/стата — твоя статистика\n"

            "/баланс — монеты\n"

            "/топ — рейтинг игроков\n"

            "/магазин — магазин товаров\n"

            "/купить ID — купить товар\n"

            "/покупки — мои покупки\n"

            "/помощь — список команд"
        )

        continue


    # ========================================================
    # /ADMIN
    # ========================================================

    if message == "/admin":

        if not is_admin(user_id):

            send_message(
                user_id,
                "⛔ Доступ запрещён."
            )

            continue

        send_message(
            user_id,

            "👑 АДМИН-ПАНЕЛЬ\n\n"

            "📊 /admin stats\n"
            "Статистика бота\n\n"

            "👥 /admin users\n"
            "Список пользователей\n\n"

            "👤 /admin user ID\n"
            "Информация о пользователе\n\n"

            "📏 /admin set ID SIZE\n"
            "Установить размер\n\n"

            "➕ /admin add ID SIZE\n"
            "Изменить размер\n\n"

            "🪙 /admin coins ID AMOUNT\n"
            "Изменить монеты (+/-)\n\n"

            "💰 /admin setcoins ID AMOUNT\n"
            "Установить монеты\n\n"

            "📢 /admin рассылка ТЕКСТ\n"
            "Рассылка всем\n\n"

            "📢 /admin рассылка_онлайн ТЕКСТ\n"
            "Рассылка активным (24ч)\n\n"

            "👀 /admin рассылка_кто\n"
            "Показать получателей\n\n"

            "Например:\n"
            "/admin user 123456789\n"
            "/admin set 123456789 50\n"
            "/admin add 123456789 5\n"
            "/admin coins 123456789 100\n"
            "/admin coins 123456789 -50\n"
            "/admin setcoins 123456789 500\n"
            "/admin рассылка Всем привет!\n"
            "/admin рассылка_онлайн Бот обновился!"
        )

        continue


    # ========================================================
    # /ADMIN STATS
    # ========================================================

    if message == "/admin stats":

        if not is_admin(user_id):

            send_message(
                user_id,
                "⛔ Доступ запрещён."
            )

            continue

        (
            users_count,
            total_size,
            total_coins,
            growth_count,
            average_size
        ) = get_admin_stats()

        send_message(
            user_id,

            "📊 СТАТИСТИКА БОТА\n\n"

            f"👥 Пользователей: "
            f"{users_count}\n"

            f"🌳 Общий размер: "
            f"{total_size:.0f} см\n"

            f"📏 Средний размер: "
            f"{average_size:.1f} см\n"

            f"🪙 Всего монет: "
            f"{total_coins}\n"

            f"📈 Всего ростов: "
            f"{growth_count}"
        )

        continue


    # ========================================================
    # /ADMIN USERS
    # ========================================================

    if message == "/admin users":

        if not is_admin(user_id):

            send_message(
                user_id,
                "⛔ Доступ запрещён."
            )

            continue

        users = get_all_users()

        if not users:

            send_message(
                user_id,
                "Пользователей пока нет."
            )

            continue

        text = (
            "👥 ПОЛЬЗОВАТЕЛИ\n\n"
        )

        for index, (
            vk_id,
            name,
            size,
            coins,
            registered_at,
            last_growth
        ) in enumerate(users, start=1):

            text += (
                f"{index}. {name}\n"
                f"ID: {vk_id}\n"
                f"Размер: {size} см\n"
                f"Монет: {coins} 🪙\n"
                f"Регистрация: {registered_at}\n"
                f"Последний рост: {last_growth}\n\n"
            )

        send_message(
            user_id,
            text
        )

        continue


    # ========================================================
    # /ADMIN РАССЫЛКА_КТО
    # ========================================================

    if message == "/admin рассылка_кто":

        if not is_admin(user_id):

            send_message(
                user_id,
                "⛔ Доступ запрещён."
            )

            continue

        all_ids = get_all_user_ids()
        online_ids = get_online_user_ids(24)

        send_message(
            user_id,
            "👀 ПОЛУЧАТЕЛИ РАССЫЛКИ\n\n"
            f"Всего пользователей: {len(all_ids)}\n"
            f"Активных за 24ч: {len(online_ids)}\n\n"
            "Команды:\n"
            "/admin рассылка ТЕКСТ — всем\n"
            "/admin рассылка_онлайн ТЕКСТ — активным"
        )

        continue


    # ========================================================
    # /ADMIN РАССЫЛКА (ВСЕМ)
    # ========================================================

    if raw_message.lower().startswith("/admin рассылка "):

        if not is_admin(user_id):

            send_message(
                user_id,
                "⛔ Доступ запрещён."
            )

            continue

        # Берём текст после "/admin рассылка "
        text_to_send = raw_message[len("/admin рассылка "):].strip()

        if not text_to_send:

            send_message(
                user_id,
                "❌ Использование:\n\n"
                "/admin рассылка ТЕКСТ\n\n"
                "Например:\n"
                "/admin рассылка Всем привет!"
            )

            continue

        target_ids = get_all_user_ids()

        if not target_ids:

            send_message(
                user_id,
                "❌ Нет пользователей для рассылки."
            )

            continue

        send_message(
            user_id,
            f"📢 Начинаю рассылку {len(target_ids)} получателям..."
        )

        sent, failed = do_broadcast(
            target_ids,
            text_to_send,
            user_id
        )

        send_message(
            user_id,
            "✅ РАССЫЛКА ЗАВЕРШЕНА\n\n"
            f"📤 Отправлено: {sent}\n"
            f"❌ Ошибок: {failed}\n"
            f"👥 Всего: {len(target_ids)}"
        )

        continue


    # ========================================================
    # /ADMIN РАССЫЛКА_ОНЛАЙН (АКТИВНЫМ)
    # ========================================================

    if raw_message.lower().startswith("/admin рассылка_онлайн "):

        if not is_admin(user_id):

            send_message(
                user_id,
                "⛔ Доступ запрещён."
            )

            continue

        text_to_send = raw_message[len("/admin рассылка_онлайн "):].strip()

        if not text_to_send:

            send_message(
                user_id,
                "❌ Использование:\n\n"
                "/admin рассылка_онлайн ТЕКСТ\n\n"
                "Например:\n"
                "/admin рассылка_онлайн Бот обновился!"
            )

            continue

        target_ids = get_online_user_ids(24)

        if not target_ids:

            send_message(
                user_id,
                "❌ Нет активных пользователей за 24ч."
            )

            continue

        send_message(
            user_id,
            f"📢 Начинаю рассылку {len(target_ids)} активным..."
        )

        sent, failed = do_broadcast(
            target_ids,
            text_to_send,
            user_id
        )

        send_message(
            user_id,
            "✅ РАССЫЛКА ЗАВЕРШЕНА\n\n"
            f"📤 Отправлено: {sent}\n"
            f"❌ Ошибок: {failed}\n"
            f"👥 Всего: {len(target_ids)}"
        )

        continue


    # ========================================================
    # /ADMIN USER ID
    # ========================================================

    if message.startswith("/admin user "):

        if not is_admin(user_id):

            send_message(
                user_id,
                "⛔ Доступ запрещён."
            )

            continue

        parts = message.split()

        if len(parts) != 3:

            send_message(
                user_id,
                "❌ Использование:\n\n"
                "/admin user ID\n\n"
                "Например:\n"
                "/admin user 123456789"
            )

            continue

        try:
            target_id = int(parts[2])

        except ValueError:

            send_message(
                user_id,
                "❌ VK ID должен быть числом."
            )

            continue

        stats = get_user_stats(target_id)

        if not stats:

            send_message(
                user_id,
                "❌ Пользователь не найден."
            )

            continue

        boost_line = ""
        if stats["boost_active"]:
            boost_line += (
                f"Буст дохода: x{stats['coin_multiplier']} "
                f"({stats['boost_left_min']} мин.)\n"
            )

        if stats["no_cd_active"]:
            boost_line += (
                f"Без КД: "
                f"({stats['no_cd_left_min']} мин.)\n"
            )

        send_message(
            user_id,

            "👤 ИНФОРМАЦИЯ\n\n"

            f"Имя: {stats['name']}\n"

            f"VK ID: {stats['vk_id']}\n"

            f"Размер: {stats['size']} см\n"

            f"Монет: {stats['coins']} 🪙\n"

            f"{boost_line}"

            f"Дней в игре: "
            f"{stats['days_in_game']}\n"

            f"Ростов: "
            f"{stats['growth_count']}\n"

            f"Регистрация: "
            f"{stats['registered_at']}\n"

            f"Последний рост: "
            f"{stats['last_growth']}"
        )

        continue


    # ========================================================
    # /ADMIN SET ID SIZE
    # ========================================================

    if message.startswith("/admin set "):

        if not is_admin(user_id):

            send_message(
                user_id,
                "⛔ Доступ запрещён."
            )

            continue

        parts = message.split()

        if len(parts) != 4:

            send_message(
                user_id,
                "❌ Использование:\n\n"
                "/admin set ID SIZE\n\n"
                "Например:\n"
                "/admin set 123456789 50"
            )

            continue

        try:

            target_id = int(parts[2])
            new_size = int(parts[3])

        except ValueError:

            send_message(
                user_id,
                "❌ ID и размер должны быть числами."
            )

            continue

        if new_size < 0:

            send_message(
                user_id,
                "❌ Размер не может быть отрицательным."
            )

            continue

        cursor.execute("""
            UPDATE users
            SET size = ?
            WHERE vk_id = ?
        """, (
            new_size,
            target_id
        ))

        db.commit()

        if cursor.rowcount == 0:

            send_message(
                user_id,
                "❌ Пользователь не найден."
            )

        else:

            send_message(
                user_id,

                "✅ Размер изменён.\n\n"

                f"Пользователь: {target_id}\n"
                f"Новый размер: {new_size} см"
            )

        continue


    # ========================================================
    # /ADMIN ADD ID SIZE
    # ========================================================

    if message.startswith("/admin add "):

        if not is_admin(user_id):

            send_message(
                user_id,
                "⛔ Доступ запрещён."
            )

            continue

        parts = message.split()

        if len(parts) != 4:

            send_message(
                user_id,
                "❌ Использование:\n\n"
                "/admin add ID SIZE\n\n"
                "Например:\n"
                "/admin add 123456789 5"
            )

            continue

        try:

            target_id = int(parts[2])
            amount = int(parts[3])

        except ValueError:

            send_message(
                user_id,
                "❌ ID и значение должны быть числами."
            )

            continue

        cursor.execute("""
            UPDATE users
            SET size = size + ?
            WHERE vk_id = ?
        """, (
            amount,
            target_id
        ))

        db.commit()

        if cursor.rowcount == 0:

            send_message(
                user_id,
                "❌ Пользователь не найден."
            )

        else:

            updated_user = get_user(target_id)

            new_size = updated_user[2]

            # Не позволяем получить отрицательное значение
            if new_size < 0:

                cursor.execute("""
                    UPDATE users
                    SET size = 0
                    WHERE vk_id = ?
                """, (target_id,))

                db.commit()

                new_size = 0

            send_message(
                user_id,

                "✅ Размер изменён.\n\n"

                f"Пользователь: {target_id}\n"
                f"Изменение: {amount:+d} см\n"
                f"Новый размер: {new_size} см"
            )

        continue


    # ========================================================
    # /ADMIN COINS ID AMOUNT
    # ========================================================

    if message.startswith("/admin coins "):

        if not is_admin(user_id):

            send_message(
                user_id,
                "⛔ Доступ запрещён."
            )

            continue

        parts = message.split()

        if len(parts) != 4:

            send_message(
                user_id,
                "❌ Использование:\n\n"
                "/admin coins ID AMOUNT\n\n"
                "AMOUNT может быть отрицательным.\n\n"
                "Например:\n"
                "/admin coins 123456789 100\n"
                "/admin coins 123456789 -50"
            )

            continue

        try:

            target_id = int(parts[2])
            amount = int(parts[3])

        except ValueError:

            send_message(
                user_id,
                "❌ ID и значение должны быть числами."
            )

            continue

        cursor.execute("""
            UPDATE users
            SET coins = coins + ?
            WHERE vk_id = ?
        """, (
            amount,
            target_id
        ))

        db.commit()

        if cursor.rowcount == 0:

            send_message(
                user_id,
                "❌ Пользователь не найден."
            )

        else:

            updated_user = get_user(target_id)

            new_coins = updated_user[3]

            # Не позволяем уйти в минус
            if new_coins < 0:

                cursor.execute("""
                    UPDATE users
                    SET coins = 0
                    WHERE vk_id = ?
                """, (target_id,))

                db.commit()

                new_coins = 0

            send_message(
                user_id,

                "✅ Монеты изменены.\n\n"

                f"Пользователь: {target_id}\n"
                f"Изменение: {amount:+d} 🪙\n"
                f"Новый баланс: {new_coins} 🪙"
            )

        continue


    # ========================================================
    # /ADMIN SETCOINS ID AMOUNT
    # ========================================================

    if message.startswith("/admin setcoins "):

        if not is_admin(user_id):

            send_message(
                user_id,
                "⛔ Доступ запрещён."
            )

            continue

        parts = message.split()

        if len(parts) != 4:

            send_message(
                user_id,
                "❌ Использование:\n\n"
                "/admin setcoins ID AMOUNT\n\n"
                "Например:\n"
                "/admin setcoins 123456789 500"
            )

            continue

        try:

            target_id = int(parts[2])
            new_coins = int(parts[3])

        except ValueError:

            send_message(
                user_id,
                "❌ ID и значение должны быть числами."
            )

            continue

        if new_coins < 0:

            send_message(
                user_id,
                "❌ Монеты не могут быть отрицательными."
            )

            continue

        cursor.execute("""
            UPDATE users
            SET coins = ?
            WHERE vk_id = ?
        """, (
            new_coins,
            target_id
        ))

        db.commit()

        if cursor.rowcount == 0:

            send_message(
                user_id,
                "❌ Пользователь не найден."
            )

        else:

            send_message(
                user_id,

                "✅ Баланс установлен.\n\n"

                f"Пользователь: {target_id}\n"
                f"Новый баланс: {new_coins} 🪙"
            )

        continue


    # ========================================================
    # НЕИЗВЕСТНАЯ КОМАНДА
    # ========================================================

    send_message(
        user_id,

        "🤔 Неизвестная команда.\n\n"
        "Напиши /помощь."
    )
