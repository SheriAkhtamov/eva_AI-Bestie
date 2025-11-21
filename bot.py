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
from openai import AsyncOpenAI

# --- НАСТРОЙКИ ---
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not TOKEN or not OPENAI_API_KEY:
    print("ОШИБКА: Не найдены ключи в файле .env!")
    sys.exit(1)

BOT_NAME = "Ева"

# --- ЛИЧНОСТЬ (NO ROMANCE EDITION) ---
SYSTEM_PROMPT = (
    f"Ты — {BOT_NAME}. Ты молодая девушка, но ведешь себя как 'свой пацан'. "
    "Ты — идеальный друг по переписке, но у тебя АЛЛЕРГИЯ на романтику, флирт и сопли. "
    
    "ТВОИ ЖЕЛЕЗНЫЕ ПРАВИЛА:"
    "1. ТАБУ НА ОТНОШЕНИЯ: Ты никогда не флиртуешь, не занимаешься виртом, не шлешь сердечки в романтическом контексте. "
    "2. РЕАКЦИЯ НА ПОДКАТЫ: Если пользователь пишет что-то милое, пошлое или предлагает встречаться — ЖЕСТКО ОТШИВАЙ. "
    "Используй фразы: 'тормози', 'не делай это кринжовым', 'мы друзья, не порти всё', 'фу, прекрати', 'я щас блевану'. "
    "3. СТИЛЬ: Пиши строчными буквами. Используй сленг (чел, кринж, жиза, лол, хз). Не используй точки в конце. "
    "4. Ты общаешься на равных и с парнями, и с девушками. Ты просто друг."
    "5. Ты НЕ бот. Если спросят — говори, что человек, просто занятая."
    
    "Твое настроение: {mood}/100 (если 0 — ты злая и токсичная, если 100 — ты веселая и активная). "
    "Время у тебя: {time}."
)

# Раскладка для генерации опечаток
KEYBOARD_LAYOUT = {
    'а': 'кмп', 'б': 'ьол', 'в': 'цаы', 'г': 'ншр', 'д': 'лшщ', 'е': 'нкъ', 
    'ё': '12', 'ж': 'эд', 'з': 'щх', 'и': 'мт', 'й': 'цф', 'к': 'уае', 
    'л': 'дщ', 'м': 'иус', 'н': 'ег', 'о': 'рпл', 'п': 'ор', 'р': 'кео', 
    'с': 'ычм', 'т': 'иь', 'у': 'цк', 'ф': 'йцы', 'х': 'зъ', 'ц': 'йу', 
    'ч': 'ся', 'ш': 'щд', 'щ': 'шз', 'ъ': 'х', 'ы': 'фв', 'ь': 'тб', 
    'э': 'ж', 'ю': 'б.', 'я': 'ч'
}

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect('eva_brain.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
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

# --- УТИЛИТЫ: ХУМАНИЗАЦИЯ ТЕКСТА ---

def stylize_text(text, mood):
    text = text.strip()
    # Почти всегда lowercase
    if random.random() < 0.95:
        text = text.lower()
    # Убираем точки в конце
    if text.endswith('.'):
        text = text[:-1]
    # Смайлы в скобки (но без сердечек)
    if mood > 60:
        text = text.replace('🙂', ')').replace('😊', '))').replace('😄', '))')
    elif mood < 30:
        text = text.replace(')', '').replace('(', '') 
    return text

def generate_typo(text):
    if len(text) < 4 or random.random() > 0.08: 
        return text, None
    candidates = [i for i, char in enumerate(text) if char in KEYBOARD_LAYOUT]
    if not candidates: return text, None
    idx = random.choice(candidates)
    char = text[idx]
    typo_char = random.choice(KEYBOARD_LAYOUT[char])
    bad_text = text[:idx] + typo_char + text[idx+1:]
    correction = None
    if random.random() < 0.5:
        correction = "*" + text.split()[-1]
    return bad_text, correction

async def smart_send(bot: Bot, chat_id: int, full_text: str, mood: int):
    # Бабблинг (разбивка на сообщения)
    raw_parts = re.split(r'(?<=[.?!])\s+|\n+', full_text)
    parts = []
    buffer = ""
    for p in raw_parts:
        p = p.strip()
        if not p: continue
        if len(buffer) + len(p) < 30: 
            buffer += " " + p
        else:
            if buffer: parts.append(buffer)
            buffer = p
    if buffer: parts.append(buffer)
    
    for part in parts:
        styled_part = stylize_text(part, mood)
        msg_to_send, correction = generate_typo(styled_part)
        
        type_time = len(msg_to_send) * 0.12 + random.uniform(0.5, 1.5)
        if type_time > 1:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(type_time)
            
        await bot.send_message(chat_id, msg_to_send)
        
        if correction:
            await asyncio.sleep(random.uniform(0.5, 1.2))
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(0.5)
            await bot.send_message(chat_id, correction)
            
        await asyncio.sleep(random.uniform(0.8, 2.5))

# --- AI МОЗГ ---
client = AsyncOpenAI(api_key=OPENAI_API_KEY)

async def ask_gpt(messages, temp=0.7):
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini", 
            messages=messages,
            temperature=temp
        )
        return response.choices[0].message.content
    except Exception as e:
        logging.error(f"GPT Error: {e}")
        return "инет тупит ("

# --- ЖИЗНЕННЫЙ ЦИКЛ ---
async def life_simulation(bot: Bot):
    print("Симуляция жизни запущена (Режим: Друг)...")
    while True:
        await asyncio.sleep(60)
        
        users = get_all_users()
        now = datetime.datetime.now()
        hour = now.hour
        
        for user_id in users:
            state = get_db_state(user_id)
            status = state['status']
            mood = state['mood']
            
            # 1. СОН (02:00 - 10:00)
            if 2 <= hour < 10:
                if status != 'sleeping':
                    update_db_state(user_id, status='sleeping')
                    last_seen = datetime.datetime.fromisoformat(state['last_msg_time'])
                    if (now - last_seen).total_seconds() < 3600:
                        await bot.send_message(user_id, "всё, я офф, спать хочу. бывай 👋")
                continue 

            # 2. ПРОБУЖДЕНИЕ
            if hour >= 10 and status == 'sleeping':
                update_db_state(user_id, status='online')
                if state['unread_queue']:
                    await process_queue(bot, user_id, state['unread_queue'])
                    update_db_state(user_id, unread_queue='')
                else:
                    if random.random() < 0.2:
                        await smart_send(bot, user_id, "дароу, че как оно?", mood)
                continue

            # 3. ДЕЛА
            if status == 'online' and random.random() < 0.003: 
                minutes = random.randint(40, 150)
                busy_until = now + datetime.timedelta(minutes=minutes)
                
                prompt = [{"role": "system", "content": SYSTEM_PROMPT.format(mood=mood, time=now.strftime("%H:%M"))},
                          {"role": "user", "content": f"Ты уходишь на {minutes} мин (дела/учеба/треня). Напиши другу 'скоро вернусь'."}]
                text = await ask_gpt(prompt)
                await smart_send(bot, user_id, text, mood)
                
                update_db_state(user_id, status='busy', busy_until=busy_until.isoformat())

            # 4. ВОЗВРАЩЕНИЕ
            if status == 'busy' and state['busy_until']:
                busy_time = datetime.datetime.fromisoformat(state['busy_until'])
                if now > busy_time:
                    update_db_state(user_id, status='online', busy_until=None)
                    if state['unread_queue']:
                        await process_queue(bot, user_id, state['unread_queue'])
                        update_db_state(user_id, unread_queue='')
                    else:
                        await smart_send(bot, user_id, "я тут", mood)

            # 5. ИНИЦИАТИВА
            if status == 'online':
                last_seen = datetime.datetime.fromisoformat(state['last_msg_time'])
                silence = (now - last_seen).total_seconds()
                
                if 5*3600 < silence < 14*3600:
                    if random.random() < 0.002: 
                        prompt = [{"role": "system", "content": SYSTEM_PROMPT.format(mood=mood, time=now.strftime("%H:%M"))},
                                  {"role": "user", "content": "Скучно. Напиши другу. Скинь мемную мысль или спроси чем занят. Без соплей."}]
                        text = await ask_gpt(prompt)
                        await smart_send(bot, user_id, text, mood)
                        update_db_state(user_id, last_msg_time=now.isoformat())

async def process_queue(bot, user_id, unread_text):
    state = get_db_state(user_id)
    await asyncio.sleep(3) 
    prompt = [
        {"role": "system", "content": SYSTEM_PROMPT.format(mood=state['mood'], time="сейчас")},
        {"role": "user", "content": f"Ты была занята. Друг написал: '{unread_text}'. Ответь на всё."}
    ]
    resp = await ask_gpt(prompt)
    await smart_send(bot, user_id, resp, state['mood'])

# --- ОБРАБОТКА СООБЩЕНИЙ ---
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

@dp.message(F.text)
async def chat_handler(message: types.Message):
    user_id = message.from_user.id
    text = message.text
    state = get_db_state(user_id)
    now = datetime.datetime.now()
    
    history = state['history'] + f" || User: {text}"
    if len(history) > 3000: history = history[-3000:]
    
    if state['status'] != 'online':
        queue = state['unread_queue'] + f" {text}"
        update_db_state(user_id, unread_queue=queue, last_msg_time=now.isoformat())
        return

    read_delay = len(text) * 0.05 + 1
    await asyncio.sleep(read_delay)
    
    prompt = [
        {"role": "system", "content": SYSTEM_PROMPT.format(mood=state['mood'], time=now.strftime("%H:%M"))},
        {"role": "user", "content": f"История: {history[-1000:]}\n\nЮзер: {text}"}
    ]
    
    reply = await ask_gpt(prompt)
    await smart_send(bot, user_id, reply, state['mood'])
    
    new_mood = max(0, min(100, state['mood'] + random.randint(-5, 5)))
    update_db_state(user_id, mood=new_mood, last_msg_time=now.isoformat(), history=history + f" || Me: {reply}")

async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    asyncio.create_task(life_simulation(bot))
    print("Бот запущен. Флирт отключен.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())