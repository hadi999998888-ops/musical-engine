import asyncio
import logging
import os
import re
import sqlite3
from html import escape
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    MessageEntity
)

# ========================= НАСТРОЙКИ =========================
# Вставь токен от @BotFather между кавычками.
BOT_TOKEN = "9015705095:AAE64m-zwxXcBYr-zz7b0dOlcMW6YBJ3mQI"
ADMIN_ID = 7608889836

LOOP_CHANNEL = "@LoopHack"
LOOP_URL = "https://t.me/LoopHack"
ANGEL_CHANNEL = "@smertnuy_angelok"
ANGEL_URL = "https://t.me/smertnuy_angelok"
MANAGER_TEXT = "Напиши свой вопрос одним сообщением. Администратор ответит тебе здесь."

DB_PATH = "loop_hack_bot.sqlite3"
# =============================================================

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("loop-hack-bot")

# Telegram custom emoji IDs supplied by the user.
EMOJI = {
    "chat": "5906476035192396092",
    "phone": "5906991701850856372",
    "check": "5906840875484321836",
    "wait": "5906598824012420908",
    "announce": "5906797358875681525",
    "rose": "5287348867317125082",
    "folder": "5906839307821259375",
}

def ce(key: str, fallback: str) -> str:
    return f'<tg-emoji emoji-id="{EMOJI[key]}">{fallback}</tg-emoji>'

def db():
    return sqlite3.connect(DB_PATH)

def init_db():
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT)""")
        con.execute("""CREATE TABLE IF NOT EXISTS channels (
            channel TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT NOT NULL)""")
        con.execute("""CREATE TABLE IF NOT EXISTS posts (
            post_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_chat INTEGER NOT NULL, source_message INTEGER NOT NULL,
            description TEXT NOT NULL)""")
        con.execute("""CREATE TABLE IF NOT EXISTS support (
            admin_message_id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL)""")
        con.execute("""CREATE TABLE IF NOT EXISTS pending (
            user_id INTEGER PRIMARY KEY, post_id INTEGER NOT NULL)""")
        con.execute("INSERT OR IGNORE INTO channels(channel,url,title) VALUES(?,?,?)",
                    (LOOP_CHANNEL, LOOP_URL, "LOOP HACK"))
        con.execute("INSERT OR IGNORE INTO channels(channel,url,title) VALUES(?,?,?)",
                    (ANGEL_CHANNEL, ANGEL_URL, "ANGEL KFG"))

def save_user(message: Message):
    with db() as con:
        con.execute("INSERT OR REPLACE INTO users(user_id,username,first_name) VALUES(?,?,?)",
                    (message.from_user.id, message.from_user.username, message.from_user.first_name))

def get_channels():
    with db() as con:
        return con.execute("SELECT channel,url,title FROM channels ORDER BY rowid").fetchall()

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📞 Менеджер", callback_data="manager")],
        [InlineKeyboardButton(text="📢 LOOP HACK", url=LOOP_URL)],
    ])

def subscribe_menu():
    rows = []
    for channel, url, title in get_channels():
        rows.append([InlineKeyboardButton(text=f"📢 {title}", url=url)])
    rows.append([InlineKeyboardButton(text="✅ Проверить", callback_data="check_subs")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌹 РАССЫЛКА", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🌹 СОЗДАТЬ ПОСТ", callback_data="admin_post")],
        [InlineKeyboardButton(text="🌹 ДОБАВИТЬ ПОДПИСКИ", callback_data="admin_channels")],
    ])

class AdminFlow(StatesGroup):
    post_material = State()
    post_description = State()
    broadcast_material = State()
    add_channel = State()
    add_channel_url = State()
    remove_channel = State()

class UserFlow(StatesGroup):
    manager_question = State()

dp = Dispatcher(storage=MemoryStorage())
bot: Optional[Bot] = None

async def membership_ok(user_id: int) -> tuple[bool, list[str]]:
    missing = []
    for channel, _url, title in get_channels():
        try:
            member = await bot.get_chat_member(channel, user_id)
            if member.status not in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR,
                                     ChatMemberStatus.CREATOR):
                missing.append(title)
        except (TelegramBadRequest, TelegramForbiddenError) as e:
            log.warning("Не удалось проверить канал %s: %s", channel, e)
            missing.append(title)
    return not missing, missing

async def show_gate(target: Message | CallbackQuery, pending_post: Optional[int] = None):
    text = (
        f'{ce("chat","💬")} <b>Подпишись на нужные каналы</b>\n\n'
        "После подписки нажми кнопку проверки."
    )
    if isinstance(target, Message):
        await target.answer(text, reply_markup=subscribe_menu())
    else:
        await target.message.edit_text(text, reply_markup=subscribe_menu())

async def send_main_menu(message: Message):
    text = (
        f'{ce("chat","💬")} <b>Добро пожаловать в главное меню</b>\n'
        f'{ce("rose","🌹")} <b>LOOP HACK</b>\n\n'
        f'{ce("folder","📁")} Получай нужные файлы.\n'
        "Перейди в наш канал — там будут все софты."
    )
    await message.answer(text, reply_markup=main_menu())

async def deliver_post(user_id: int, post_id: int):
    with db() as con:
        row = con.execute(
            "SELECT source_chat,source_message,description FROM posts WHERE post_id=?",
            (post_id,)
        ).fetchone()
    if not row:
        await bot.send_message(user_id, "Этот материал больше недоступен.")
        return
    source_chat, source_message, description = row
    try:
        await bot.copy_message(user_id, source_chat, source_message)
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 LOOP HACK", url=LOOP_URL)]
        ])
        await bot.send_message(
            user_id,
            f'{ce("folder","📁")} <b>Описание материала</b>\n{escape(description)}',
            reply_markup=markup
        )
    except TelegramForbiddenError:
        log.info("Пользователь %s заблокировал бота", user_id)
    except TelegramBadRequest as e:
        log.exception("Не удалось отправить пост %s пользователю %s: %s", post_id, user_id, e)

@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    save_user(message)
    payload = (message.text or "").partition(" ")[2].strip()
    post_id = None
    if payload.startswith("p") and payload[1:].isdigit():
        post_id = int(payload[1:])
        with db() as con:
            con.execute("INSERT OR REPLACE INTO pending(user_id,post_id) VALUES(?,?)",
                        (message.from_user.id, post_id))
    ok, _ = await membership_ok(message.from_user.id)
    if not ok:
        await show_gate(message)
        return
    if post_id:
        await deliver_post(message.from_user.id, post_id)
        with db() as con:
            con.execute("DELETE FROM pending WHERE user_id=?", (message.from_user.id,))
    else:
        await send_main_menu(message)
    if is_admin(message.from_user.id):
        await message.answer("Панель администратора: /admin")

@dp.callback_query(F.data == "check_subs")
async def check_subs(callback: CallbackQuery):
    ok, missing = await membership_ok(callback.from_user.id)
    if not ok:
        await callback.answer("Подписка пока не найдена. Подпишись и проверь ещё раз.", show_alert=True)
        return
    await callback.answer("Подписка подтверждена!")
    with db() as con:
        row = con.execute("SELECT post_id FROM pending WHERE user_id=?",
                          (callback.from_user.id,)).fetchone()
        con.execute("DELETE FROM pending WHERE user_id=?", (callback.from_user.id,))
    await callback.message.delete()
    if row:
        await deliver_post(callback.from_user.id, row[0])
    else:
        await send_main_menu(callback.message)

@dp.message(Command("admin"))
async def admin(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer(
        f'{ce("chat","💬")} <b>Добро пожаловать в админку</b>\nВыбери, что ты хочешь:',
        reply_markup=admin_menu()
    )

@dp.callback_query(F.data == "manager")
async def manager(callback: CallbackQuery, state: FSMContext):
    ok, _ = await membership_ok(callback.from_user.id)
    if not ok:
        await callback.answer("Сначала подпишись на каналы.", show_alert=True)
        await show_gate(callback)
        return
    await state.set_state(UserFlow.manager_question)
    await callback.message.answer(MANAGER_TEXT)
    await callback.answer()

@dp.message(UserFlow.manager_question)
async def manager_question(message: Message, state: FSMContext):
    if message.from_user.id == ADMIN_ID:
        return
    save_user(message)
    try:
        forwarded = await bot.copy_message(ADMIN_ID, message.chat.id, message.message_id)
        with db() as con:
            con.execute("INSERT OR REPLACE INTO support(admin_message_id,user_id) VALUES(?,?)",
                        (forwarded.message_id, message.from_user.id))
        await message.answer("Вопрос отправлен администратору. Ответ придёт сюда.")
    except TelegramForbiddenError:
        await message.answer("Сейчас не удалось доставить сообщение менеджеру. Попробуй позже.")
    await state.clear()

@dp.message(F.from_user.id == ADMIN_ID, F.reply_to_message)
async def admin_reply(message: Message):
    with db() as con:
        row = con.execute("SELECT user_id FROM support WHERE admin_message_id=?",
                          (message.reply_to_message.message_id,)).fetchone()
    if not row:
        return
    user_id = row[0]
    try:
        await bot.copy_message(user_id, message.chat.id, message.message_id)
        await message.reply("Ответ отправлен.")
    except TelegramForbiddenError:
        await message.reply("Пользователь не может получить сообщение от бота.")

@dp.callback_query(F.data == "admin_post")
async def create_post(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(AdminFlow.post_material)
    await callback.message.answer(f'{ce("rose","🌹")} Пришли материал любого формата одним сообщением.')
    await callback.answer()

@dp.message(AdminFlow.post_material, F.from_user.id == ADMIN_ID)
async def receive_material(message: Message, state: FSMContext):
    await state.update_data(source_chat=message.chat.id, source_message=message.message_id)
    await state.set_state(AdminFlow.post_description)
    await message.answer("Теперь пришли описание материала.")

@dp.message(AdminFlow.post_description, F.from_user.id == ADMIN_ID)
async def receive_description(message: Message, state: FSMContext):
    description = message.text or message.caption
    if not description:
        await message.answer("Описание должно быть текстом. Пришли описание ещё раз.")
        return
    data = await state.get_data()
    with db() as con:
        cur = con.execute(
            "INSERT INTO posts(source_chat,source_message,description) VALUES(?,?,?)",
            (data["source_chat"], data["source_message"], description)
        )
        post_id = cur.lastrowid
    await state.clear()
    await message.answer(f'{ce("wait","⏳")} <b>Создаю ссылку...</b>')
    await asyncio.sleep(1)
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=p{post_id}"
    await message.answer(
        f'{ce("wait","⏳")} <b>Готово!</b>\n\nСсылка на материал:\n<code>{link}</code>',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 LOOP HACK", url=LOOP_URL)]
        ])
    )

@dp.callback_query(F.data == "admin_broadcast")
async def broadcast_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(AdminFlow.broadcast_material)
    await callback.message.answer("Пришли одним сообщением материал для рассылки (текст, фото, видео, файл и т.д.).")
    await callback.answer()

@dp.message(AdminFlow.broadcast_material, F.from_user.id == ADMIN_ID)
async def broadcast_send(message: Message, state: FSMContext):
    await state.clear()
    with db() as con:
        users = [r[0] for r in con.execute("SELECT user_id FROM users").fetchall()]
    await message.answer(f'{ce("wait","⏳")} Начинаю рассылку: {len(users)} получателей.')
    sent = 0
    failed = 0
    for user_id in users:
        try:
            await bot.copy_message(user_id, message.chat.id, message.message_id)
            sent += 1
            await asyncio.sleep(0.05)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            try:
                await bot.copy_message(user_id, message.chat.id, message.message_id)
                sent += 1
            except Exception:
                failed += 1
        except (TelegramForbiddenError, TelegramBadRequest):
            failed += 1
        except Exception:
            failed += 1
            log.exception("Broadcast error for %s", user_id)
    await message.answer(f"Рассылка завершена.\nУспешно: {sent}\nНе доставлено: {failed}")

@dp.callback_query(F.data == "admin_channels")
async def channels_panel(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    rows = [
        [InlineKeyboardButton(text="➕ Добавить канал", callback_data="channel_add")],
        [InlineKeyboardButton(text="➖ Удалить канал", callback_data="channel_remove")],
        [InlineKeyboardButton(text="⬅️ В админку", callback_data="admin_back")]
    ]
    await callback.message.answer("Управление обязательными подписками:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()

@dp.callback_query(F.data == "admin_back")
async def admin_back(callback: CallbackQuery):
    if is_admin(callback.from_user.id):
        await callback.message.answer("Админ-панель:", reply_markup=admin_menu())
    await callback.answer()

@dp.callback_query(F.data == "channel_add")
async def channel_add(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(AdminFlow.add_channel)
    await callback.message.answer("Пришли @username канала или его числовой ID (например, -100...).")
    await callback.answer()

@dp.message(AdminFlow.add_channel, F.from_user.id == ADMIN_ID)
async def channel_add_id(message: Message, state: FSMContext):
    channel = (message.text or "").strip()
    if not channel or not (channel.startswith("@") or re.fullmatch(r"-100\d+", channel)):
        await message.answer("Неверный формат. Пришли @username или ID вида -100...")
        return
    await state.update_data(channel=channel)
    await state.set_state(AdminFlow.add_channel_url)
    await message.answer("Теперь пришли ссылку-приглашение/ссылку на канал (https://t.me/...).")

@dp.message(AdminFlow.add_channel_url, F.from_user.id == ADMIN_ID)
async def channel_add_url(message: Message, state: FSMContext):
    url = (message.text or "").strip()
    if not url.startswith("https://t.me/"):
        await message.answer("Нужна ссылка вида https://t.me/...")
        return
    data = await state.get_data()
    channel = data["channel"]
    try:
        chat = await bot.get_chat(channel)
        title = chat.title or channel
        # Verify the bot can check members; bot must be an administrator in the channel.
        me = await bot.get_me()
        bot_member = await bot.get_chat_member(chat.id, me.id)
        if bot_member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
            raise RuntimeError("Сначала назначь бота администратором этого канала.")
        with db() as con:
            con.execute("INSERT OR REPLACE INTO channels(channel,url,title) VALUES(?,?,?)",
                        (str(chat.id), url, title))
        await message.answer(f"Канал «{escape(title)}» добавлен.")
    except Exception as e:
        await message.answer(
            "Не получилось добавить канал. Проверь, что бот добавлен в канал администратором, "
            "а ID/@username указан правильно.\n"
            f"Техническая причина: {escape(str(e)[:300])}"
        )
    await state.clear()

@dp.callback_query(F.data == "channel_remove")
async def channel_remove(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    channels = get_channels()
    rows = []
    for channel, _url, title in channels:
        if channel == LOOP_CHANNEL:
            continue  # LOOP HACK is always required and cannot be removed.
        rows.append([InlineKeyboardButton(text=f"❌ {title}", callback_data=f"rmch:{channel}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")])
    await callback.message.answer("Выбери канал для удаления:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()

@dp.callback_query(F.data.startswith("rmch:"))
async def remove_channel(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    channel = callback.data.split(":", 1)[1]
    if channel == LOOP_CHANNEL:
        await callback.answer("LOOP HACK удалить нельзя.", show_alert=True)
        return
    with db() as con:
        con.execute("DELETE FROM channels WHERE channel=?", (channel,))
    await callback.answer("Канал удалён.")
    await callback.message.answer("Список обязательных подписок обновлён.")

@dp.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Текущее действие отменено.")

async def main():
    global bot
    if not BOT_TOKEN or BOT_TOKEN == "ВСТАВЬ_СЮДА_ТОКЕН":
        raise RuntimeError("Вставь токен бота в переменную BOT_TOKEN в начале файла.")
    init_db()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
