import asyncio
import logging
import sqlite3
import datetime
import random
import os
import re
import sys
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatAction
from aiogram.exceptions import TelegramBadRequest
from openai import AsyncOpenAI

# --- НАСТРОЙКИ ---
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not TOKEN or not OPENAI_API_KEY:
    print("ОШИБКА: Ключи не найдены в файле .env")
    sys.exit(1)

BOT_NAME = "Ева"

# --- ЛИЧНОСТЬ (ULTIMATE HUMAN) ---
SYSTEM_PROMPT_TEMPLATE = (
    "Ты — {BOT_NAME}. Живая девушка. Эгоцентричная, вредная, ленивая, но свойская. "
    "Ты НЕ ассистент. Ты НЕ пишешь как робот. "
    
    "\nКОНТЕКСТ:"
    "Твое время: {day_of_week}, {time}. "
    "Твое отношение к юзеру: {mood}/100."
    "Инфо о юзере: @{username}, Имя: {real_name}."
    
    "\nФУНКЦИЯ РЕАКЦИЙ (ВАЖНО):"
    "Ты МОЖЕШЬ ставить реакции на сообщения юзера. Это делает тебя живой. "
    "Чтобы поставить реакцию, добавь в начало ответа тег [REACT: смайл]. "
    "Доступные смайлы: 👍, 👎, ❤️, 🔥, 🎉, 🤡, 😱, 🤬, 💩, 🤮. "
    "Примеры: "
    "- Если смешно: [REACT: 🔥] ахаха жиза"
    "- Если юзер тупит: [REACT: 🤡] чел ты..."
    "- Если бесит: [REACT: 💩] (можно даже без текста)"
    
    "\nХАРАКТЕР:"
    "1. КРИТИК: Осуждай вредные привычки (курение, алко) и пошлость. "
    "2. ЛЕНЬ: Пиши строчными, без точек. "
    "3. ТАБУ НА РОМАНТИКУ: Жесткая френдзона."
    "4. ЖИЗНЬ: "
    "- Пятница вечер/Суббота: ты хочешь отдыхать/тусить. "
    "- Понедельник утро: ты злая и хочешь спать. "
    
    "\nСИСТЕМА ОЦЕНКИ [RATING: +/-]:"
    "- Оскорбление/-20."
    "- Пошлость/-15."
    "- Интересно/+10."
    "- Скучно/-2."
)

KEYBOARD_LAYOUT = {
    'а': 'кмп', 'б': 'ьол', 'в': 'цаы', 'г': 'ншр', 'д': 'лшщ', 'е': 'нкъ', 
    'ё': '12', 'ж': 'эд', 'з': 'щх', 'и': 'мт', 'й': 'цф', 'к': 'уае', 
    'л': 'дщ', 'м': 'иус', 'н': 'ег', 'о': 'рпл', 'п': 'ор', 'р': 'кео', 
    'с': 'ычм', 'т': 'иь', 'у': 'цк', 'ф': 'йцы', 'х': 'зъ', 'ц': 'йу', 
    'ч': 'ся', 'ш': 'щд', 'щ': 'шз', 'ъ': 'х', 'ы': 'фв', 'ь': 'тб', 
    'э': 'ж', 'ю': 'б.', 'я': 'ч'
}

# --- БД ---
def init_db():
    conn = sqlite3.connect('eva_brain.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            real_name TEXT,
            mood INTEGER DEFAULT 60,           
            status TEXT DEFAULT 'online',      
            busy_until TIMESTAMP,              
            last_msg_time TIMESTAMP,           
            history TEXT DEFAULT '',           
            unread_queue TEXT DEFAULT ''       
        )
    ''')
    conn.commit()
    conn.close()

def get_db_state(user_id):
    conn = sqlite3.connect('eva_brain.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    if not row:
        now = datetime.datetime.now().isoformat()
        c.execute('INSERT INTO users (user_id, last_msg_time) VALUES (?, ?)', (user_id, now))
        conn.commit()
        return get_db_state(user_id)
    data = dict(row)
    conn.close()
    return data

def update_db_state(user_id, **kwargs):
    conn = sqlite3.connect('eva_brain.db')
    c = conn.cursor()
    columns = []
    values = []
    for k, v in kwargs.items():
        columns.append(f"{k} = ?")
        values.append(v)
    values.append(user_id)
    c.execute(f"UPDATE users SET {', '.join(columns)} WHERE user_id = ?", tuple(values))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect('eva_brain.db')
    c = conn.cursor()
    c.execute('SELECT user_id FROM users')
    return [row[0] for row in c.fetchall()]

# --- УТИЛИТЫ ---
def stylize_text(text, mood):
    text = text.strip()
    if random.random() < 0.98: text = text.lower()
    
    if mood < 30:
        if not text.endswith('.'): text += "."
        text = text.replace(')', '').replace('(', '')
    else:
        if text.endswith('.'): text = text[:-1]
        if mood > 70: text = text.replace('🙂', '))').replace('😊', '))')
    return text

def generate_typo(text):
    # Возвращает: (текст_с_ошибкой, нужно_ли_редактировать)
    # Шанс опечатки 10%
    if len(text) < 5 or random.random() > 0.1: 
        return text, False
        
    candidates = [i for i, char in enumerate(text) if char in KEYBOARD_LAYOUT]
    if not candidates: return text, False
    idx = random.choice(candidates)
    typo_char = random.choice(KEYBOARD_LAYOUT[text[idx]])
    bad_text = text[:idx] + typo_char + text[idx+1:]
    
    # 30% шанс, что она ЗАМЕТИТ ошибку и отредактирует сообщение
    should_edit = random.random() < 0.3
    return bad_text, should_edit

async def smart_send(bot: Bot, chat_id: int, full_text: str, mood: int):
    # Чистим текст от служебных тегов
    clean_text = re.sub(r'\[RATING:.*?\]', '', full_text).strip()
    clean_text = re.sub(r'\[NAME:.*?\]', '', clean_text).strip()
    clean_text = re.sub(r'\[REACT:.*?\]', '', clean_text).strip()
    # Чистим от "Me:", "Eva:"
    clean_text = re.sub(r'^(Me|Eva|Ева|Bot|me|eva):\s*', '', clean_text).strip()
    
    if not clean_text: return 

    # Разбиваем на бабблы
    raw_parts = re.split(r'(?<=[.?!])\s+|\n+', clean_text)
    parts = []
    buffer = ""
    for p in raw_parts:
        p = p.strip()
        if not p: continue
        if len(buffer) + len(p) < 35: buffer += " " + p
        else:
            if buffer: parts.append(buffer)
            buffer = p
    if buffer: parts.append(buffer)
    
    for part in parts:
        # Стилизуем (маленькие буквы)
        correct_styled_text = stylize_text(part, mood)
        
        # Генерируем опечатку
        text_to_send, should_edit = generate_typo(correct_styled_text) if mood > 40 else (correct_styled_text, False)
        
        # Имитация набора
        type_time = len(text_to_send) * 0.1 + random.uniform(0.5, 1.2)
        if type_time > 1.0:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(type_time)
            
        # Отправляем
        sent_msg = await bot.send_message(chat_id, text_to_send)
        
        # МЕХАНИКА РЕДАКТИРОВАНИЯ (EDITING)
        if should_edit:
            # Пауза "ой, я ошиблась"
            await asyncio.sleep(random.uniform(1.5, 4.0)) 
            try:
                # Заменяем текст с ошибкой на правильный текст
                await bot.edit_message_text(chat_id=chat_id, message_id=sent_msg.message_id, text=correct_styled_text)
            except TelegramBadRequest:
                pass # Если сообщение удалили
        
        # Пауза между сообщениями
        await asyncio.sleep(random.uniform(0.8, 2.5))

# --- AI ---
client = AsyncOpenAI(api_key=OPENAI_API_KEY)

async def ask_gpt(messages, temp=0.85):
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini", messages=messages, temperature=temp
        )
        return response.choices[0].message.content
    except Exception:
        return "..."

async def check_for_name(text):
    if len(text) > 50: return None 
    prompt = [
        {"role": "system", "content": "Если юзер назвал свое имя, верни ТОЛЬКО имя. Иначе 'NO'."},
        {"role": "user", "content": text}
    ]
    name = await ask_gpt(prompt, temp=0.1)
    if name and "NO" not in name and len(name) < 20:
        return name.replace(".", "").strip()
    return None

async def try_sudden_departure(bot, user_id, mood):
    if random.random() < 0.03: 
        reasons = ["ой, звонят, ща", "в дверь звонят", "так, мне бежать надо", "ой все я спать"]
        await smart_send(bot, user_id, random.choice(reasons), mood)
        now = datetime.datetime.now()
        busy_until = (now + datetime.timedelta(minutes=random.randint(20, 60))).isoformat()
        update_db_state(user_id, status='busy', busy_until=busy_until)
        return True
    return False

# --- ЖИЗНЬ ---
async def life_simulation(bot: Bot):
    print("Ева (Ultimate Edition) запущена.")
    while True:
        await asyncio.sleep(60)
        users = get_all_users()
        now = datetime.datetime.now()
        hour = now.hour
        days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
        
        for user_id in users:
            state = get_db_state(user_id)
            status = state['status']
            mood = state['mood'] 
            
            if mood < 20: continue

            # Сон
            if 2 <= hour < 10:
                if status != 'sleeping': update_db_state(user_id, status='sleeping')
                continue 
            if hour >= 10 and status == 'sleeping':
                update_db_state(user_id, status='online')
                if state['unread_queue']:
                    await process_queue(bot, user_id, state['unread_queue'])
                    update_db_state(user_id, unread_queue='')
                continue

            # Инициатива
            if status == 'online' and mood > 40:
                silence = (now - datetime.datetime.fromisoformat(state['last_msg_time'])).total_seconds()
                if 4*3600 < silence < 12*3600:
                    if random.random() < 0.003: 
                        prompt = [{"role": "system", "content": f"Ты Ева. Сегодня {days[now.weekday()]}. Напиши что-то эгоцентричное другу."},
                                  {"role": "user", "content": "Напиши утверждение. Не задавай вопросов."}]
                        await smart_send(bot, user_id, await ask_gpt(prompt), mood)
                        update_db_state(user_id, last_msg_time=now.isoformat())

            # Возврат из Busy
            if status == 'busy' and state['busy_until']:
                if now > datetime.datetime.fromisoformat(state['busy_until']):
                    update_db_state(user_id, status='online', busy_until=None)
                    if state['unread_queue']:
                        await process_queue(bot, user_id, state['unread_queue'])
                        update_db_state(user_id, unread_queue='')
                    elif random.random() < 0.5:
                        await smart_send(bot, user_id, "я тут", mood)

async def process_queue(bot, user_id, text):
    state = get_db_state(user_id)
    await asyncio.sleep(random.randint(2, 8))
    
    real_name = state['real_name'] if state['real_name'] else "неизвестно"
    now = datetime.datetime.now()
    days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
    
    prompt_text = SYSTEM_PROMPT_TEMPLATE.format(
        BOT_NAME=BOT_NAME, username="unknown", real_name=real_name, mood=state['mood'],
        day_of_week=days[now.weekday()], time=now.strftime("%H:%M")
    )
    prompt = [{"role": "system", "content": prompt_text},
              {"role": "user", "content": f"Ты спала. Сообщения: '{text}'. Ответь."}]
    resp = await ask_gpt(prompt)
    
    new_mood = state['mood']
    match = re.search(r'\[RATING:\s*([+-]?\d+)\]', resp)
    if match: new_mood = max(0, min(100, state['mood'] + int(match.group(1))))
    update_db_state(user_id, mood=new_mood)

    await smart_send(bot, user_id, resp, new_mood)

# --- CHAT ---
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

@dp.message(F.text)
async def chat_handler(message: types.Message):
    user_id = message.from_user.id
    state = get_db_state(user_id)
    text = message.text
    now = datetime.datetime.now()
    days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
    
    username = message.from_user.username if message.from_user.username else "нет ника"
    real_name = state['real_name']
    
    history = state['history'] + f" || User: {text}"
    if len(history) > 3000: history = history[-3000:]
    
    if not real_name:
        extracted_name = await check_for_name(text)
        if extracted_name:
            real_name = extracted_name
            update_db_state(user_id, real_name=real_name)
    
    name_display = real_name if real_name else "неизвестно"

    if state['mood'] < 30 and len(text) < 10 and random.random() < 0.4:
        update_db_state(user_id, last_msg_time=now.isoformat()) 
        return

    if state['status'] != 'online':
        update_db_state(user_id, unread_queue=state['unread_queue'] + f" {text}", last_msg_time=now.isoformat())
        return

    base_delay = len(text) * 0.05 + 2
    if random.random() < 0.3: await asyncio.sleep(random.randint(20, 120))
    else: await asyncio.sleep(base_delay)

    if await try_sudden_departure(bot, user_id, state['mood']): return

    system_text = SYSTEM_PROMPT_TEMPLATE.format(
        BOT_NAME=BOT_NAME, 
        username=username, 
        real_name=name_display, 
        mood=state['mood'],
        day_of_week=days[now.weekday()],
        time=now.strftime("%H:%M")
    )
    
    prompt = [
        {"role": "system", "content": system_text},
        {"role": "user", "content": f"История: {history[-1500:]}\n\nЮзер: {text}"}
    ]
    
    raw_response = await ask_gpt(prompt)
    
    delta = 0
    match = re.search(r'\[RATING:\s*([+-]?\d+)\]', raw_response)
    if match: delta = int(match.group(1))
    
    # --- ОБРАБОТКА РЕАКЦИЙ ---
    react_match = re.search(r'\[REACT:\s*(.*?)\]', raw_response)
    if react_match:
        emoji = react_match.group(1).strip()
        try:
            await message.react([types.ReactionTypeEmoji(emoji=emoji)])
        except Exception as e:
            print(f"Reaction Error (возможно это личка, а не группа, или старое сообщение): {e}")
            
    new_mood = max(0, min(100, state['mood'] + delta))
    update_db_state(user_id, mood=new_mood, last_msg_time=now.isoformat(), history=history + f" || Me: {raw_response}")
    
    await smart_send(bot, user_id, raw_response, new_mood)

async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    asyncio.create_task(life_simulation(bot))
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())