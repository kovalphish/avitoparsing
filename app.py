import time
import sqlite3
import json
import re
import random
import urllib.parse
import logging
from threading import Lock
from curl_cffi import requests
from bs4 import BeautifulSoup
from telebot import TeleBot, types

# --- НАСТРОЙКИ И ЛОГИРОВАНИЕ ---
TOKEN = "8570991374:AAGOxulL0W679vZ6g4P0HhbAkqY14JxhhU8"
bot = TeleBot(TOKEN)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Блокировка для работы с БД
db_lock = Lock()

def init_db():
    """Инициализация базы данных"""
    conn = sqlite3.connect("monitor_bot.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    # Таблица пользователей
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY,
            url TEXT,
            active BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Таблица объявлений с привязкой к пользователю
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ads (
            ad_id TEXT,
            chat_id INTEGER,
            url TEXT,
            title TEXT,
            price TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (ad_id, chat_id),
            FOREIGN KEY (chat_id) REFERENCES users(chat_id)
        )
    """)
    
    # Индексы для ускорения поиска
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ads_user ON ads(chat_id, ad_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_users_active ON users(active)")
    
    conn.commit()
    return conn, cur

db_conn, db_cur = init_db()

# --- ФУНКЦИИ ДЛЯ РАБОТЫ С БД ---
def save_ad(chat_id, ad_id, url, title, price):
    """Сохранение объявления в БД"""
    with db_lock:
        db_cur.execute("""
            INSERT OR IGNORE INTO ads (ad_id, chat_id, url, title, price)
            VALUES (?, ?, ?, ?, ?)
        """, (ad_id, chat_id, url, title, price))
        db_conn.commit()

def is_ad_seen(chat_id, ad_id):
    """Проверка, видел ли пользователь это объявление"""
    with db_lock:
        db_cur.execute(
            "SELECT 1 FROM ads WHERE chat_id = ? AND ad_id = ?",
            (chat_id, ad_id)
        )
        return db_cur.fetchone() is not None

def get_user_url(chat_id):
    """Получение URL пользователя"""
    with db_lock:
        db_cur.execute(
            "SELECT url FROM users WHERE chat_id = ? AND active = 1",
            (chat_id,)
        )
        result = db_cur.fetchone()
        return result['url'] if result else None

def get_all_active_users():
    """Получение всех активных пользователей"""
    with db_lock:
        db_cur.execute("SELECT chat_id, url FROM users WHERE active = 1")
        return db_cur.fetchall()

# --- ФУНКЦИИ ДЛЯ ПАРСИНГА ---
def get_avito_data(url, max_retries=3):
    """Получение данных с Авито с повторными попытками"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    
    for attempt in range(max_retries):
        try:
            session = requests.Session()
            # Добавляем случайную задержку между запросами
            time.sleep(random.uniform(1, 3))
            
            resp = session.get(
                url,
                headers=headers,
                impersonate="chrome110",
                timeout=30
            )
            
            logger.info(f"Статус код: {resp.status_code} для {url}")
            
            if resp.status_code == 403:
                logger.warning(f"Доступ запрещен (403) для {url}")
                return None, []
            if resp.status_code != 200:
                logger.warning(f"Неверный статус код: {resp.status_code}")
                if attempt < max_retries - 1:
                    time.sleep(5)
                    continue
                return None, []
            
            soup = BeautifulSoup(resp.content, 'html.parser')
            
            # Получаем данные из JSON
            catalog_info = {}
            script = soup.find("script", string=re.compile(r"window\.__initialData__\s*="))
            if script:
                try:
                    script_text = script.string
                    # Ищем JSON данные
                    match = re.search(r'window\.__initialData__\s*=\s*(.*?);', script_text, re.DOTALL)
                    if match:
                        data_str = match.group(1)
                        # Убираем возможные escape-последовательности
                        if data_str.startswith('"') and data_str.endswith('"'):
                            data_str = urllib.parse.unquote(data_str[1:-1])
                        data = json.loads(data_str)
                        
                        # Ищем каталог с объявлениями
                        def find_catalog(obj):
                            if isinstance(obj, dict):
                                if 'items' in obj and isinstance(obj['items'], list) and len(obj['items']) > 0:
                                    first_item = obj['items'][0]
                                    if isinstance(first_item, dict) and 'id' in first_item:
                                        return obj['items']
                                for value in obj.values():
                                    result = find_catalog(value)
                                    if result:
                                        return result
                            elif isinstance(obj, list):
                                for item in obj:
                                    result = find_catalog(item)
                                    if result:
                                        return result
                            return None
                        
                        items_data = find_catalog(data)
                        if items_data:
                            for item in items_data:
                                if isinstance(item, dict) and 'id' in item:
                                    item_id = str(item.get('id'))
                                    catalog_info[item_id] = {
                                        'desc': item.get('description', '').replace('\n', ' ').strip(),
                                        'img': item.get('images', [{}])[0].get('636x476') if item.get('images') else None
                                    }
                except Exception as e:
                    logger.error(f"Ошибка при парсинге JSON: {e}")
            
            # Парсим HTML
            items = soup.find_all('div', {'data-marker': re.compile(r'^item(-\d+)?$')})
            logger.info(f"Найдено {len(items)} объявлений на странице")
            
            return catalog_info, items
            
        except requests.exceptions.Timeout:
            logger.warning(f"Таймаут при запросе (попытка {attempt + 1}/{max_retries})")
            if attempt < max_retries - 1:
                time.sleep(10)
                continue
        except Exception as e:
            logger.error(f"Ошибка при парсинге: {e}")
            if attempt < max_retries - 1:
                time.sleep(5)
                continue
    
    return None, []

def send_ad(chat_id, item, info):
    """Отправка объявления в Telegram"""
    try:
        ad_id = str(item.get('data-item-id') or item.get('data-marker', '').replace('item-', ''))
        if not ad_id or ad_id == 'item':
            logger.warning("Не удалось получить ID объявления")
            return
        
        # Проверяем, не видел ли пользователь уже это объявление
        if is_ad_seen(chat_id, ad_id):
            logger.debug(f"Объявление {ad_id} уже было отправлено пользователю {chat_id}")
            return
        
        title_tag = item.find('a', {'data-marker': 'item-title'})
        if not title_tag:
            return
        
        title = title_tag.get('title', '')
        title = re.sub(r'купить (в|на)?.*?(на Авито|Авито)?$', '', title, flags=re.IGNORECASE).strip()
        
        # Пробуем разные способы получить цену
        price_elem = item.find('meta', {'itemprop': 'price'})
        if price_elem:
            price = price_elem.get('content', '')
        else:
            price_elem = item.find('span', {'data-marker': 'item-price'})
            if price_elem:
                price = price_elem.text.strip()
            else:
                price = "Цена не указана"
        
        if price and price.isdigit():
            price = f"{int(price):,} ₽".replace(",", " ")
        
        link = "https://www.avito.ru" + title_tag['href']
        
        extra = info.get(ad_id, {})
        
        # Ищем изображение
        photo = None
        img_elem = item.find('img')
        if img_elem:
            photo = img_elem.get('src') or img_elem.get('data-src')
        
        if not photo and extra.get('img'):
            photo = extra['img']
        
        description = extra.get('desc', '')
        if not description:
            desc_elem = item.find('div', {'class': re.compile(r'description|item-description')})
            if desc_elem:
                description = desc_elem.text.strip()[:350]
        
        # Формируем сообщение
        caption = (f"<b>{title}</b>\n"
                   f"💰 <b>{price}</b>\n"
                   f"🔗 <a href='{link}'>Открыть на Avito</a>\n")
        
        if description:
            caption += f"\n📝 {description[:350]}{'...' if len(description) > 350 else ''}"
        
        caption += "\n________________________"
        
        # Отправляем сообщение
        try:
            if photo and photo.startswith(('http://', 'https://')):
                msg = bot.send_photo(
                    chat_id,
                    photo,
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=main_menu()
                )
            else:
                msg = bot.send_message(
                    chat_id,
                    caption,
                    parse_mode="HTML",
                    reply_markup=main_menu()
                )
            
            # Сохраняем в БД после успешной отправки
            save_ad(chat_id, ad_id, link, title, price)
            logger.info(f"Отправлено объявление {ad_id} пользователю {chat_id}")
            
        except Exception as e:
            logger.error(f"Ошибка при отправке сообщения: {e}")
            # Пробуем отправить без фото
            try:
                msg = bot.send_message(
                    chat_id,
                    caption,
                    parse_mode="HTML",
                    reply_markup=main_menu()
                )
                save_ad(chat_id, ad_id, link, title, price)
            except Exception as e2:
                logger.error(f"Не удалось отправить даже текст: {e2}")
    
    except Exception as e:
        logger.error(f"Ошибка в send_ad: {e}")

# --- КНОПКИ И ОБРАБОТЧИКИ ---
def main_menu():
    """Создание меню с кнопками"""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    btn_stop = types.KeyboardButton("❌ Остановить мониторинг")
    btn_status = types.KeyboardButton("📊 Статус")
    markup.add(btn_stop, btn_status)
    return markup

@bot.message_handler(commands=['start', 'help'])
def welcome(message):
    """Приветственное сообщение"""
    bot.send_message(
        message.chat.id,
        "👋 Привет! Я бот для мониторинга новых объявлений на Авито.\n\n"
        "📌 Просто пришли мне ссылку на поиск Авито (например: https://www.avito.ru/...)\n"
        "Я начну отслеживать новые объявления и присылать их тебе.\n\n"
        "❌ Для остановки мониторинга нажми кнопку ниже.",
        reply_markup=main_menu()
    )

@bot.message_handler(func=lambda m: m.text == "❌ Остановить мониторинг")
def stop_monitoring(message):
    """Остановка мониторинга"""
    chat_id = message.chat.id
    with db_lock:
        db_cur.execute(
            "UPDATE users SET active = 0 WHERE chat_id = ?",
            (chat_id,)
        )
        db_conn.commit()
    
    bot.send_message(
        chat_id,
        "⏹ Мониторинг остановлен. Твоя ссылка удалена.\n"
        "Чтобы начать заново — просто пришли новую ссылку.",
        reply_markup=types.ReplyKeyboardRemove()
    )
    logger.info(f"Мониторинг остановлен для пользователя {chat_id}")

@bot.message_handler(func=lambda m: m.text == "📊 Статус")
def show_status(message):
    """Показ статуса мониторинга"""
    chat_id = message.chat.id
    url = get_user_url(chat_id)
    
    if url:
        with db_lock:
            db_cur.execute(
                "SELECT COUNT(*) as count FROM ads WHERE chat_id = ?",
                (chat_id,)
            )
            count = db_cur.fetchone()['count']
        
        bot.send_message(
            chat_id,
            f"✅ Мониторинг активен\n"
            f"📊 Всего получено объявлений: {count}\n"
            f"🔗 Отслеживаемая ссылка: {url[:50]}...",
            reply_markup=main_menu()
        )
    else:
        bot.send_message(
            chat_id,
            "❌ Мониторинг не активен. Пришли ссылку для начала отслеживания.",
            reply_markup=main_menu()
        )

@bot.message_handler(func=lambda m: "avito.ru" in m.text.lower())
def set_link(message):
    """Установка ссылки для мониторинга"""
    chat_id = message.chat.id
    url = message.text.strip()
    
    # Проверяем, что это валидная ссылка на Авито
    if not re.match(r'^https?://(www\.)?avito\.ru/.+', url):
        bot.reply_to(message, "❌ Это не похоже на ссылку Авито. Пришли корректную ссылку.")
        return
    
    # Сохраняем пользователя
    with db_lock:
        db_cur.execute("""
            INSERT OR REPLACE INTO users (chat_id, url, active)
            VALUES (?, ?, 1)
        """, (chat_id, url))
        db_conn.commit()
    
    bot.send_message(
        chat_id,
        "✅ Ссылка принята! Начинаю мониторинг...",
        reply_markup=main_menu()
    )
    logger.info(f"Начался мониторинг для пользователя {chat_id}")
    
    # Парсим первые объявления
    try:
        info, items = get_avito_data(url)
        if items:
            count = 0
            for item in items[:5]:  # Отправляем только первые 5
                send_ad(chat_id, item, info)
                count += 1
                time.sleep(1)  # Задержка между отправками
            
            bot.send_message(
                chat_id,
                f"✅ Мониторинг запущен! Первые {count} объявлений отправлены.\n"
                f"Теперь я буду присылать только новые объявления.",
                reply_markup=main_menu()
            )
        else:
            bot.send_message(
                chat_id,
                "⚠️ Не удалось получить объявления. Проверьте ссылку или попробуйте позже.",
                reply_markup=main_menu()
            )
    except Exception as e:
        logger.error(f"Ошибка при первоначальном парсинге: {e}")
        bot.send_message(
            chat_id,
            "❌ Ошибка при обработке ссылки. Попробуйте еще раз.",
            reply_markup=main_menu()
        )

# --- ФУНКЦИЯ ПРОВЕРКИ ОБНОВЛЕНИЙ ---
def check_updates():
    """Фоновая проверка обновлений"""
    logger.info("Запущен процесс проверки обновлений")
    
    while True:
        try:
            users = get_all_active_users()
            logger.info(f"Проверка обновлений для {len(users)} пользователей")
            
            for user in users:
                chat_id = user['chat_id']
                url = user['url']
                
                try:
                    info, items = get_avito_data(url)
                    if items:
                        new_ads = 0
                        for item in items:
                            if new_ads >= 10:  # Ограничение на количество новых объявлений за раз
                                break
                            
                            send_ad(chat_id, item, info)
                            if not is_ad_seen(chat_id, str(item.get('data-item-id'))):
                                new_ads += 1
                                # Задержка между отправками, чтобы не спамить
                                time.sleep(random.uniform(2, 4))
                        
                        if new_ads > 0:
                            logger.info(f"Отправлено {new_ads} новых объявлений пользователю {chat_id}")
                    
                    # Случайная задержка между пользователями
                    time.sleep(random.uniform(5, 15))
                    
                except Exception as e:
                    logger.error(f"Ошибка при проверке для пользователя {chat_id}: {e}")
                    continue
            
            # Пауза между циклами проверки (3-5 минут)
            sleep_time = random.randint(180, 300)
            logger.info(f"Следующая проверка через {sleep_time} секунд")
            time.sleep(sleep_time)
            
        except Exception as e:
            logger.error(f"Критическая ошибка в check_updates: {e}")
            time.sleep(60)

# --- ЗАПУСК БОТА ---
if __name__ == "__main__":
    import threading
    
    # Запускаем фоновую проверку в отдельном потоке
    monitor_thread = threading.Thread(target=check_updates, daemon=True)
    monitor_thread.start()
    
    logger.info("🚀 Бот запущен!")
    
    # Запускаем бота с обработкой ошибок
    while True:
        try:
            bot.polling(none_stop=True, interval=1, timeout=30)
        except Exception as e:
            logger.error(f"Ошибка в работе бота: {e}")
            time.sleep(10)
