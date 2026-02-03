import time
import sqlite3
import json
import re
import random
import urllib.parse
import threading
import os
from curl_cffi import requests
from bs4 import BeautifulSoup
from telebot import TeleBot, types
from flask import Flask

# --- ВЕБ-ЗАТЫЧКА ДЛЯ KOYEB ---
app = Flask(__name__)

@app.route('/')
def index():
    return "Бот работает!"

def run_flask():
    # Слушаем порт 8000, который требует Koyeb
    app.run(host='0.0.0.0', port=8000)

# --- НАСТРОЙКИ БОТА ---
TOKEN = "8570991374:AAGOxulL0W679vZ6g4P0HhbAkqY14JxhhU8"
bot = TeleBot(TOKEN)

def init_db():
    conn = sqlite3.connect("monitor_bot.db", check_same_thread=False)
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS users (chat_id INTEGER PRIMARY KEY, url TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS ads (ad_id TEXT PRIMARY KEY)")
    conn.commit()
    return conn, cur

db_conn, db_cur = init_db()

def main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("❌ Остановить мониторинг"))
    return markup

def get_avito_data(url):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        session = requests.Session()
        resp = session.get(url, headers=headers, impersonate="chrome110", timeout=20)
        if resp.status_code == 403: return None, []
        soup = BeautifulSoup(resp.text, 'html.parser')
        catalog_info = {}
        script = soup.find("script", string=re.compile("window.__initialData__"))
        if script:
            try:
                raw = script.string.split('window.__initialData__ = "')[1].split('";')[0]
                data = json.loads(urllib.parse.unquote(raw))
                for key in data:
                    if 'items' in data[key] and isinstance(data[key]['items'], list):
                        for it in data[key]['items']:
                            catalog_info[str(it.get('id'))] = {
                                'desc': it.get('description', '').replace('\n', ' ').strip(),
                                'img': it.get('images', [{}])[0].get('636x476')
                            }
            except: pass
        return catalog_info, soup.find_all('div', {'data-marker': 'item'})
    except: return None, []

def send_ad(chat_id, item, info):
    try:
        ad_id = str(item.get('data-item-id'))
        title_tag = item.find('a', {'data-marker': 'item-title'})
        if not title_tag: return
        title = title_tag.get('title', '').replace('купить в Челябинске на Авито', '').strip()
        try: price = item.find('meta', {'itemprop': 'price'}).get('content') + " ₽"
        except: price = "Цена не указана"
        link = "https://www.avito.ru" + title_tag['href']
        extra = info.get(ad_id, {})
        photo = extra.get('img')
        caption = f"<b>{title}</b>\n💰 <b>{price}</b>\n\n🔗 <a href='{link}'>Открыть</a>"
        if photo: bot.send_photo(chat_id, photo, caption=caption, parse_mode="HTML")
        else: bot.send_message(chat_id, caption, parse_mode="HTML")
    except: pass

@bot.message_handler(commands=['start'])
def welcome(message):
    bot.reply_to(message, "Пришли ссылку на Авито.", reply_markup=main_menu())

@bot.message_handler(func=lambda m: "avito.ru" in m.text)
def set_link(message):
    db_cur.execute("INSERT OR REPLACE INTO users (chat_id, url) VALUES (?, ?)", (message.chat.id, message.text.strip()))
    db_conn.commit()
    bot.send_message(message.chat.id, "✅ Мониторинг запущен!")

def check_updates():
    while True:
        try:
            # 1. Сначала пингуем сервер, чтобы он не заснул
            try: requests.get("http://localhost:8000", timeout=5)
            except: pass

            db_cur.execute("SELECT chat_id, url FROM users")
            users = db_cur.fetchall()
            
            for chat_id, url in users:
                # 2. Небольшая пауза ПЕРЕД каждым запросом, чтобы не грузить CPU
                time.sleep(2) 
                
                info, items = get_avito_data(url)
                if items:
                    for item in items:
                        ad_id = str(item.get('data-item-id'))
                        db_cur.execute("SELECT ad_id FROM ads WHERE ad_id = ?", (ad_id,))
                        if db_cur.fetchone() is None:
                            send_ad(chat_id, item, info)
                            db_cur.execute("INSERT INTO ads (ad_id) VALUES (?)", (ad_id,))
                            db_conn.commit()
                
                # Даем боту "подышать" после обработки одного пользователя
                time.sleep(5) 

        except Exception as e:
            print(f"Ошибка мониторинга: {e}")
        
        # 3. Увеличиваем общий отдых. 5-10 минут - это норма для бесплатного хостинга.
        time.sleep(random.randint(300, 600))

if __name__ == "__main__":
    # 1. Запуск веб-затычки для Koyeb
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # 2. Запуск мониторинга Авито в фоне
    monitor_thread = threading.Thread(target=check_updates, daemon=True)
    monitor_thread.start()
    
    print("🚀 Бот запущен и готов к работе!")

    # 3. Запуск самого бота с уменьшенным временем ожидания
    while True:
        try:
            # interval=0 делает бота более отзывчивым на команды
            # timeout=20 - оптимально для стабильной связи
            bot.polling(none_stop=True, interval=0, timeout=20)
        except Exception as e:
            print(f"⚠️ Ошибка связи: {e}")
            time.sleep(5)


