import asyncio
import logging
from random import shuffle, choice
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, User
from telegram.ext import (ContextTypes, JobQueue, filters,
    CallbackQueryHandler, ConversationHandler, CommandHandler, MessageHandler)
from enum import Enum, auto
from typing import Dict, List, Tuple, Optional
from datetime import datetime, timezone
from SQLtables import *

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)


# Состояния игры
class GameState(Enum):
    WAITING = -1
    PREFLOP = 0
    FLOP = 1
    TURN = 2
    RIVER = 3
    SHOWDOWN = 4
    def next(state):
        states = list(GameState)

        try:
            index = states.index(state)
        except ValueError:
            raise ValueError(f"Invalid current state: {state}")

        if index == len(states) - 1:
            return state

        # Возвращаем следующее состояние
        return states[index + 1]

# Константы для ConversationHandler
POKER_START, POKER_JOIN, POKER_PLAY = range(3)

active_games = {}

# Карты
suits = ['♠️', '♥️', '♦️', '♣️']
ranks = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
card_values = {rank: i + 1 for i, rank in enumerate(ranks, 2)}


class Card:
    def __init__(self, suit, rank):
        self.suit = suit
        self.rank = rank
        self.value = int(card_values[rank])

    def __str__(self):
        return f"{self.suit}{self.rank}"

    def __repr__(self):
        return self.__str__()

    def __int__(self):
        return self.value


class PokerGame:
    def __init__(self, chat_id):
        self.chat_id = chat_id
        self.players: List[User] = []
        self.state = GameState.WAITING
        self.deck: List[Card] = []
        self.community_cards: List[Card] = []
        self.pot = 0
        self.side_pots = []
        self.current_bet = 0
        self.current_player_idx = 0
        self.small_blind = 10
        self.big_blind = 20
        self.started = False

        self.player_bets: Dict[User, int] = {}
        self.player_cards: Dict[User, Tuple[Card, Card]] = {}
        self.last_raiser: Optional[User] = None
        self.current_player: Optional[User] = None

        self.active_players: List[User] = []
        self.all_in_players: List[User] = []

        self.current_message: Optional[Update.message] = None  # айди сообщения с клавиатурой

        self.text = f"Банк: {self.pot}.\nТекущая ставка: {self.current_bet}."

    async def start_game(self):
        if len(self.players) < 2:
            return False

        self.state = GameState.PREFLOP
        self.deck = [Card(suit, rank) for suit in suits for rank in ranks]
        shuffle(self.deck)

        self.community_cards = []
        self.pot = 30
        self.current_bet = self.big_blind
        self.active_players = self.players.copy()
        self.street_players = self.players.copy()
        self.player_bets = {player: 0 for player in self.players}
        self.player_cards = {}
        self.all_in_players = []

        # Раздача карт
        for player in self.players:
            self.player_cards[player] = (self.deck.pop(), self.deck.pop())

        # Выбор дилера
        dealer = choice(self.players)
        dealer_idx = self.players.index(dealer)

        # Собираем блайнды
        small_blind_player = self.players[dealer_idx - 1]
        big_blind_player = self.players[dealer_idx - 2]

        self.player_bets[small_blind_player] = self.small_blind
        self.player_bets[big_blind_player] = self.big_blind
        await add_balance(-self.small_blind, small_blind_player)
        await add_balance(-self.small_blind, big_blind_player)

        return True

    def next_deal(self, context):
        if self.state == GameState.RIVER: self.showdown(context)
        elif self.state == GameState.TURN: self.deal_river()
        elif self.state == GameState.FLOP: self.deal_turn()
        elif self.state == GameState.PREFLOP: self.deal_flop()

    def deal_flop(self):
        if self.state != GameState.PREFLOP:
            return False
        self.deck.pop()
        self.community_cards.extend([self.deck.pop() for _ in range(3)])  # добавляет три карты к общим картам - ФЛОП
        self.state = GameState.FLOP
        self.current_bet = 0
        self.last_raiser = None
        return True

    def deal_turn(self):
        if self.state != GameState.FLOP:
            return False
        print(2)
        self.deck.pop()
        self.community_cards.append(self.deck.pop())  # добавляет одну карту к общим картам - ТЁРН
        self.state = GameState.TURN
        self.current_bet = 0
        self.last_raiser = None
        return True

    def deal_river(self):
        if self.state != GameState.TURN:
            return False

        self.deck.pop()
        self.community_cards.append(self.deck.pop())  # добавляет одну (последнюю) карту к общим картам - РИВЕР
        self.state = GameState.RIVER
        self.current_bet = 0
        self.last_raiser = None
        return True

    async def showdown(self, context):
        if self.state != GameState.RIVER:
            return False
        context.chat_data['showdown'] = True
        self.state = GameState.SHOWDOWN
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton('Фолд', callback_data='poker_fold')],
                                         [InlineKeyboardButton('Рейз', callback_data='poker_raise')]])
        msg = await context.bot.send_message(self.chat_id,
                                       f"<b>Раскрытие карт!</b> \n"
                                       f"У игроков остается лишь 15 секунд, чтобы сдаться, или поднять ставки "
                                       f"еще выше!",
                                       parse_mode="HTML", reply_markup=keyboard)
        await asyncio.sleep(1)
        for i in range(14, 0, -1):
            await context.bot.edit_message_text(chat_id=self.chat_id, message_id=msg.id,
                                                text=f"<b>Раскрытие карт!</b> \n"
                                       f"У игроков остается лишь {i} секунд, чтобы сдаться, или поднять ставки "
                                       f"еще выше!",
                                       parse_mode="HTML", reply_markup=keyboard)
            await asyncio.sleep(1)

        await context.bot.send_message(self.chat_id,
                                       f"<b>Время вышло, открываем карты!</b> \n", parse_mode="HTML", reply_markup=None)
        await  asyncio.sleep(1)
        for player_id, player in enumerate(self.active_players):
            cards_list = [str(card) for card in list(game.player_cards[player])]
            await context.bot.send_message(self.chat_id,
                                       f"Карты открывает @{self.active_players[player_id]}!\n"
                                       f"Его карты:\n"
                                       f"{'\n'.join(cards_list)}",
                                       parse_mode="HTML")
            value = await evaluate_hand(player)
            self.winner_cards_combinations_values.append(value)
            self.active_players.remove(player)
            await asyncio.sleep(2)

        logger.info(f'Игра в покере завершилась, победил(-и): {'; '.join(self.active_players)}')
        return True

    async def evaluate_hand(self, player):
        try:
            hole_cards = self.player_cards[player]
            all_cards = hole_cards + tuple(self.community_cards)
            values = sorted([c.value for c in all_cards], reverse=True)
            suits = [c.suit for c in all_cards]
            value_counts = {}
            for v in values:
                value_counts[v] = value_counts.get(v, 0) + 1
            sorted_counts = sorted(value_counts.values(), reverse=True)

            flush = False
            suit_counts = {}
            for s in suits:
                suit_counts[s] = suit_counts.get(s, 0) + 1
            if max(suit_counts.values()) >= 5:
                flush = True

            straight = False
            unique_values = sorted(list(set(values)), reverse=True)
            for i in range(len(unique_values) - 4):
                if unique_values[i] - unique_values[i + 4] == 4:
                    straight = True
                    break

            if flush and straight and 14 in unique_values and 10 in unique_values:
                return 10, "Роял-флеш"
            if flush and straight:
                return 9, "Стрит-флеш"
            if sorted_counts[0] == 4:
                return 8, "Каре"
            if sorted_counts[0] == 3 and sorted_counts[1] == 2:
                return 7, "Фулл-хаус"
            if flush:
                return 6, "Флеш"
            if straight:
                return 5, "Стрит"
            if sorted_counts[0] == 3:
                return 4, "Тройка"
            if len(sorted_counts) >= 2 and sorted_counts[0] == 2 and sorted_counts[1] == 2:
                return 3, "Две пары"
            if sorted_counts[0] == 2:
                return 2, "Пара"
            return 1, "Старшая карта"
        except Exception as e:
            # return 0, "Неизвестная комбинация"
            raise e

    async def player_action(self, user, action, context, chat_id, amount=None):
        try:
            balance = await get_balance(user)

            if action == "FOLD":
                self.active_players.remove(user)
                del self.player_bets[user]
                # await context.bot.send_message(chat_id, f"📪 @{user.username} сбросил карты!")
                await context.bot.edit_message_text(chat_id=chat_id, message_id=self.current_message.id,
                                                    text=f"📪 @{user.username} сбросил карты!")
                return True

            if action == "CHECK":
                if self.current_bet > self.player_bets[user]:
                    keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton("Мои карты", callback_data="poker_mycards")],
                        [InlineKeyboardButton("Фолд", callback_data="poker_fold"),
                         InlineKeyboardButton("Колл", callback_data="poker_call")],
                        [InlineKeyboardButton("Рейз", callback_data="poker_raise"),
                         InlineKeyboardButton("Ва-банк", callback_data="poker_allin")]])
                    await self.current_message.edit_message_text(
                        "❌ Нельзя сделать чек, принимайте или поднимайте ставку.", reply_markup=keyboard)
                    return False
                # await context.bot.send_message(chat_id, f"👌 @{user.username} cделал чек!")
                await context.bot.edit_message_text(chat_id=chat_id, message_id=self.current_message.id,
                                                    text=f"👌 @{user.username} cделал чек!")
                return True

            if action == "CALL":
                if amount > balance:
                    keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton("Мои карты", callback_data="poker_mycards")],
                        [InlineKeyboardButton("Фолд", callback_data="poker_fold"),
                         InlineKeyboardButton("Ва-банк", callback_data="poker_allin")]])
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=self.current_message.id,
                        text=
                            "❌ Недостаточно средств для колла, можете пойти ва-банк.", reply_markup=keyboard)
                    return False
                self.player_bets[user] = self.current_bet
                self.pot += amount
                await add_balance(-amount, user)
                # await context.bot.send_message(chat_id, f"⛓️ @{user.username} принял ставку!")
                await context.bot.edit_message_text(chat_id=chat_id, message_id=self.current_message.id,
                                                    text=f"⛓️ @{user.username} принял ставку!")
                return True

            if action == "BET":
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("Мои карты", callback_data="poker_mycards")],
                    [InlineKeyboardButton("Фолд", callback_data="poker_fold"),
                     InlineKeyboardButton("Чек", callback_data="poker_check")],
                    [InlineKeyboardButton("Бет", callback_data="poker_bet"),
                     InlineKeyboardButton("Ва-банк", callback_data="poker_allin")]])
                if amount > balance:
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=self.current_message.id,
                        text="❌ Недостаточно средств для ставки, можете пойти ва-банк или уменьшить ставку.",
                        reply_markup=keyboard)
                    return False
                if amount <= 0:
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=self.current_message.id,
                        text="❌ Ставка слишком мала.", reply_markup=keyboard)
                    return False
                self.current_bet += amount
                self.player_bets[user] += self.current_bet
                self.pot += amount
                await add_balance(-amount, user)
                # await context.bot.send_message(chat_id, f"⚖️ @{user.username} поставил {amount}!")
                await context.bot.edit_message_text(chat_id=chat_id, message_id=self.current_message.id,
                                                    text=f"⚖️ @{user.username} поставил {amount}!")
                return True

            if action == "RAISE":
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("Мои карты\u200B", callback_data="poker_mycards")],
                    [InlineKeyboardButton("Фолд\u200B", callback_data="poker_fold"),
                     InlineKeyboardButton("Колл\u200B", callback_data="poker_call")],
                    [InlineKeyboardButton("Рейз\u200B", callback_data="poker_raise"),
                     InlineKeyboardButton("Ва-банк\u200B", callback_data="poker_allin")]])
                total = amount - self.player_bets[user]
                if total > balance:
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=self.current_message.id,
                        text=
                            '❌ Недостаточно средств для повышения ставки. Можете пойти ва-банк или уменьшить сумму.'
                            '\u200B',
                        reply_markup=keyboard)
                    return False
                if total == balance:
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=self.current_message.id,
                        text=
                            "❌ Нельза поставить сумму, равную балансу. Можете пойти ва-банк или уменьшить сумму."
                            "\u200B",
                        reply_markup=keyboard)
                    return False
                self.player_bets[user] += total
                self.pot += total
                self.current_bet = amount
                self.last_raiser = user
                await add_balance(-total, user)
                # await context.bot.send_message(chat_id, f"⚖️ @{user.username} поднял ставку до {self.current_bet}!")
                await context.bot.edit_message_text(chat_id=chat_id, message_id=self.current_message.id,
                    text=f"⚖️ @{user.username} поднял ставку до {self.current_bet}!")
                return True

                # if total == balance:
                    # self.all_in_players.append(user)
                    # self.active_players.remove(user)
                    # + создание сайд пота для тех, кто продолжит игру...
                    # (all_in player может получить только те деньги, которые были в pot-e на момент его рейза,
                    #                                                                           ставшего Ва-банком)

            if action == "ALLIN":
                all_in_amount = balance

                self.player_bets[user] += all_in_amount
                self.pot += all_in_amount
                if self.player_bets[user] > self.current_bet:
                    self.current_bet = all_in_amount
                    self.last_raiser = user
                await context.bot.send_message(chat_id, f"🔥 @{user.username} идет ва-банк!")
                await add_balance(-all_in_amount, user)
                # self.all_in_players.append(user)
                # self.active_players.remove(user)
                # ^ создание сайд пота для тех, кто продолжит игру...
                # (all_in player может получить только те деньги, которые были в pot-e на момент его Ва-банка)
                return True

            return False
        except Exception as e:
            logger.error(f"Ошибка в player_action: {str(e)}")
            return False


'''def restrict_chat(update) -> bool:
    chat_id = update.effective_chat.id
    user = update.effective_user
    message_text = update.message.text if update.message else ""

    # Всегда разрешить команды присоединения и старта
    if message_text in ("/join_poker", "/start_poker"):
        return True

    # Остальная логика
    return (
            chat_id not in active_games
            or user in active_games[chat_id].players)'''


async def poker_start(update, context):
    try:
        chat_id = update.effective_chat.id
        user = update.effective_user
        time_before_start = 180
        inline_kb = [[InlineKeyboardButton(text="Присоединиться", callback_data="poker_join")]]

        # Создаем новую игру только если ее нет
        if chat_id not in active_games:
            active_games[chat_id] = PokerGame(chat_id)
            active_games[chat_id].players.append(user)
            await update.message.reply_text(
                f"🎴 Игра создана! Присоединяйтесь: /join_poker\n"
                f"Чтобы начать: /start_poker \n"
                f"Игра начнется автоматически через 3 минуты.",
                reply_markup=InlineKeyboardMarkup(inline_kb)
            )
            job = context.job_queue.run_once(lambda x: start_poker_game(update, context), time_before_start,
                                             chat_id=update.effective_chat.id)
            context.chat_data["poker_timer"] = job
            return POKER_JOIN
        else:
            job = context.chat_data.get("poker_timer")
            time_left = (int(round((job.next_t - datetime.now(timezone.utc)).total_seconds(), 0)))
            await update.message.reply_text("⚠️ Игра уже создана! Присоединяйтесь: /join_poker \n"
                                            f"До начала игры осталось "
                                            f"{time_left} секунд.")
            return POKER_JOIN

    except Exception as e:
        logger.error(f"Ошибка в PokerStart: {str(e)}")
        raise e
        return ConversationHandler.END


async def join_poker(update, context):
    try:
        chat_id = update.effective_chat.id
        user = update.effective_user
        balance = await get_balance(user)
        game = active_games[chat_id]

        if update.message:
            if chat_id not in active_games:
                await update.message.reply_text("❌ Игра не найдена! Создайте: /poker")
                return ConversationHandler.END

            if game.started:
                await update.message.reply_text("❌ Игра уже началась!")
                return ConversationHandler.END

        if balance <= 300:
            await update.message.reply_text("❌ Нельзя присоединиться к игре при балансе менее 300, "
                                            "попробуйте работу вместо азартных игр.")
            context.chat_data["pending_action"] = "nomoney"
            return ConversationHandler.END

        game = active_games[chat_id] if not game else game
        # Простая проверка без ограничений
        if user not in game.players:
            game.players.append(user)
            await context.bot.send_message(chat_id, text=f"✅ @{user.username} присоединился! Игроков: "
                                                         f"{len(game.players)}")
            return POKER_PLAY
        else:
            await update.message.reply_text("⚠️ Вы уже в игре!")
            if not user.id == game.players[0]:
                return POKER_PLAY

        return POKER_JOIN

    except Exception as e:
        logger.error(f"Ошибка в JoinPoker: {str(e)}")
        return ConversationHandler.END


async def start_poker_game(update, context):
    try:
        chat_id = update.effective_chat.id
        user = update.effective_user
        job = context.chat_data.get("poker_timer")

        if chat_id not in active_games or user != active_games[chat_id].players[0]:
            await context.bot.send_message(chat_id, "❌ Только создатель может начать игру!")
            return POKER_JOIN

        game = active_games[chat_id]
        if len(game.players) < 2:
            await context.bot.send_message(chat_id, "❌ Нужно минимум 2 игрока!")
            return POKER_JOIN

        if await game.start_game():
            ikb = [[InlineKeyboardButton(text="Мои карты", callback_data="poker_mycards")]]
            game.current_player = game.active_players[0]
            await context.bot.send_message(chat_id, "🎴 Игра началась! Раздаю карты...",
                                           reply_markup=InlineKeyboardMarkup(ikb))
            job.schedule_removal()
            game.started = True

            await show_player_turn(context, game)
            return POKER_PLAY

        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Ошибка в start_poker_game: {str(e)}")
        return ConversationHandler.END


async def show_player_turn(context, game):
    try:
        '''if game.current_message:
            await context.bot.delete_message(
                chat_id=game.chat_id,
                message_id=game.current_message.id
            )'''

        keyboard = await get_keyboard(game)

        if game.state == GameState.PREFLOP:
            msg = await context.bot.send_message(
                game.chat_id,
                f"Общие карты: на префлопе нет общих карт. \n\n"
                f"🎴 Ход @{game.current_player.username}:   Его ставка: {game.player_bets[game.current_player]}.\n"
                f"Текущая ставка: {game.current_bet} (по ней делается колл и рейз)."
                f"\nБанк: {game.pot}.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            cards_list = [str(card) for card in list(game.community_cards)]
            msg = await context.bot.send_message(
                game.chat_id,
                f"Общие карты:\n{'\n'.join(cards_list)}\n\n"
                f"🎴 Ход @{game.current_player.username}.\nЕго ставка: {game.player_bets[game.current_player]}. "
                f"Текущая ставка: {game.current_bet} (по ней делается колл)."
                f"\nБанк: {game.pot}.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        game.current_message = msg

    except Exception as e:
        # logger.error(f"Ошибка в show_player_turn: {str(e)}")
        raise e
        return ConversationHandler.END



async def get_keyboard(game):
    if game.state == GameState.PREFLOP and game.player_bets:
        if game.player_bets[game.current_player] >= game.current_bet:
            keyboard = [
                [InlineKeyboardButton("Мои карты", callback_data="poker_mycards")],
                [InlineKeyboardButton("Фолд", callback_data="poker_fold"),
                 InlineKeyboardButton("Чек", callback_data="poker_check")],
                [InlineKeyboardButton("Рейз", callback_data="poker_raise"),  # рейз доступен только поле бета
                 InlineKeyboardButton("Ва-банк", callback_data="poker_allin")]
            ]

        elif game.player_bets[game.current_player] < game.current_bet:
            keyboard = [
                [InlineKeyboardButton("Мои карты", callback_data="poker_mycards")],
                [InlineKeyboardButton("Фолд", callback_data="poker_fold"),
                 InlineKeyboardButton("Колл", callback_data="poker_call")],
                [InlineKeyboardButton("Рейз", callback_data="poker_raise"),
                 InlineKeyboardButton("Ва-банк", callback_data="poker_allin")]
            ]
    else:
        if game.player_bets[game.current_player] == game.current_bet == 0:
            keyboard = [
                [InlineKeyboardButton("Мои карты", callback_data="poker_mycards")],
                [InlineKeyboardButton("Фолд", callback_data="poker_fold"),
                 InlineKeyboardButton("Чек", callback_data="poker_check")],
                [InlineKeyboardButton("Бет", callback_data="poker_bet"),  # рейз доступен только поле бета
                 InlineKeyboardButton("Ва-банк", callback_data="poker_allin")]
            ]
        else:
            keyboard = [
                [InlineKeyboardButton("Мои карты", callback_data="poker_mycards")],
                [InlineKeyboardButton("Фолд", callback_data="poker_fold"),
                 InlineKeyboardButton("Колл", callback_data="poker_call")],
                [InlineKeyboardButton("Рейз", callback_data="poker_raise"),  # рейз доступен только поле бета
                 InlineKeyboardButton("Ва-банк", callback_data="poker_allin")]
            ]
    return keyboard


async def handle_poker_action(update, context):
    query = update.callback_query

    chat_id = update.effective_chat.id
    user = update.effective_user
    game = active_games.get(chat_id)

    if not game:
        await query.edit_message_text("❌ Игра не найдена!", reply_markup=None)
        return

    action = query.data.split('_')[1]
    logger.info(f'@{user.username} вызвал {action} в покере.')

    if action == 'join':
        if user in game.players:
            await query.answer("⚠️ Вы уже в игре!")
            return
        if await join_poker(update, context) == ConversationHandler.END:
            await query.answer("❌ Необходимо иметь минимум 300 на балансе.")
            return
        await query.answer("✅ Вы присоединилсь к игре.")
        return

    cards_list = [str(card) for card in list(game.player_cards[user])]
    if action == 'mycards':
        combo_data = await game.evaluate_hand(user)
        combo_power, combo_name = combo_data[0], combo_data[1]
        await query.answer(f"🃏 Ваши карты:\n"
                           f"{'\n'.join(cards_list)}\n\n"
                           f"Лучшая возможная комбинация: {combo_name}.\n"
                           f"Ее численное значение: {combo_power}.")
        return

    if user.id != game.current_player.id and not context.chat_data.get('showdown'):
        await query.answer(f"❌ Сейчас не ваш ход!")
        return

    try:  # Обрабатываем действие
        await query.answer()

        if action == "fold":
            await game.player_action(user, "FOLD", context, chat_id)
            if await next_turn(context, game, chat_id) == ConversationHandler.END:
                return ConversationHandler.END
            context.user_data['pending_action'] = 'FOLD'

        elif action == "check":
            await game.player_action(user, "CHECK", context, chat_id)
            await next_turn(context, game, chat_id)
            context.user_data['pending_action'] = 'CHECK'

        elif action == "call":
            call_amount = game.current_bet - game.player_bets.get(user, 0)
            if not await game.player_action(user, "CALL", context, chat_id, call_amount): return POKER_PLAY
            await next_turn(context, game, chat_id)
            context.user_data['pending_action'] = 'CALL'

        elif action == "bet":
            msg = context.chat_data.get("current_reply_msg")
            if msg: await context.bot.delete_message(
                chat_id=game.chat_id,
                message_id=msg.id)
            msg = await context.bot.send_message(
                chat_id=game.chat_id,
                text=f"@{user.username}, введите сумму, которую хотите поставить (Только число!) "
                     f"ответом на это сообщение.")
            context.chat_data["current_reply_msg"] = msg
            context.user_data['pending_action'] = 'BET'

        elif action == "raise":
            msg = context.chat_data.get("current_reply_msg")
            if msg: await context.bot.delete_message(
                    chat_id=game.chat_id,
                    message_id=msg.id)
            msg = await context.bot.send_message(
                chat_id=game.chat_id,
                text=f"@{user.username}, введите сумму, до которой хотите поднять ставку (Только число!) "
                     f"ответом на это сообщение.")
            context.chat_data["current_reply_msg"] = msg
            context.user_data['pending_action'] = 'RAISE'
        
        elif action == "allin":
            await game.player_action(user, "ALLIN", context, chat_id)
            await next_turn(context, game, chat_id)
            context.user_data['pending_action'] = 'ALLIN'
# ...

    except Exception as e:
        logger.exception(e)
        await query.edit_message_text("❌ Ошибка при обработке действия!")


async def process_bet(update, context):
    try:
        if not update.message.reply_to_message or not context.chat_data.get("current_reply_msg") or \
                int(update.message.reply_to_message.id) != int(context.chat_data.get("current_reply_msg").id):
            return POKER_PLAY

        chat_id = update.effective_chat.id
        user = update.effective_user
        game = active_games[chat_id]

        amount = int(update.message.text)

        action = context.user_data.get('pending_action')

        if amount < 100 and action == 'BET':
            await update.message.reply_text("❌ Минимальная сумма для ставки ─ 100.")
            return POKER_PLAY
        if amount - int(game.player_bets[user]) < 100 and action == 'RAISE':
            await update.message.reply_text("❌ Минимальная сумма для повышения ставки ─ 100.")
            return POKER_PLAY

        await game.player_action(user, action, context, chat_id, amount)

        await next_turn(context, game, chat_id)

        del context.user_data["pending_action"]
        del context.chat_data["current_reply_msg"]

        return POKER_PLAY

    except ValueError as e:
        raise e
        await update.message.reply_text("❌ Введите число!")
    except Exception as e:
        logger.error(f"Ошибка в process_bet: {str(e)}.")
        raise e


async def next_turn(context, game, chatid=None):
    try:
        if len(game.active_players) == 1:
            return await end_game(context, chatid)

        if context.chat_data.get("pending_action") == 'RAISE' and len(game.street_players) == 1:
            game.street_players = game.players.copy()
            next_player_idx = (game.players.index(game.current_player) + 1) % len(game.players)
            game.street_players.pop(game.street_players.index(game.current_player))
            game.current_player = game.players[next_player_idx]

            while game.current_player not in game.active_players:  # Если игрок уже сбросил карты - пропуск
                next_player_idx = (next_player_idx + 1) % len(game.players)
                game.street_players.pop(game.street_players.index(game.current_player))
                game.current_player = game.players[next_player_idx]

        if context.chat_data.get("pending_action") != 'RAISE' or len(game.street_players) >= 1:
            next_player_idx = (game.players.index(game.current_player) + 1) % len(game.players)
            game.street_players.pop(game.street_players.index(game.current_player))
            game.current_player = game.players[next_player_idx]

            while game.current_player not in game.active_players:  # Если игрок уже сбросил карты - пропуск
                next_player_idx = (next_player_idx + 1) % len(game.players)
                game.street_players.pop(game.street_players.index(game.current_player))
                game.current_player = game.players[next_player_idx]

        if len(game.street_players) <= 0 and context.chat_data.get("pending_action") != 'RAISE' and \
                game.state != GameState.RIVER:
            if game.state == GameState.PREFLOP: await context.bot.send_message(game.chat_id,
                text=f"Первая улица прошла, переходим на Флоп!")
            if game.state == GameState.FLOP: await context.bot.send_message(game.chat_id,
                text=f"Вторая улица прошла, переходим на Тёрн!")
            if game.state == GameState.TURN: await context.bot.send_message(game.chat_id,
                text=f"Третья улица прошла, переходим на Ривер!")
            game.next_deal(context)
            game.current_player = game.active_players[0]
            game.street_players = game.players.copy()
            for key in game.player_bets.keys():
                game.player_bets[key] = 0

        elif game.state == GameState.RIVER:
            await game.showdown(context)
            return await end_game(context, chatid)

        await show_player_turn(context, game)
        return POKER_PLAY

    except Exception as e:
        # logger.error(f"Ошибка в NextTurn: {str(e)}")
        raise e


async def end_game(context, chatid):
    try:
        chat_id = chatid
        game = active_games[chat_id]
        winners = game.active_players or game.all_in_players

        for winner in winners:
            prize = game.pot // len(winners)
            await add_balance(prize, winner)
            await context.bot.send_message(
                chat_id,
                f"🏆 Победитель @{winner.username}! Выигрыш: {prize}"
            )

        del active_games[chat_id]
        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Ошибка в end_game: {str(e)}")


async def cancel_poker(update, context):
    try:
        del active_games[update.effective_chat.id]
        await context.bot.send_message(update.effective_chat.id, "❌ Игра отменена!")
        return ConversationHandler.END
    except:
        return ConversationHandler.END


async def dummy_handler(update, context):
    """Пустой обработчик для игнорирования сообщений."""
    pass


def setup_poker_handlers(application):
    poker_handler = ConversationHandler(
        entry_points=[CommandHandler('poker', poker_start),
                      CommandHandler('join_poker', join_poker),
                      CallbackQueryHandler(handle_poker_action, pattern='^poker_')],
        states={
            POKER_JOIN: [
                CommandHandler('join_poker', join_poker),
                CommandHandler('start_poker', start_poker_game),
                CommandHandler('cancel', cancel_poker),
                CallbackQueryHandler(handle_poker_action, pattern='^poker_'),
            ],
            POKER_PLAY: [
                CallbackQueryHandler(handle_poker_action, pattern='^poker_'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_bet)
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel_poker)],
        allow_reentry=True
    )
    application.add_handler(poker_handler)
