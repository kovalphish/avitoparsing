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
# Нужна, чтобы бесплатный тариф не отключал бота из-за порта 8000
app = Flask(__name__)

@app.route('/')
def index():
    return "Бот-мониторинг активен!"

def run_flask():
    app.run(host='0.0.0.0', port=8000)

# --- 2. НАСТРОЙКИ БОТА ---
TOKEN = "8570991374:AAGOxulL0W679vZ6g4P0HhbAkqY14JxhhU8"
bot = TeleBot(TOKEN)

# Инициализация базы данных
conn = sqlite3.connect("monitor_bot.db", check_same_thread=False)
cur = conn.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS users (chat_id INTEGER PRIMARY KEY, url TEXT)")
cur.execute("CREATE TABLE IF NOT EXISTS ads (ad_id TEXT PRIMARY KEY)")
conn.commit()

# --- 3. ЛОГИКА ПАРСИНГА ---
def get_avito_data(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
    }
    try:
        # Используем обычный requests для экономии памяти на Koyeb
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code != 200:
            print(f"⚠️ Ошибка Авито: {resp.status_code}")
            return []
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        items = soup.find_all('div', {'data-marker': 'item'})
        print(f"🔎 Найдено объявлений на странице: {len(items)}")
        return items
    except Exception as e:
        print(f"❌ Ошибка запроса: {e}")
        return []

# --- 4. ФУНКЦИЯ МОНИТОРИНГА ---
def check_updates():
    while True:
        try:
            # Пингуем сами себя, чтобы Koyeb не "усыплял" процесс
            try: requests.get("http://localhost:8000", timeout=5)
            except: pass

            cur.execute("SELECT chat_id, url FROM users")
            users = cur.fetchall()
            
            for chat_id, url in users:
                items = get_avito_data(url)
                
                if not items:
                    continue

                # Проверяем только первые 10, чтобы не тратить ресурсы
                for item in items[:10]:
                    ad_id = str(item.get('data-item-id'))
                    
                    cur.execute("SELECT ad_id FROM ads WHERE ad_id = ?", (ad_id,))
                    if cur.fetchone() is None:
                        # Собираем данные об объявлении
                        title_tag = item.find('a', {'data-marker': 'item-title'})
                        if title_tag:
                            title = title_tag.get('title', 'Новое объявление').replace('купить в Челябинске на Авито', '').strip()
                            link = "https://www.avito.ru" + title_tag['href']
                            
                            try:
                                price = item.find('p', {'data-marker': 'item-price'}).text
                            except:
                                price = "Цена не указана"

                            caption = f"🌟 <b>{title}</b>\n💰 <b>{price}</b>\n\n🔗 <a href='{link}'>Открыть на Авито</a>"
                            
                            try:
                                bot.send_message(chat_id, caption, parse_mode="HTML")
                                # Сохраняем в БД только после успешной отправки
                                cur.execute("INSERT INTO ads (ad_id) VALUES (?)", (ad_id,))
                                conn.commit()
                            except Exception as send_error:
                                print(f"Ошибка отправки сообщения: {send_error}")
                
                # Небольшая пауза между пользователями
                time.sleep(5) 

        except Exception as e:
            print(f" Ошибка в цикле обновлений: {e}")
        
        # Интервал проверки (раз в 3-5 минут)
        time.sleep(random.randint(180, 300))

# --- 5. ОБРАБОТКА КОМАНД ---
@bot.message_handler(commands=['start'])
def welcome(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("❌ Остановить мониторинг"))
    bot.reply_to(message, "👋 Привет! Пришли ссылку на поиск Авито (с настроенными фильтрами), и я буду присылать новые объявления.", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "❌ Остановить мониторинг")
def stop_monitoring(message):
    cur.execute("DELETE FROM users WHERE chat_id = ?", (message.chat.id,))
    conn.commit()
    bot.send_message(message.chat.id, "⏹ Мониторинг остановлен. Твоя ссылка удалена.")

@bot.message_handler(func=lambda m: "avito.ru" in m.text)
def set_link(message):
    url = message.text.strip()
    cur.execute("INSERT OR REPLACE INTO users (chat_id, url) VALUES (?, ?)", (message.chat.id, url))
    conn.commit()
    bot.send_message(message.chat.id, "✅ Ссылка принята! Начинаю поиск новых объявлений...")
    
    # Сразу записываем текущие объявления как "старые", чтобы не спамить ими
    items = get_avito_data(url)
    for item in items:
        ad_id = str(item.get('data-item-id'))
        cur.execute("INSERT OR IGNORE INTO ads (ad_id) VALUES (?)", (ad_id,))
    conn.commit()

# --- 6. ЗАПУСК ---
if __name__ == "__main__":
    # 1. Запуск Flask
    threading.Thread(target=run_flask, daemon=True).start()
    
    # 2. Очистка старых вебхуков/сессий (лечит ошибку 409)
    try:
        bot.remove_webhook()
        time.sleep(1)
    except:
        pass
    
    # 3. Запуск мониторинга
    threading.Thread(target=check_updates, daemon=True).start()
    
    print("🚀 Бот успешно запущен!")
    
    # 4. Запуск бота с автоматическим пропуском ошибок
    while True:
        try:
            bot.polling(none_stop=True, interval=2, timeout=20) # interval=2 дает паузу между запросами к API Telegram
        except ApiTelegramException as e:
            if e.error_code == 409:
                print("⚠️ Конфликт (409). Ждем завершения другой копии...")
                time.sleep(10)
            else:
                print(f"⚠️ Ошибка Telegram API: {e}")
                time.sleep(5)
        except Exception as e:
            print(f"⚠️ Неизвестная ошибка: {e}")
            time.sleep(5)
