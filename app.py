import time
import sqlite3
import json
import re
import random
import threading
import requests  # Заменили curl_cffi на обычный requests
from bs4 import BeautifulSoup
from telebot import TeleBot, types
from flask import Flask

# --- ВЕБ-ЗАТЫЧКА ---
app = Flask(__name__)
@app.route('/')
def index(): return "OK"

def run_flask():
    app.run(host='0.0.0.0', port=8000)

# --- БОТ ---
TOKEN = "8570991374:AAGOxulL0W679vZ6g4P0HhbAkqY14JxhhU8"
bot = TeleBot(TOKEN)

# База данных
conn = sqlite3.connect("monitor_bot.db", check_same_thread=False)
cur = conn.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS users (chat_id INTEGER PRIMARY KEY, url TEXT)")
cur.execute("CREATE TABLE IF NOT EXISTS ads (ad_id TEXT PRIMARY KEY)")
conn.commit()

def get_avito_data(url):
    # Облегченный запрос
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200: return None, []
        soup = BeautifulSoup(resp.text, 'html.parser')
        items = soup.find_all('div', {'data-marker': 'item'})
        return {}, items # Упростили сбор инфо для скорости
    except:
        return None, []

@bot.message_handler(commands=['start'])
def welcome(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("❌ Остановить мониторинг"))
    bot.reply_to(message, "Пришли ссылку с Авито.", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "❌ Остановить мониторинг")
def stop(message):
    cur.execute("DELETE FROM users WHERE chat_id = ?", (message.chat.id,))
    conn.commit()
    bot.send_message(message.chat.id, "⏹ Мониторинг остановлен.")

@bot.message_handler(func=lambda m: "avito.ru" in m.text)
def set_link(message):
    cur.execute("INSERT OR REPLACE INTO users (chat_id, url) VALUES (?, ?)", (message.chat.id, message.text.strip()))
    conn.commit()
    bot.send_message(message.chat.id, "✅ Ссылка принята!")

def check_updates():
    while True:
        try:
            cur.execute("SELECT chat_id, url FROM users")
            for chat_id, url in cur.fetchall():
                _, items = get_avito_data(url)
                for item in items[:5]: # Проверяем только первые 5 объявлений, чтобы не тормозить
                    ad_id = str(item.get('data-item-id'))
                    cur.execute("SELECT ad_id FROM ads WHERE ad_id = ?", (ad_id,))
                    if cur.fetchone() is None:
                        title = item.find('h3').text if item.find('h3') else "Товар"
                        bot.send_message(chat_id, f"🌟 Новое: {title}\nID: {ad_id}")
                        cur.execute("INSERT INTO ads (ad_id) VALUES (?)", (ad_id,))
                        conn.commit()
                time.sleep(10) # Большая пауза между пользователями
        except: pass
        time.sleep(5) # Проверка раз в 5 минут

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=check_updates, daemon=True).start()
    bot.polling(none_stop=True)

