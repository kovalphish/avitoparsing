import time
import sqlite3
import json
import re
import random
import threading
import requests
from bs4 import BeautifulSoup
from telebot import TeleBot, types
from flask import Flask

# --- 1. ВЕБ-ЗАТЫЧКА ДЛЯ KOYEB ---
app = Flask(__name__)
@app.route('/')
def index(): return "Бот в сети"

def run_flask():
    app.run(host='0.0.0.0', port=8000)

# --- 2. НАСТРОЙКИ БОТА ---
TOKEN = "8570991374:AAGOxulL0W679vZ6g4P0HhbAkqY14JxhhU8"
bot = TeleBot(TOKEN)

# База данных
conn = sqlite3.connect("monitor_bot.db", check_same_thread=False)
cur = conn.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS users (chat_id INTEGER PRIMARY KEY, url TEXT)")
cur.execute("CREATE TABLE IF NOT EXISTS ads (ad_id TEXT PRIMARY KEY)")
conn.commit()

# --- 3. ЛОГИКА ПАРСИНГА ---
def get_avito_data(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code == 429:
            print("⚠️ Авито: Слишком много запросов (429). Нужен отдых.")
            return []
        if resp.status_code != 200:
            return []
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        return soup.find_all('div', {'data-marker': 'item'})
    except:
        return []

# --- 4. МОНИТОРИНГ ---
def check_updates():
    while True:
        try:
            # Пингуем Flask
            try: requests.get("http://localhost:8000", timeout=5)
            except: pass

            cur.execute("SELECT chat_id, url FROM users")
            users = cur.fetchall()
            
            for chat_id, url in users:
                items = get_avito_data(url)
                if not items: continue

                for item in items[:5]:
                    ad_id = str(item.get('data-item-id'))
                    cur.execute("SELECT ad_id FROM ads WHERE ad_id = ?", (ad_id,))
                    if cur.fetchone() is None:
                        title_tag = item.find('a', {'data-marker': 'item-title'})
                        if title_tag:
                            title = title_tag.get('title', 'Объявление').split('купить')[0].strip()
                            link = "https://www.avito.ru" + title_tag['href']
                            price = item.find('p', {'data-marker': 'item-price'}).text if item.find('p', {'data-marker': 'item-price'}) else "Цена не указана"
                            
                            bot.send_message(chat_id, f"🌟 <b>{title}</b>\n💰 <b>{price}</b>\n\n🔗 <a href='{link}'>Открыть</a>", parse_mode="HTML")
                            cur.execute("INSERT INTO ads (ad_id) VALUES (?)", (ad_id,))
                            conn.commit()
                time.sleep(10) # Пауза между пользователями
        except: pass
        time.sleep(300) # Проверка раз в 5 минут

# --- 5. ОБРАБОТКА КОМАНД ---
@bot.message_handler(commands=['start'])
def welcome(message):
    bot.reply_to(message, "Пришли ссылку на поиск Авито.")

@bot.message_handler(func=lambda m: "avito.ru" in m.text)
def set_link(message):
    url = message.text.strip()
    cur.execute("INSERT OR REPLACE INTO users (chat_id, url) VALUES (?, ?)", (message.chat.id, url))
    conn.commit()
    bot.send_message(message.chat.id, "✅ Мониторинг запущен! Пришлю новые объявления, как только они появятся.")
    
    # Помечаем текущие как старые
    items = get_avito_data(url)
    for item in items:
        ad_id = str(item.get('data-item-id'))
        cur.execute("INSERT OR IGNORE INTO ads (ad_id) VALUES (?)", (ad_id,))
    conn.commit()

# --- 6. ЗАПУСК (С ЛЕЧЕНИЕМ ОШИБКИ 409) ---
if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    
    # Сброс зависших соединений
    try:
        bot.delete_webhook(drop_pending_updates=True)
        time.sleep(2)
    except: pass
        
    threading.Thread(target=check_updates, daemon=True).start()
    
    print("🚀 Бот запущен!")
    while True:
        try:
            bot.polling(none_stop=True, interval=2, timeout=20)
        except Exception as e:
            if "Conflict" in str(e):
                time.sleep(10)
            else:
                time.sleep(5)
