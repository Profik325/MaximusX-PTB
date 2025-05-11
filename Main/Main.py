import logging
from datetime import datetime, timedelta
from random import randint

from pyexpat.errors import messages
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.constants import ChatMemberStatus
from telegram.ext import (filters, CallbackQueryHandler, Application,
    MessageHandler, ConversationHandler, CommandHandler,
    ContextTypes, ChatMemberHandler)
import asyncio
import math
from functools import partial

from SQLtables import *
from config import BOT_TOKEN, forbidden
from Poker import *

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Состояния для Conversation Handler
CHOOSING, BET, GAME = range(3)
TAKING_CREDIT, REPLY_MESSAGE, WHY_CREDIT = range(3)

# Хранение данных пользователей
create_table_users()
create_table_chats()
create_table_credits()
admin_id = '5724899384'
game_type = ''


async def start(update, context):
    user = update.effective_user
    await check_user(user, update, context)

    await update.message.reply_text(
        f"Используйте команды:\n"
        "/work - заработать\n"
        "/dice - игра в кубик\n"
        "/slots - игровые автоматы\n"
        "/balance - проверить баланс"
    )
    if update.message.text == 'Кубик':
        return await dice(update, context)
    if update.message.text == 'Слоты':
        return await slots(update, context)


# Функции про разлечения и заработок

async def work(update, context):
    good_job = False
    user = update.effective_user
    cooldown = timedelta(hours=3, minutes=1)
    last_work = context.user_data.get("last_work")
    await check_user(user, update, context)
    level = await set_get_job_lvl(user)
    jobs_to_lvlup = round(8 * (level ** 1.3) / (1 + 0.15 * level)) + 2
    bonuses_to_lvlup = round(1.5 * (level ** 2.0) / (1 + 0.2 * level)) + 1

    if last_work and (datetime.now() - last_work) < cooldown:
        remaining = cooldown - (datetime.now() - last_work)
        if remaining > timedelta(hours=1):
            hr = 'час' if remaining.seconds // 3600 == 1 else 'часа'
            mn = 'минуту' if remaining.seconds % 3600 // 60 == 1 else 'минут'
            if remaining.seconds % 3600 // 60 != 0:
                await update.message.reply_text(
                    f"⏳ Следующая работа доступна через "
                    f"{remaining.seconds // 3600} {hr} {remaining.seconds % 3600 // 60} {mn}.")
                return
            await update.message.reply_text(
                f"⏳ Следующая работа доступна через "
                f"{remaining.seconds // 3600} {hr}.")
            return
        mn = 'минута' if remaining.seconds // 60 == 1 else 'минут'
        await update.message.reply_text(f"⏳ Следующая работа доступна через {remaining.seconds // 60} {mn}.")
        return

    salary = randint(10, 102)
    if salary >= 100: salary, good_job = randint(100, 500) * 5, True
    salary = int((salary + (salary * (level * 0.1))) // 1)
    context.user_data["last_work"] = datetime.now()

    current_balance = await get_balance(user)
    global_cursor.execute("INSERT OR REPLACE INTO users (userid, username, balance) VALUES (?, ?, ?)",
                          (user.id, user.username, current_balance + salary))
    global_db.commit()

    if not good_job:
        await set_get_jobs_bonuses_done(user, 1)
        await update.message.reply_text(
            f"💵 Вы поработали и заработали {salary}! Текущий баланс: {await get_balance(user)}."
            f"До повышения осталось отработать еще {jobs_to_lvlup - 1} раз или получить {bonuses_to_lvlup} премий.")
    else:
        await set_get_jobs_bonuses_done(user, 1, True)
        await update.message.reply_text(
            f"💰 Вы поработали на славу и получили премию, заработав {salary}! Текущий баланс: "
            f"{await get_balance(user)}\n"
            f"До повышения осталось отработать еще {jobs_to_lvlup} раз или получить {bonuses_to_lvlup - 1} премий.")

    jobs_done, bonuses_obtained = (await set_get_jobs_bonuses_done(user),
                                   await set_get_jobs_bonuses_done(user, bonuses=True))
    if jobs_done == jobs_to_lvlup or bonuses_obtained == bonuses_to_lvlup:
        await set_get_job_lvl(user, 1)
        await set_get_jobs_bonuses_done(user, reset=True)
        await update.message.reply_text(
            f"👏 Вы так хорошо работали, что вас повысили до уровня {level + 2}! Теперь ваш модификатор к "
            f"зарплате составляет {(level + 2) * 0.1 + 1.0}.")


async def dice_command(update, context):
    context.user_data["game_type"] = "Кубик"
    return await start_game(update, context)


async def slots_command(update, context):
    context.user_data["game_type"] = "Слоты"
    return await start_game(update, context)


async def start_game(update, context):
    await check_user(update.effective_user, update, context)
    global game_type
    game_type = context.user_data.get("game_type", "Игра")
    # print(game_type)
    cooldown = timedelta(minutes=1)

    if game_type == "Кубик" and context.user_data.get('last_dice_rolled') and \
            (datetime.now() - context.user_data.get('last_dice_rolled')) < cooldown:
        remaining = cooldown - (datetime.now() - context.user_data.get('last_dice_rolled'))
        await update.message.reply_text(f"⏳ Вы уже кидали кубик недавно, подождите еще {remaining.seconds} секунд.",
                                        reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    elif game_type == "Слоты" and context.user_data.get('last_slots_rolled') and \
            (datetime.now() - context.user_data.get('last_slots_rolled')) < cooldown:
        remaining = cooldown - (datetime.now() - context.user_data.get('last_slots_rolled'))
        await update.message.reply_text(f"⏳ Вы уже крутили слоты недавно, подождите еще {remaining.seconds} секунд.",
                                        reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    if context.chat_data.get('is_dice_rolling') and game_type == "Кубик":
        await update.message.reply_text(f"⏳ Кто-то уже кидает кубик.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    elif context.chat_data.get('is_slots_rolling') and game_type == "Слоты":
        await update.message.reply_text(f"⏳ Кто-то уже крутит слоты.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    if game_type == "Кубик":
        context.chat_data["is_dice_rolling"] = True
    elif game_type == "Слоты":
        context.chat_data["is_slots_rolling"] = True
    msg = await update.message.reply_text(
        "Введите сумму ставки (Только число!) ответом на это сообщение.\nНапишите «Отмена», чтобы прервать действие.")
    context.chat_data['bet_msg_to_reply'] = msg.id
    return BET


async def process_bet(update, context):
    user = update.effective_user
    if not update.message.reply_to_message or update.message.reply_to_message.from_user.id != context.bot.id or \
            context.chat_data.get('bet_msg_to_reply') != update.message.reply_to_message.id:
        return BET
    try:
        bet = int(update.message.text)
        if context.user_data.get("game_type") == "Кубик" and (bet < 10 or bet > 5000):
            await update.message.reply_text("❌ Минимальная ставка в «Кубике» ─ 10, максимальная ─ 5000.")
            return BET
        elif context.user_data.get("game_type") == "Слоты" and (bet < 500 or bet > 1000000):
            await update.message.reply_text("❌ Минимальная ставка в «Слотах» ─ 500, максимальная ─ 1000000.")
            return BET
        if bet > int(await get_balance(user)):
            await update.message.reply_text("❌ Недостаточно средств.")
            return BET

        context.user_data["current_bet"] = bet
        try:
            await check_user(user, update, context)
            global_cursor.execute("""
            UPDATE users SET balance = balance + ? WHERE userid = ?""", (-bet, user.id))
            global_db.commit()
        except Exception:
            await update.message.reply_text(
                "❌ Ошибка со снятием денег с счета пользователя, обратитесь к создателю бота @Profik_X",
                reply_markup=ReplyKeyboardRemove())
            raise ValueError("Could not find userid in users - SQL")

        if context.user_data.get("game_type") == "Кубик":
            return await dice(update, context)
        if context.user_data.get("game_type") == "Слоты":
            return await slots(update, context)

    except ValueError:
        if not update.message.text == 'Отмена':
            await update.message.reply_text("❌ Введите число!")
            return BET
        return await cancel(update, context)


async def dice(update, context):
    user = update.effective_user
    # await update.message.reply_dice("Ставка принята! Крутим кубик...")
    dice_message = await update.message.reply_dice(emoji="🎲")
    value = dice_message.dice.value

    if value <= 3:
        win = 0
    elif value == 4:
        win = context.user_data["current_bet"] * 1
    elif value == 5:
        win = context.user_data["current_bet"] * 1.5
    else:
        win = context.user_data["current_bet"] * 3

    await asyncio.sleep(3.5)

    if win:
        await update.message.reply_text(
            f"🎲 На кубике выпало {value}. \n"
            f'✅ Выигрыш! Вы получили {int(win // 1)}! \n'
            f'Теперь ваш баланс составляет {int(await get_balance(user) + win)}.',
            reply_markup=ReplyKeyboardRemove())
    else:
        await update.message.reply_text(
            f"🎲 На кубике выпало {value}. \n"
            f'❌ Проигрыш! Вы все потеряли. \n'
            f'Теперь ваш баланс составляет {int(await get_balance(user) + win)}.',
            reply_markup=ReplyKeyboardRemove())

    context.user_data["last_dice_rolled"] = datetime.now()
    del context.chat_data["is_dice_rolling"]

    try:
        await check_user(user, update, context)
        global_cursor.execute("""UPDATE users SET balance = balance + ? WHERE userid = ?""",
                              (win, user.id))
        global_db.commit()
    except Exception:
        await update.message.reply_text(
            "❌ Ошибка с начислением денег на счет, обратитесь к создателю бота @Profik_X",
            reply_markup=ReplyKeyboardRemove())
        raise ValueError("Could not find userid in users - SQL")

    return ConversationHandler.END


async def slots(update, context):
    user = update.effective_user
    two_combinations = (39, 2, 35, 54, 49, 42, 47, 1, 2, 35, 38, 26, 11, 21,
                                 26, 24, 6, 38, 41, 27, 23, 24, 38, 18, 44, 42, 2, 33,
                                 17, 49, 3, 9, 4, 33, 13, 5)
    three_combinations = (22, 1)  # 22 - 3 blueberries
    two_of_sevens = (32, 52, 61, 62, 16, 56, 63, 60, 48)
    three_of_sevens = 64
    two_of_bars = (2, 33, 17, 49, 3, 9, 4, 33, 13, 5)
    three_of_bars = 1
    slot_message = await update.message.reply_dice(emoji="🎰")
    value = slot_message.dice.value
    logger.log(50, 'slots.dice value = '+ str(value))
    bet = context.user_data.get('current_bet', 0)

    if value == 64:
        win = int(bet * 10)
        amount_result = 'выпало три семерки! ДЖЕКПОТ!'
        result = '🎉 ДЖЕКПОТ!'
    elif value in three_combinations:
        win = int(bet * 5)
        amount_result = 'выпало три одинаковых символа!'
        result = '🎉 Большая удача!'
    elif value in two_of_sevens:
        win = int(bet * 3)
        amount_result = 'выпало две семерки!'
        result = '✅ Выигрыш!'
    elif value in two_combinations:
        win = int(bet * 1.25)
        amount_result = 'выпало два одинаковых символа.'
        result = '✅ Выигрыш!'
    else:
        win = 0
        amount_result = 'не выпало ни одной пары одинаковых символов.'
        result = '💥 Проигрыш!'

    await asyncio.sleep(3.5)

    if win: await update.message.reply_text(
        f" 🎰 Результат: {amount_result} \n"
        f'{result} Вы получили {int(win // 1)}! \n'
        f'Теперь ваш баланс составляет {int(await get_balance(user) + win)}.',
        reply_markup=ReplyKeyboardRemove())
    else: await update.message.reply_text(
        f'{result} Вы все потеряли. \n'
        f'Теперь ваш баланс составляет {int(await get_balance(user) + win)}.',
        reply_markup=ReplyKeyboardRemove())

    try:
        await check_user(user, update, context)
        global_cursor.execute("""UPDATE users SET balance = balance + ? WHERE userid = ?""",
                              (win, user.id))
        global_db.commit()
    except Exception:
        await update.message.reply_text(
            "❌ Ошибка с начислением денег на счет, обратитесь к создателю бота @Profik_X",
            reply_markup=ReplyKeyboardRemove())
        raise ValueError("Could not find userid in users - SQL")

    context.user_data["last_slots_rolled"] = datetime.now()
    del context.chat_data["is_slots_rolling"]

    return ConversationHandler.END


async def cancel(update, context):
    await context.bot.send_message(
        chat_id=update.effective_chat.id,  # ID текущего чата
        text="❌ Отмена... Действие прервано.")
    '''
    await context.bot.edit_message_reply_markup(
        chat_id=update.effective_chat.id,
        message_id=context.user_data['inline_keyboard_message_id'],  # ID сообщения с клавиатурой
        reply_markup=None  # Удаляем клавиатуру
    )'''
    if game_type == "Кубик": del context.chat_data["is_dice_rolling"]
    if game_type == "Слоты": del context.chat_data["is_slots_rolling"]
    if context.chat_data.get('is_getting_credit'): del context.chat_data['is_getting_credit']
    if context.user_data.get('credit_amount'): del context.user_data['credit_amount']
    if context.chat_data.get('credit_msg_to_reply'): del context.chat_data['credit_msg_to_reply']
    if context.chat_data.get('why_msg_to_reply'): del context.chat_data['why_msg_to_reply']
    return ConversationHandler.END


  # функции про экономику бота

async def add_money(update, context):
    if update.message.reply_to_message:
        amount = update.message.text.split()[1] if len(update.message.text.split()) == 2 else (
            update.message.text.split())[2]
        user = update.message.reply_to_message.from_user.username
    else:
        amount = update.message.text.split()[1] if len(update.message.text.split()) == 3 else (
            update.message.text.split())[2]
        user = update.message.text.split()[2].lstrip('@') if len(update.message.text.split()) == 3 else (
            update.message.text.split())[3].lstrip('@')

    try:
        if int(amount) <= 0:
            await update.message.reply_text("❌ Минимальная сумма должна превышать 0.")
            return
    except Exception:
        await update.message.reply_text("❌ Введите число.")
        return



    await check_user(user, update, context, isusername=True)
    userid = global_cursor.execute("""SELECT userid FROM users WHERE username = ?""",
                                   (user,)).fetchone()[0]
    if not str(update.effective_user.id) == admin_id:
        if update.message.text == 'Выписать чек': return await transfer_money(update, context)
        await update.message.reply_text("❌ Недостаточно прав.")
        return

    try:
        global_cursor.execute("""UPDATE users SET balance = balance + ? WHERE userid = ?""",
                              (amount, userid))
        global_db.commit()
    except Exception:
        await update.message.reply_text(
            "❌ Ошибка с начислением денег на счет.")
        raise ValueError("Could not find userid in users - SQL")
    await update.message.reply_text("✅ Деньги начислены на счет.")
    # await update.message.reply_text("✅ Соси мой пенис, хуила.")


async def transfer_money(update, context):
    if update.message.reply_to_message:
        user_send_to = update.message.reply_to_message.from_user
        amount = update.message.text.split()[1] if len(update.message.text.split()) == 2 else (
            update.message.text.split())[2]
        isusername = False
    else:
        amount = update.message.text.split()[1] if len(update.message.text.split()) == 3 else (
            update.message.text.split())[2]
        user_send_to = update.message.text.split()[2].lstrip('@') if len(update.message.text.split()) == 3 else (
            update.message.text.split())[3].lstrip('@')
        isusername = True

    if int(amount) < 10:
        await update.message.reply_text("❌ Минимальная сумма перевода - 10.")
        return

    user_send_from = update.message.from_user

    await check_user(user_send_from, update, context)
    if not await check_user(user_send_to, update, context, isusername=isusername, send_msg=False):
        await update.message.reply_text("❌ Невозможно перевести деньги пользователю, попросите его написать /start.")
        return

    if int(amount) > global_cursor.execute("""SELECT balance FROM users WHERE userid = ?""",
                                   (user_send_from.id,)).fetchone()[0]:
        await update.message.reply_text("❌ Недостаточно средств на счету отправителя.")
        return

    global_cursor.execute("""UPDATE users SET balance = balance + ? WHERE userid = ?""",
                          (-int(amount), user_send_from.id,))
    global_db.commit()
    if isusername:
        global_cursor.execute("""UPDATE users SET balance = balance + ? WHERE username = ?""",
                              (amount, user_send_to, ))
        await update.message.reply_text(
            f"✅ Деньги перечислены на счет {user_send_to} с счета {user_send_from.username}.")
    else:
        global_cursor.execute("""UPDATE users SET balance = balance + ? WHERE username = ?""",
                              (amount, user_send_to.id,))
        await update.message.reply_text(
            f"✅ Деньги перечислены на счет {user_send_to.username} с счета {user_send_from.username}.")

    global_db.commit()


async def balance(update, context):
    await check_user(update.effective_user, update, context)
    user = update.effective_user
    if update.message.reply_to_message:  # Если это ответ на другое сообщение
        replier = update.message.reply_to_message.from_user.username
        await others_balance(update, context, userN=replier)
        return
    credit_amount = global_cursor.execute("""SELECT current_credit FROM credits WHERE userid = ?""",
                                          (user.id,)).fetchone()[0]
    global_db.commit()
    if credit_amount:
        await update.message.reply_text(f"💰 Ваш баланс: {await get_balance(user)}\n"
                                        f"🧾 Ваша задолженность банку: {credit_amount}")
        return
    await update.message.reply_text(f"💰 Ваш баланс: {await get_balance(user)}")


async def others_balance(update, context, userN=None):
    await check_user(update.effective_user, update, context)
    user = userN if userN else update.message.text.split()[1].lstrip('@')

    try:
        await check_user(user, update, context, isusername=True)
        await update.message.reply_text(f"💰 Баланс {user}: {await get_balance(user, isusername=True)}")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при попытке получить баланс пользователя.")
        raise e


  # кредиты и задолженности

async def credit_command(update, context):
    await check_user(update.effective_user)
    if context.chat_data.get('is_getting_credit'):
        await update.message.reply_text(f"⏳ Встаньте в очередь за получением кредита, пожалуйста.",
                                        reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    if global_cursor.execute(
            """SELECT current_credit FROM credits WHERE userid = ?""",
            (update.effective_user.id,)).fetchone()[0]:
        await update.message.reply_text(f"❌ Вы еще не погасили существующий кредит.",
                                        reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    context.chat_data['is_getting_credit'] = True
    return await take_credit(update, context)


async def take_credit(update, context):
    msg = update.message.text
    await check_user(update.effective_user)
    if len(msg.split()) <= 2:
        reply_msg = await update.message.reply_text(
        """Введите сумму, которую хотите взять в кредит (Только число!) ответом на это сообщение.
Напишите «Отмена», чтобы прервать действие.""")
        context.chat_data['credit_msg_to_reply'] = reply_msg.id
        return REPLY_MESSAGE

    else:
        amount = update.message.text.split()[2] if len(update.message.text.split()) == 3 else (
            update.message.text.split())[3]
        amount = int(amount)
        if amount == 50 or amount > 10000:
            await update.message.reply_text(
                """❌ Минимальная сумма кредита, которую наш банк может выдать ─ 50, максимальная ─ 10000.""")
            return ConversationHandler.END
        user = update.message.from_user
        why_msg = await update.message.reply_text("""Для каких целей вы хотите взять этот кредит?""")
        context.chat_data['why_msg_to_reply'] = why_msg.id
        context.user_data['credit_amount'] = amount
        return WHY_CREDIT


async def reply_message_credit(update, context):
    if not update.message.reply_to_message or update.message.reply_to_message.from_user.id != context.bot.id or \
            context.chat_data.get('credit_msg_to_reply') != update.message.reply_to_message.id:
        return REPLY_MESSAGE
    try:
        if int(update.message.text) < 50 or int(update.message.text) > 10000:
            await update.message.reply_text(
                """❌ Минимальная сумма кредита, которую наш банк может выдать ─ 50, максимальная ─ 10000.""")

            if context.chat_data.get('is_getting_credit'): del context.chat_data['is_getting_credit']
            if context.user_data.get('credit_amount'): del context.user_data['credit_amount']
            if context.chat_data.get('credit_msg_to_reply'): del context.chat_data['credit_msg_to_reply']
            if context.chat_data.get('why_msg_to_reply'): del context.chat_data['why_msg_to_reply']

            return ConversationHandler.END
        context.user_data['credit_amount'] = int(update.message.text)
        why_msg = await update.message.reply_text(
            """Для каких целей вы хотите взять этот кредит?""")
        context.chat_data['why_msg_to_reply'] = why_msg.id
        return WHY_CREDIT

    except ValueError:
        if not update.message.text == 'Отмена':
            await update.message.reply_text("❌ Введите число!")
            return REPLY_MESSAGE
        return await cancel(update, context)


async def why_credit(update, context):
    if not update.message.reply_to_message or update.message.reply_to_message.from_user.id != context.bot.id or \
            context.chat_data.get('why_msg_to_reply') != update.message.reply_to_message.id:
        return WHY_CREDIT
    msg = [i.lower() for i in update.message.text.split()]
    amount = context.user_data.get("credit_amount")
    user = update.effective_user
    try:
        if not set(msg).intersection(forbidden):
            daily_percent = calculate_daily_percent(amount)

            global_cursor.execute("""UPDATE users SET balance = balance + ? WHERE userid = ?""",
                                  (amount, user.id))
            global_db.commit()
            global_cursor.execute("""UPDATE credits SET current_credit = current_credit + ? WHERE userid = ?""",
                                  (amount * 1.3 // 1, user.id))
            global_db.commit()
            global_cursor.execute("""UPDATE credits SET daily_payment = ? WHERE userid = ?""",
                                  (((amount * 1.3) * (daily_percent / 100)) // 1, user.id))
            global_db.commit()
            global_cursor.execute("""UPDATE credits SET innate_credit = ? WHERE userid = ?""",
                                  (amount * 1.3 // 1, user.id))
            global_db.commit()
            global_cursor.execute("""UPDATE credits SET credit_obtainment_time = ? WHERE userid = ?""",
                                  (datetime.now().isoformat(), user.id))
            global_db.commit()
            
            context.job_queue.run_repeating(
                callback=lambda context: increase_credit(update=update, context=context),
                                                            # ^ Функция, которая будет вызываться
                interval=3600 * 8,                          # Интервал в секундах
                first=3600 * 8,                             # Через сколько секунд запустится в первый раз
                chat_id=update.effective_chat.id,           # Параметры для callback
                data={"user_id": update.effective_user.id}  # Доп. данные
            )
            context.job_queue.run_repeating(
                callback=lambda context: pay_credit_time_out(update=update, context=context),
                interval=3600 * 24,
                first=3600 * 24,
                chat_id=update.effective_chat.id,
                data={"user_id": update.effective_user.id}
            )

            await update.message.reply_text(
f'✅ Выдача кредита одобрена. @{update.effective_user.username}, средства успешно поступили на ваш счет. '
f'Настоятельно просим не нарушать условия договора и выплачивать не менее {daily_percent}% кредита ежедневно '
f'(Более детальная информация по договору: "Кредит").')

        else:
            await update.message.reply_text(
f"❌ Извините, но наш банк вынужден отказать в выдаче кредита в связи с неприемлемой целью использования средств.")
    except Exception as e:
        logger.log(50, e)
        await update.message.reply_text(
            "❌ Произошла ошибка при выдаче кредита, обратитесь к создателю бота @Profik_X.")

    if context.chat_data.get('is_getting_credit'): del context.chat_data['is_getting_credit']
    if context.user_data.get('credit_amount'): del context.user_data['credit_amount']
    if context.chat_data.get('credit_msg_to_reply'): del context.chat_data['credit_msg_to_reply']
    if context.chat_data.get('why_msg_to_reply'): del context.chat_data['why_msg_to_reply']

    return ConversationHandler.END


async def increase_credit(update, context):
    user = update.effective_user
    amount = global_cursor.execute(
        """SELECT current_credit FROM credits WHERE userid = ?""", (user.id,)).fetchone()[0]
    new_amount = amount * 1.1
    global_cursor.execute("""UPDATE credits SET current_credit = ? WHERE userid = ?""",
                          (new_amount // 1, user.id,))
    global_db.commit()
    await update.message.reply_text(f"💸 Кредит @{user.username} растет...")


async def pay_credit_time_out(update, context):
    user = update.effective_user
    balance = await get_balance(user)
    credit_amount = global_cursor.execute(
        """SELECT current_credit FROM credits WHERE userid = ?""", (user.id,)).fetchone()[0]

    last_payment_time = global_cursor.execute(
        """SELECT last_payment_time FROM credits WHERE userid = ?""", (user.id,)).fetchone()[0]
    paid_today = global_cursor.execute(
        """SELECT paid_today FROM credits WHERE userid = ?""", (user.id,)).fetchone()[0]
    daily_payment = global_cursor.execute(
        """SELECT daily_payment FROM credits WHERE userid = ?""", (user.id,)).fetchone()[0]

    if last_payment_time:
        if datetime.fromisoformat(last_payment_time) + timedelta(hours=24) <= datetime.now():
            await add_balance(-balance, user)
            if credit_amount - user_balance / 10 // 1 <= 0:
                await context.bot.send_message(chat_id=update.effective_chat.id,
                                               text=
f"""🩸 Коллекторы навестили @{user.username} и изъяли его накопления в уплату долга. Нужно было платить раньше.
Задолженность составляет еще {credit_amount - ((await get_balance(user) / 10) // 1)}.""")
                global_cursor.execute("""UPDATE credits SET current_credit = current_credit - ? WHERE userid = ?""",
                                      (await get_balance(user) / 10 // 1, user.id,))
                global_db.commit()
            else:
                await context.bot.send_message(chat_id=update.effective_chat.id,
                                               text=
f"""🩸 Коллекторы навестили @{user.username} и изъяли его накопления в уплату долга. Нужно было платить раньше.
Задолженность банку выплачена, кредит погашен.""")
                global_cursor.execute("""UPDATE credits SET current_credit = ? WHERE userid = ?""",
                                      (0, user.id,))
                global_cursor.execute("""UPDATE credits SET paid_today = ? WHERE userid = ?""",
                                      (0, user.id,))
                global_cursor.execute("""UPDATE credits SET last_payment_time = ? WHERE userid = ?""",
                                      (None, user.id,))
                global_db.commit()

            return
    elif paid_today < daily_payment:
        user_balance = await get_balance(user)
        await add_balance(-user_balance, user)

        if credit_amount - user_balance / 10 // 1 <= 0:
            global_cursor.execute("""UPDATE credits SET current_credit = ? WHERE userid = ?""",
                                  (0, user.id,))
            global_cursor.execute("""UPDATE credits SET paid_today = ? WHERE userid = ?""",
                                  (0, user.id,))
            global_cursor.execute("""UPDATE credits SET last_payment_time = ? WHERE userid = ?""",
                                  (None, user.id,))
            global_db.commit()
            await context.bot.send_message(chat_id=update.effective_chat.id,
                                           text=
f"""🩸 Коллекторы навестили @{user.username} и изъяли его накопления в уплату долга. Сегодня он заплатил слишком мало.
Задолженность банку выплачена, кредит погашен.""")

        else:
            global_cursor.execute("""UPDATE credits SET current_credit = current_credit - ? WHERE userid = ?""",
                                  (user_balance / 10 // 1 <= 0, user.id,))

            await context.bot.send_message(chat_id=update.effective_chat.id,
                                           text=
f"""🩸 Коллекторы навестили @{user.username} и изъяли его накопления в уплату долга. Сегодня он заплатил слишком мало.
Задолженность составляет еще {credit_amount - ((await get_balance(user) / 10) // 1)}.""")
        global_db.commit()
        return


async def pay_credit(update, context):
    user = update.effective_user
    credit_amount = global_cursor.execute(
        """SELECT current_credit FROM credits WHERE userid = ?""", (user.id,)).fetchone()[0]
    innate_credit = global_cursor.execute(
        """SELECT innate_credit FROM credits WHERE userid = ?""", (user.id,)).fetchone()[0]
    daily_payment = global_cursor.execute(
        """SELECT daily_payment FROM credits WHERE userid = ?""", (user.id,)).fetchone()[0]
    amount_to_pay = int(update.message.text.split()[2])
    user_balance = await get_balance(user)
    if amount_to_pay < credit_amount // 25:
        await update.message.reply_text(
            f"❌ Сумма, кооторую вы пытаетесь внести ({round((amount_to_pay / innate_credit * 100), 1)}% от "
            f"изначальной суммы кредита) слишком мала, минимальная сумма выплаты ─ {credit_amount // 25}.")
        return

    if await get_balance(user) < amount_to_pay:
        await update.message.reply_text("❌ Недостаточно средств.")
        return
    if amount_to_pay > credit_amount:
        amount_to_pay = credit_amount
    await add_balance(-amount_to_pay, user)

    global_cursor.execute("""UPDATE credits SET paid_today = paid_today + ? WHERE userid = ?""",
                          (amount_to_pay, user.id,))
    global_cursor.execute("""UPDATE credits SET current_credit = current_credit - ? WHERE userid = ?""",
                          (amount_to_pay, user.id,))
    global_cursor.execute("""UPDATE credits SET last_payment_time = ? WHERE userid = ?""",
                          (datetime.now().isoformat(), user.id,))
    global_db.commit()

    paid_today = global_cursor.execute(
        """SELECT paid_today FROM credits WHERE userid = ?""", (user.id,)).fetchone()[0]

    if amount_to_pay == credit_amount:
        global_cursor.execute("""UPDATE credits SET paid_today = ? WHERE userid = ?""",
                              (0, user.id,))
        global_cursor.execute("""UPDATE credits SET last_payment_time = ? WHERE userid = ?""",
                              (None, user.id,))

        await update.message.reply_text(f"""🎉 Кредит, составлявший {innate_credit} полностью выплачен!""")
        return
    if daily_payment - paid_today <= 0:
        await update.message.reply_text(
            f"✅ Очередные {round((amount_to_pay / credit_amount * 100), 1)}% кредита выплачены. Осталось еще "
            f"{credit_amount - amount_to_pay}. Ежедневный взнос уже выплачен.")
        return
    await update.message.reply_text(
        f"✅ Очередные {round((amount_to_pay / credit_amount * 100), 1)}% кредита выплачены. Осталось еще "
        f"{credit_amount - amount_to_pay}. За сегодня необходимо заплатить еще {daily_payment - amount_to_pay}.")


async def credit(update, context):
    user = update.effective_user
    await check_user(user)

    credit_amount = global_cursor.execute(
        """SELECT current_credit FROM credits WHERE userid = ?""", (user.id,)).fetchone()[0]
    last_time_paid = global_cursor.execute(
        """SELECT last_payment_time FROM credits WHERE userid = ?""", (user.id,)).fetchone()[0]
    paid_today = global_cursor.execute(
        """SELECT paid_today FROM credits WHERE userid = ?""", (user.id,)).fetchone()[0]
    daily_payment = global_cursor.execute(
        """SELECT daily_payment FROM credits WHERE userid = ?""", (user.id,)).fetchone()[0]
    innate_credit = global_cursor.execute(
        """SELECT innate_credit FROM credits WHERE userid = ?""", (user.id,)).fetchone()[0]
    credit_obtainment_time = global_cursor.execute(
        """SELECT credit_obtainment_time FROM credits WHERE userid = ?""", (user.id,)).fetchone()[0]

    if not credit_amount:
        await update.message.reply_text(f"❌ У вас нет задолженностей.",
                                        reply_markup=ReplyKeyboardRemove())
        return
    if context.user_data.get("last_time_checked_credit") and \
        context.user_data.get("last_time_checked_credit") + timedelta(hours=1) < datetime.now():
        await update.message.reply_text(f"⏳ Вы недавно пересматривали свой кредитный договор, подождите еще "
f"{(datetime.now - (context.user_data.get("last_time_checked_credit") + timedelta(hours=1))).seconds // 60} минут.",
                                        reply_markup=ReplyKeyboardRemove())
        return

    await update.message.reply_text(
f"""📑 Кредитный договор @{user.username}:

Изначальная сумма кредита: {innate_credit}.
Текущий кредит: {credit_amount}.
Требуемый минимальный ежедневный взнос: {daily_payment}.
Выплачено сегодня: {paid_today}.
Посленяя выплата задолженности: {get_last_payment_time(last_time_paid)}.

Чтобы выплатить задолженность: «Выплатить задолженность <Число>».

Кредит взят: {str(datetime.fromisoformat(credit_obtainment_time))[:19]}.

Все кредиты выдаются под ставку 30%.
Настоятельно рекомендуем выплачивать ежедневные взносы в срок во избежании проблем с федеральными службами.""")
    context.user_data["last_time_checked_credit"] = datetime.now()


def get_last_payment_time(last_payment_time):
    if not last_payment_time:
        return 'еще не платили'
    hours = (datetime.now() - datetime.fromisoformat(last_payment_time)).seconds / 3600
    if hours < 1:
        return 'менее часа назад'
    if 2 > hours >= 1:
        return '1 час назад'
    if 2 <= hours < 5:
        return f'{hours // 1} часа назад.'
    return f'{hours // 1} часов назад'


def calculate_daily_percent(credit_amount):
    normalized = (credit_amount - 50) / (10000 - 50)
    percent = 1 + math.sqrt(normalized) * 49

    return max(1.0, min(50.0, round(percent, 1)))


# Системные функции

async def table_recreation(update, context):  # для MessageHandler-а
    global admin_id
    logger.log(20, f'admin_id:{admin_id}')
    logger.log(20, f'eff.user_id:{update.effective_user.id}')
    if str(update.effective_user.id) != admin_id:
        await update.message.reply_text("❌ Недостаточно прав.")
        return
    await asyncio.sleep(0.5)
    await recreate_table_users()
    await recreate_table_credits()
    await update.message.reply_text("✅ Таблица пересоздана.")


async def new_members_handler(update, context):
    await check_chat(update, context)
    for user in update.message.new_chat_members:
        need_to_greet = global_cursor.execute("""SELECT show_member_greeting FROM chats WHERE chatid = ?""",
                                              (update.effective_chat.id,))
        greeting = get_greeting_text_for_sys(update.effective_chat.id)
        if not need_to_greet:
            return
        if user.id != context.bot.id:
            if not greeting:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
    text=f"Стальной шепот револьвера, щелчок курка... Тени смотрят вам в спину. Эта пуля - последняя милость, "
         f"что вам светит, {user.username}...")
            else:
                await context.bot.send_message(chat_id=update.effective_chat.id, text=greeting)
            await add_user(user.username, user.id)

        else:
            chat = update.effective_chat
            add_chat(update, context)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=(f"Чёрные плащи наполняют улицы... "
                      f"Приходит ночь. Этот город больше не ваш — он принадлежит Мафии, "
                      f"преступлений и обмана. Здесь правит лишь тишина. Выбирайте сторону — и кто знает, что будет, "
                      f"если вы встанете против <i>них</i>..."),
                parse_mode="HTML"
            )
            logger.log(f"Бота добавили в группу: {chat.title} (ID: {chat.id})")


async def get_user_by_username(username, context):
    try:
        clean_username = username.lstrip('@')
        '''
        except Exception as e:
            logger.info(f"Не удалось получить данные пользователя {clean_username} через API: {e}")

        return None'''

    except Exception as e:
        logger.error(f"Ошибка в get_user_by_username: {e}")
        return None


async def add_user_for_sys(update, context):
    username = update.message.text.split()[1]
    user = await get_user_by_username(username, context)
    if user:
        if check_user(user, update, context):
            await update.message.reply_text("⚠️ Пользователь уже присутствует в базе данных.")
            return
        await update.message.reply_text("✅ Пользователь добавлен в базу данных.")
        return
    await update.message.reply_text("❌ Не удалось добавить пользователя в базу данных.")
    return


async def messages(update, context):
    if update.message == None: return

    if update.message.dice and update.message.dice.emoji == '🎰':
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=update.message.id)


# Осн часть функций закончена

def main():
    application = Application.builder().token(BOT_TOKEN).build()



    joinPhandler = MessageHandler(filters.Regex(
r'(?i)(Вступить в игру|Вступить|Сесть за стол|Принять приглшение|/join_poker|/join_poker@MaximusX_bot)'),
                               join_poker)


    setup_poker_handlers(application)

    '''application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
            dummy_handler, # Явно указываем, что обработчик отсутствует
            restrict_chat
        )
    )'''

    dice_handler = ConversationHandler(
        entry_points=[CommandHandler("dice", dice_command),
                      MessageHandler(filters.Regex(r'(?i)^(🎲 Кубик|Кубик|Кости)$'), dice_command)],
        states={
            BET: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_bet)],
            GAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, dice)]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    slots_handler = ConversationHandler(
        entry_points=[CommandHandler("slots", slots_command),
                      MessageHandler(filters.Regex(r'(?i)^(🎰 Слоты|Слоты|Казино)$'), slots_command)],
        states={
            BET: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_bet)],
            GAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, slots)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    credit_handler = ConversationHandler(
        entry_points=[CommandHandler("getcredit", credit_command),
MessageHandler(filters.Regex(r'(?i)^(Взять кредит|Запросить кредит)'), credit_command),
MessageHandler(filters.Regex(r'(?i)^(Взять кредит|Запросить кредит)$'), credit_command)],
        states={
            # TAKING_CREDIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, take_credit)],
            REPLY_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, reply_message_credit)],
            WHY_CREDIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, why_credit)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    application.add_handler(CommandHandler("start", start))

    application.add_handler(MessageHandler(filters.Regex(r'(?i)^(Баланс|/balance|💰 Баланс|/balance@MaximusX_bot)$'),
                                           balance))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^(Баланс|/balance)'), others_balance))
    application.add_handler(MessageHandler(filters.Regex(r"(?i)^(💵 Работа|Работа|/work@MaximusX_bot|/work)$"),
                                           work))
    application.add_handler(MessageHandler(filters.Regex(r"(?i)^(Кредит|Мой кредит)$"), credit))
    application.add_handler(MessageHandler(filters.Regex(
        r"(?i)^(Выплатить задолженность|Внести оплату|Выплатить кредит)"),
        pay_credit))

    application.add_handler(dice_handler)
    application.add_handler(slots_handler)
    application.add_handler(credit_handler)

    application.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(r"(?i)^Пересоздать таблицу$") & ~filters.COMMAND, table_recreation))

    application.add_handler(MessageHandler(filters.Regex(
        r"(?i)^(Выписать счет|Выплатить счет|Выписать чек|Выплатить чек|/add_money)"), add_money))
    application.add_handler(MessageHandler(filters.Regex(
        r"(?i)^(Заплатить|Передать деньги|Выписать чек|/pay)"), transfer_money))

    # application.add_handler(MessageHandler(filters.Regex(r"(?i)^(Добавить пользователя|/add_user)"),
    #                                                              add_user_for_sys))

    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_members_handler))

    # application.add_handler(CallbackQueryHandler(handle_query))

    '''application.add_handler(MessageHandler(filters.Regex(r'^(Обновить базу данных|/bd_update)$'),
                                           add_new_group_members))'''

    application.add_handler(MessageHandler(filters.Regex(r'(?i)^(Приветствие|Показать приветствие|/get_greeting)$'),
                                           get_greeting_text))

    application.add_handler(MessageHandler(filters.Regex(r'(?i)^(Изменить приветствие|/change_greeting)'),
        set_greeting_text))

    application.add_handler(MessageHandler(
        filters.Regex(r'(?i)^(\+приветствие|/show_greeting)$'),
        change_greeting_to_show))

    application.add_handler(MessageHandler(
        filters.Regex(r'(?i)^(\-приветствие|/hide_greeting)$'),
        change_greeting_to_hide))
    
    # application.add_handler(MessageHandler(filters.TEXT, ...))

    application.add_handler(MessageHandler(filters.ALL, messages))

    application.run_polling()


if __name__ == "__main__":
    main()