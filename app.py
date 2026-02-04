import time
import sqlite3
import json
import re
import random
import urllib.parse
import logging
from threading import Lock, Thread
import requests
from bs4 import BeautifulSoup
from telebot import TeleBot, types

# --- НАСТРОЙКИ ---
# СРОЧНО ЗАМЕНИТЕ ТОКЕН ПОСЛЕ REVOKE!
TOKEN = "7714231951:AAEUl_BYZfitgOkUcLETLTWRrdw3E58qvN4"
bot = TeleBot(TOKEN)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('bot.log'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

db_lock = Lock()

def init_db():
    conn = sqlite3.connect("monitor_bot.db", check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY,
            url TEXT,
            active BOOLEAN DEFAULT 1,
            last_check TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ad_id TEXT NOT NULL,
            chat_id INTEGER NOT NULL,
            url TEXT,
            title TEXT,
            price TEXT,
            seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(ad_id, chat_id)
        )
    """)
    conn.commit()
    return conn, cur

db_conn, db_cur = init_db()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def get_user_url(chat_id):
    with db_lock:
        db_cur.execute("SELECT url FROM users WHERE chat_id = ? AND active = 1", (chat_id,))
        res = db_cur.fetchone()
        return res['url'] if res else None

def is_ad_seen(chat_id, ad_id):
    with db_lock:
        db_cur.execute("SELECT 1 FROM ads WHERE chat_id = ? AND ad_id = ?", (chat_id, ad_id))
        return db_cur.fetchone() is not None

def save_ad(chat_id, ad_id, url, title, price):
    try:
        with db_lock:
            db_cur.execute("INSERT OR IGNORE INTO ads (ad_id, chat_id, url, title, price) VALUES (?, ?, ?, ?, ?)",
                         (ad_id, chat_id, url, title, price))
            db_conn.commit()
    except Exception as e:
        logger.error(f"DB Error: {e}")

# --- ПАРСИНГ ---

def get_headers():
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
    ]
    return {
        'User-Agent': random.choice(user_agents),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'ru-RU,ru;q=0.9',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Upgrade-Insecure-Requests': '1'
    }

def parse_avito(url):
    # ПРИНУДИТЕЛЬНАЯ СОРТИРОВКА ПО ДАТЕ (параметр s=104)
    if "s=104" not in url:
        url += "&s=104" if "?" in url else "?s=104"

    try:
        # Используем сессию для имитации поведения браузера (куки)
        session = requests.Session()
        response = session.get(url, headers=get_headers(), timeout=20)
        
        if response.status_code != 200:
            logger.error(f"Код ответа: {response.status_code}")
            return []

        soup = BeautifulSoup(response.text, 'html.parser')
        # Ищем контейнеры объявлений
        items = soup.find_all('div', {'data-marker': 'item'})
        
        ads_data = []
        for item in items:
            try:
                ad_id = item.get('data-item-id')
                title_node = item.find('h3', {'itemprop': 'name'}) or item.find('a', {'data-marker': 'item-title'})
                title = title_node.get_text(strip=True)
                
                link = "https://www.avito.ru" + item.find('a', {'data-marker': 'item-title'})['href']
                
                price_node = item.find('meta', {'itemprop': 'price'})
                price = price_node['content'] + " руб." if price_node else item.find('span', {'data-marker': 'item-price'}).get_text(strip=True)
                
                img_node = item.find('img')
                image = img_node.get('src') if img_node else None

                ads_data.append({
                    'id': ad_id,
                    'title': title,
                    'price': price,
                    'url': link,
                    'image': image
                })
            except:
                continue
        
        # Переворачиваем список, чтобы новые (сверху страницы) обрабатывались последними
        # и бот присылал их в правильном порядке
        return list(reversed(ads_data))
    except Exception as e:
        logger.error(f"Ошибка парсинга: {e}")
        return []

# --- ЛОГИКА ОТПРАВКИ ---

def send_new_ads(chat_id, ads):
    new_found = 0
    for ad in ads:
        if not is_ad_seen(chat_id, ad['id']):
            caption = f"<b>{ad['title']}</b>\n💰 {ad['price']}\n\n🔗 <a href='{ad['url']}'>Открыть на Avito</a>"
            try:
                if ad['image'] and ad['image'].startswith('http'):
                    bot.send_photo(chat_id, ad['image'], caption=caption, parse_mode='HTML')
                else:
                    bot.send_message(chat_id, caption, parse_mode='HTML', disable_web_page_preview=False)
                
                save_ad(chat_id, ad['id'], ad['url'], ad['title'], ad['price'])
                new_found += 1
                time.sleep(random.uniform(2, 4)) # Задержка между сообщениями
            except Exception as e:
                logger.error(f"Ошибка отправки: {e}")
    return new_found

# --- ОБРАБОТЧИКИ КОМАНД ---

def main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("📊 Статус", "🔍 Проверить сейчас", "❌ Остановить")
    return markup

@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(message.chat.id, "Пришли ссылку на поиск Авито (отсортированную по дате).", reply_markup=main_menu())

@bot.message_handler(func=lambda m: "avito.ru" in m.text.lower())
def set_url(message):
    url = message.text.strip()
    with db_lock:
        db_cur.execute("INSERT OR REPLACE INTO users (chat_id, url, active) VALUES (?, ?, 1)", (message.chat.id, url))
        db_conn.commit()
    bot.send_message(message.chat.id, "✅ Мониторинг запущен! Сейчас проверю текущие объявления...")
    
    # Первичный прогон (запоминаем текущие, чтобы не спамить старьем)
    ads = parse_avito(url)
    for ad in ads:
        save_ad(message.chat.id, ad['id'], ad['url'], ad['title'], ad['price'])
    bot.send_message(message.chat.id, f"Готово. Запомнил {len(ads)} последних объявлений. Новые придут сюда.")

@bot.message_handler(func=lambda m: m.text == "🔍 Проверить сейчас")
def manual_check(message):
    url = get_user_url(message.chat.id)
    if url:
        ads = parse_avito(url)
        found = send_new_ads(message.chat.id, ads)
        if found == 0:
            bot.send_message(message.chat.id, "Новых объявлений пока нет.")
    else:
        bot.send_message(message.chat.id, "Сначала пришли ссылку.")

@bot.message_handler(func=lambda m: m.text == "❌ Остановить")
def stop(message):
    with db_lock:
        db_cur.execute("UPDATE users SET active = 0 WHERE chat_id = ?", (message.chat.id,))
        db_conn.commit()
    bot.send_message(message.chat.id, "Мониторинг остановлен.")

# --- ФОНОВЫЙ ЦИКЛ ---

def monitoring_loop():
    while True:
        try:
            with db_lock:
                db_cur.execute("SELECT chat_id, url FROM users WHERE active = 1")
                active_users = db_cur.fetchall()

            for user in active_users:
                chat_id, url = user['chat_id'], user['url']
                logger.info(f"Проверка для {chat_id}")
                ads = parse_avito(url)
                send_new_ads(chat_id, ads)
                
                with db_lock:
                    db_cur.execute("UPDATE users SET last_check = CURRENT_TIMESTAMP WHERE chat_id = ?", (chat_id,))
                    db_conn.commit()
                
                time.sleep(random.uniform(10, 20)) # Пауза между пользователями

            # Пауза между общими циклами (Авито не любит частые заходы)
            # Рекомендую ставить 300-600 секунд
            time.sleep(random.randint(300, 500))
        except Exception as e:
            logger.error(f"Ошибка цикла: {e}")
            time.sleep(60)

if __name__ == "__main__":
    Thread(target=monitoring_loop, daemon=True).start()
    while True:
        try:
            bot.polling(none_stop=True, interval=1, timeout=20)
        except Exception as e:
            logger.error(f"Polling error: {e}")
            time.sleep(5)
