import time
import sqlite3
import datetime
import json
import re
import random
import urllib.parse
from curl_cffi import requests
from bs4 import BeautifulSoup
from telebot import TeleBot, types # Добавили types для кнопок

# --- НАСТРОЙКИ ---
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

# Функция создания кнопок
def main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    btn_stop = types.KeyboardButton("❌ Остановить мониторинг")
    markup.add(btn_stop)
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
        items = soup.find_all('div', {'data-marker': 'item'})
        return catalog_info, items
    except: return None, []

def send_ad(chat_id, item, info):
    ad_id = str(item.get('data-item-id'))
    title_tag = item.find('a', {'data-marker': 'item-title'})
    if not title_tag: return
    title = title_tag.get('title', '').replace('купить в Челябинске на Авито', '').strip()
    try: price = item.find('meta', {'itemprop': 'price'}).get('content') + " ₽"
    except: price = "Цена не указана"
    link = "https://www.avito.ru" + title_tag['href']
    extra = info.get(ad_id, {})
    photo = extra.get('img') or (item.find('img').get('src') if item.find('img') else None)
    description = extra.get('desc') or "Описание доступно по ссылке"

    caption = (f"<b>{title}</b>\n💰 <b>{price}</b>\n📍 Челябинск\n\n🔗 <a href='{link}'>Открыть на Avito</a>\n"
               f"________________________\n📝 {description[:350]}...")
    try:
        if photo: bot.send_photo(chat_id, photo, caption=caption, parse_mode="HTML", reply_markup=main_menu())
        else: bot.send_message(chat_id, caption, parse_mode="HTML", reply_markup=main_menu())
    except: pass

@bot.message_handler(commands=['start'])
def welcome(message):
    bot.reply_to(message, "Пришли ссылку на поиск Авито для мониторинга.", reply_markup=main_menu())

# ОБРАБОТКА КНОПКИ ОТМЕНЫ
@bot.message_handler(func=lambda m: m.text == "❌ Остановить мониторинг")
def stop_monitoring(message):
    chat_id = message.chat.id
    db_cur.execute("DELETE FROM users WHERE chat_id = ?", (chat_id,))
    db_conn.commit()
    bot.send_message(chat_id, "⏹ Мониторинг остановлен. Твоя ссылка удалена. Чтобы начать заново — просто пришли новую ссылку.")

@bot.message_handler(func=lambda m: "avito.ru" in m.text)
def set_link(message):
    chat_id = message.chat.id
    url = message.text.strip()
    db_cur.execute("INSERT OR REPLACE INTO users (chat_id, url) VALUES (?, ?)", (chat_id, url))
    db_conn.commit()
    bot.send_message(chat_id, "✅ Ссылка принята! Мониторинг запущен.", reply_markup=main_menu())
    
    info, items = get_avito_data(url)
    if items:
        for item in items:
            ad_id = str(item.get('data-item-id'))
            db_cur.execute("INSERT OR IGNORE INTO ads (ad_id) VALUES (?)", (ad_id,))
        db_conn.commit()
        send_ad(chat_id, items[0], info)

def check_updates():
    while True:
        try:
            db_cur.execute("SELECT chat_id, url FROM users")
            users = db_cur.fetchall()
            for chat_id, url in users:
                info, items = get_avito_data(url)
                if items:
                    for item in items:
                        ad_id = str(item.get('data-item-id'))
                        db_cur.execute("SELECT ad_id FROM ads WHERE ad_id = ?", (ad_id,))
                        if db_cur.fetchone() is None:
                            send_ad(chat_id, item, info)
                            db_cur.execute("INSERT INTO ads (ad_id) VALUES (?)", (ad_id,))
                            db_conn.commit()
        except: pass
        time.sleep(random.randint(30, 70))

if __name__ == "__main__":
    import threading
    threading.Thread(target=check_updates, daemon=True).start()
    print("🚀 Бот с кнопкой отмены запущен!")
    bot.polling(none_stop=True)


