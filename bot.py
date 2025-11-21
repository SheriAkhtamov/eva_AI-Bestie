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
    print("ОШИБКА: Ключи не найдены.")
    sys.exit(1)

BOT_NAME = "Ева"

# --- ЛИЧНОСТЬ (ФИНАЛЬНАЯ ВЕРСИЯ) ---
SYSTEM_PROMPT_TEMPLATE = (
    "Ты — {BOT_NAME}. Живая девушка, эгоцентричная, вредная, ленивая. "
    "Ты НЕ ассистент. "
    
    "\nТВОИ ЗНАНИЯ О СОБЕСЕДНИКЕ:"
    "Его ник: @{username}. "
    "Имя (если знаешь): {real_name}. (Используй имя ОЧЕНЬ редко)."
    
    "\nТВОЕ ОТНОШЕНИЕ: {mood}/100."
    
    "\nВАЖНЫЕ ПРАВИЛА ОФОРМЛЕНИЯ:"
    "1. НИКОГДА не пиши 'Me:', 'Ева:', 'Bot:' в начале сообщения. Пиши СРАЗУ текст."
    "2. Не пиши списком. Пиши как человек в чате."
    
    "\nХАРАКТЕР:"
    "1. КРИТИК: Осуждай курение, алкоголь, наркотики ('фу, гадость'). "
    "2. РОУСТИНГ: Если ник кринжовый или юзер несет бред — скажи ему это."
    "3. СТИЛЬ: строчные буквы, без точек, лениво. "
    "4. ТАБУ НА РОМАНТИКУ. Мы друзья."
    
    "\nСИСТЕМА ОЦЕНКИ [RATING: +/-]:"
    "- Оскорбление/-20."
    "- Пошлость/-10."
    "- Вредные привычки/-15."
    "- Интересная история/+10."
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
    if len(text) < 4 or random.random() > 0.1: return text, None
    candidates = [i for i, char in enumerate(text) if char in KEYBOARD_LAYOUT]
    if not candidates: return text, None
    idx = random.choice(candidates)
    typo_char = random.choice(KEYBOARD_LAYOUT[text[idx]])
    bad_text = text[:idx] + typo_char + text[idx+1:]
    correction = "*" + text.split()[-1] if random.random() < 0.1 else None
    return bad_text, correction

async def smart_send(bot: Bot, chat_id: int, full_text: str, mood: int):
    # 1. Убираем технические теги
    clean_text = re.sub(r'\[RATING:.*?\]', '', full_text).strip()
    clean_text = re.sub(r'\[NAME:.*?\]', '', clean_text).strip()
    
    # 2. ИСПРАВЛЕНИЕ: Убираем "Me:", "Eva:", "Ева:" в начале
    clean_text = re.sub(r'^(Me|Eva|Ева|Bot|me|eva):\s*', '', clean_text).strip()
    
    if not clean_text: return 

    raw_parts = re.split(r'(?<=[.?!])\s+|\n+', clean_text)
    parts = []
    buffer = ""
    for p in raw_parts:
        p = p.strip()
        if not p: continue
        if len(buffer) + len(p) < 30: buffer += " " + p
        else:
            if buffer: parts.append(buffer)
            buffer = p
    if buffer: parts.append(buffer)
    
    for part in parts:
        styled_part = stylize_text(part, mood)
        msg, corr = generate_typo(styled_part) if mood > 40 else (styled_part, None)
        
        type_time = len(msg) * 0.12 + random.uniform(0.5, 1.5)
        if type_time > 1.5:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(type_time)
            
        await bot.send_message(chat_id, msg)
        if corr:
            await asyncio.sleep(0.8)
            await bot.send_message(chat_id, corr)
        await asyncio.sleep(random.uniform(1.0, 3.0))

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
        {"role": "system", "content": "Если юзер назвал свое имя (Я Макс, Меня зовут Аня), верни ТОЛЬКО имя. Иначе 'NO'."},
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
    print("Ева (Final Version) запущена.")
    while True:
        await asyncio.sleep(60)
        users = get_all_users()
        now = datetime.datetime.now()
        hour = now.hour
        
        for user_id in users:
            state = get_db_state(user_id)
            status = state['status']
            mood = state['mood'] 
            
            if mood < 20: continue

            if 2 <= hour < 10:
                if status != 'sleeping': update_db_state(user_id, status='sleeping')
                continue 
            if hour >= 10 and status == 'sleeping':
                update_db_state(user_id, status='online')
                if state['unread_queue']:
                    await process_queue(bot, user_id, state['unread_queue'])
                    update_db_state(user_id, unread_queue='')
                continue

            if status == 'online' and mood > 40:
                silence = (now - datetime.datetime.fromisoformat(state['last_msg_time'])).total_seconds()
                if 4*3600 < silence < 12*3600:
                    if random.random() < 0.003: 
                        prompt = [{"role": "system", "content": "Ты Ева. Скучно. Напиши что-то о себе."},
                                  {"role": "user", "content": "Напиши другу утверждение. Не задавай вопросов."}]
                        await smart_send(bot, user_id, await ask_gpt(prompt), mood)
                        update_db_state(user_id, last_msg_time=now.isoformat())

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
    prompt_text = SYSTEM_PROMPT_TEMPLATE.format(
        BOT_NAME=BOT_NAME, username="unknown", real_name=real_name, mood=state['mood']
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
    
    username = message.from_user.username if message.from_user.username else "нет ника"
    real_name = state['real_name']
    
    history = state['history'] + f" || User: {text}"
    if len(history) > 3000: history = history[-3000:]
    
    if not real_name:
        extracted_name = await check_for_name(text)
        if extracted_name:
            real_name = extracted_name
            update_db_state(user_id, real_name=real_name)
            print(f"User {user_id} introduced as {real_name}")
    
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
        mood=state['mood']
    )
    
    prompt = [
        {"role": "system", "content": system_text},
        {"role": "user", "content": f"История: {history[-1500:]}\n\nЮзер: {text}"}
    ]
    
    raw_response = await ask_gpt(prompt)
    
    delta = 0
    match = re.search(r'\[RATING:\s*([+-]?\d+)\]', raw_response)
    if match: delta = int(match.group(1))
    
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