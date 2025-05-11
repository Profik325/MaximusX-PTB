import sqlite3
import os

global_db = sqlite3.connect(os.path.join(os.path.dirname(__file__), "global_table.db"))
global_cursor = global_db.cursor()

# USERS

def create_table_users():
    global_cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
        userid INTEGER NOT NULL UNIQUE PRIMARY KEY,
        username TEXT NOT NULL,
        balance INTEGER DEFAULT 100,
        job_level INTEGER DEFAULT 0,
        jobs_done INTEGER DEFAULT 0,
        bonuses_obtained INTEGER DEFAULT 0)''')
    global_db.commit()


async def recreate_table_users():
    global_cursor.execute("DROP TABLE IF EXISTS users")
    create_table_users()


async def add_user(username, userid):
    data_users = (userid, username, 100)
    data_credits = (userid,)
    global_cursor.execute("INSERT OR IGNORE INTO users (userid, username, balance) VALUES (?, ?, ?)", data_users)
    global_db.commit()

    global_cursor.execute(
"""INSERT OR IGNORE INTO credits (userid) VALUES (?)""",
        data_credits)
    # параметризация (?, ?) = защита от SQL-инъекций

    global_db.commit()


async def check_user(user, update=None, context=None, isusername=False, send_msg=True):
    if isusername:
        result = global_cursor.execute(
            "SELECT 1 FROM users WHERE username = ?",
            (user.lstrip('@'),)).fetchone()
        if not bool(result) and send_msg:
            await update.message.reply_text("❌ Невозможно добавить пользователя автоматически.")
        return bool(result)
    else:
        result = global_cursor.execute(
            "SELECT 1 FROM users WHERE userid = ?",
            (user.id,)).fetchone()
        if not result:
            await add_user(user.username, user.id)
        return True


async def get_balance(user, isusername=False):
    await check_user(user, isusername=isusername)
    if isusername:
        return global_cursor.execute("""
            SELECT balance FROM users WHERE username = ?""", (user,)).fetchone()[0]
    return global_cursor.execute("""SELECT balance FROM users WHERE userid = ?""",
                                 (user.id,)).fetchone()[0]


async def add_balance(addition, user, isusername=False):
    await check_user(user, isusername=isusername)
    if isusername:
        global_cursor.execute("""
                UPDATE users SET balance = balance + ? WHERE username = ?""", (addition, user,))
        global_db.commit()
    global_cursor.execute("""UPDATE users SET balance = balance + ? WHERE userid = ?""",
                                 (addition, user.id,))
    global_db.commit()


 # jobs (only two funcs for everything instead of 8 lmao)

async def set_get_jobs_bonuses_done(user, amount=0, bonuses=False, reset=False, isusername="not yet used"):
    await check_user(user, isusername=False)
    if reset:
        global_cursor.execute("UPDATE users SET jobs_done = 0 WHERE userid = ?", (user.id,))
        global_cursor.execute("UPDATE users SET bonuses_obtained = 0 WHERE userid = ?", (user.id,))
        global_db.commit()
        return

    if not amount:
        return global_cursor.execute("""
                    SELECT jobs_done, bonuses_obtained FROM users WHERE userid = ?""", (user.id,)).fetchone()
    else:
        if not bonuses:
            global_cursor.execute("UPDATE users SET jobs_done = jobs_done + ? WHERE userid = ?",
                                  (amount, user.id,))
        else: global_cursor.execute("UPDATE users SET bonuses_obtained = bonuses_obtained + ? WHERE userid = ?",
                                    (amount, user.id,))
        global_db.commit()


async def set_get_job_lvl(user, amount=0):
    await check_user(user)
    if not amount: return global_cursor.execute("SELECT job_level FROM users WHERE userid = ?",
                                                (user.id,)).fetchone()[0]
    else: global_cursor.execute("UPDATE users SET job_level = job_level + ? WHERE userid = ?",
                                (amount, user.id,))
    global_db.commit()


  # credits


def create_table_credits():
    global_cursor.execute('''
        CREATE TABLE IF NOT EXISTS credits (
        creditid INTEGER PRIMARY KEY AUTOINCREMENT,
        userid INTEGER NOT NULL UNIQUE,
        current_credit INTEGER DEFAULT 0,
        last_payment_time TEXT DEFAULT NULL,
        paid_today INTEGER DEFAULT 0,
        daily_payment INTEGER DEFAULT 0,
        innate_credit INTEGER DEFAULT 0,
        credit_obtainment_time TEXT)''')
    global_db.commit()


async def recreate_table_credits():
    global_cursor.execute("DROP TABLE IF EXISTS credits")
    create_table_credits()


# CHATS


def create_table_chats():
    global_cursor.execute('''
        CREATE TABLE IF NOT EXISTS chats (
        chatid INTEGER NOT NULL UNIQUE PRIMARY KEY,
        member_greeting TEXT DEFAULT NONE,
        show_member_greeting BOOL DEFAULT FALSE)''')

    global_db.commit()


async def add_chat(update, context):
    data = (update.effective_chat.id, None, False)
    global_cursor.execute(
        "INSERT OR IGNORE INTO chats (chatid, member_greeting, show_member_greeting) VALUES (?, ?, ?)", data)
    global_db.commit()


async def check_chat(update, context):
    chat = update.effective_chat
    if not global_cursor.execute("SELECT * FROM chats WHERE chatid = ?", (chat.id,)).fetchone():
        await add_chat(update, context)


async def change_greeting_to_show(update, context):
    try:
        chat = update.effective_chat
        await check_chat(update, context)

        global_cursor.execute(
            """UPDATE chats SET show_member_greeting = TRUE WHERE chatid = ?""",
            (str(chat.id),))
        global_db.commit()

        await update.message.reply_text("✅ Приветствие новых пользователей включено")
    except Exception as e:
        await update.message.reply_text("❌ Ошибка при включении приветствия")


async def change_greeting_to_hide(update, context):
    try:
        chat = update.effective_chat
        await check_chat(update, context)

        global_cursor.execute(
            """UPDATE chats SET show_member_greeting = FALSE WHERE chatid = ?""",
            (str(chat.id),))
        global_db.commit()

        await update.message.reply_text("✅ Приветствие новых пользователей отключено")
    except Exception as e:
        await update.message.reply_text("❌ Ошибка при отключении приветствия")


def get_greeting_bool_state(chat_id):
    global_cursor.execute("SELECT show_member_greeting FROM chats WHERE chatid = ?", (str(chat_id),))
    return global_cursor.fetchone()[0]

def get_greeting_text_for_sys(chat_id):
    global_cursor.execute("SELECT member_greeting FROM chats WHERE chatid = ?", (str(chat_id),))
    return global_cursor.fetchone()[0] or False


async def get_greeting_text(update, context):
    await check_chat(update, context)
    text = global_cursor.execute(
        "SELECT member_greeting FROM chats WHERE chatid = ?",
        (update.effective_chat.id,)).fetchone()[0]
    if not text:
        text = ("Стальной шепот револьвера, щелчок курка... Тени смотрят вам в спину. Эта пуля - последняя милость, "
                "что вам светит, {username}...")
        await update.message.reply_text('Текущее приветствие новых пользователей отсутствует, приветствие по умолчанию:\n' + text)
        return
    await update.message.reply_text('Текущее приветствие новых пользователей:\n' + text)
    return text


async def set_greeting_text(update, context):
    try:
        # Получаем текст после команды (без разделения на строки)
        if len(update.message.text.split('\n', maxsplit=1)) > 1: text = update.message.text.split('\n', maxsplit=1)[1]
        else: text = ''

        if not text:
            await update.message.reply_text("❌ Укажите текст приветствия после команды")
            return

        chat = update.effective_chat
        await check_chat(update, context)

        global_cursor.execute(
            """UPDATE chats SET member_greeting = ? WHERE chatid = ?""",
            (text, str(chat.id)))
        global_db.commit()

        await update.message.reply_text("✅ Текст приветствия обновлен")
    except Exception as e:
        await update.message.reply_text("❌ Ошибка при обновлении приветствия")
        raise e


async def change_greeting_to_show_txt(update, context):
    if update.message.text[1:].lower() == 'приветствие' and update.message.text[1] == '+':
        await change_greeting_to_show(update, context)


async def change_greeting_to_hide_txt(update, context):
    if update.message.text[1:].lower() == 'приветствие' and update.message.text[1] == '-':
        await change_greeting_to_hide(update, context)