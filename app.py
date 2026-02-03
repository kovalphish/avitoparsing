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
import concurrent.futures

# --- НАСТРОЙКИ И ЛОГИРОВАНИЕ ---
TOKEN = "8570991374:AAGOxulL0W679vZ6g4P0HhbAkqY14JxhhU8"
bot = TeleBot(TOKEN)

# Настройка логирования
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Блокировка для работы с БД
db_lock = Lock()

# Прокси (опционально, если нужен)
PROXIES = None  # {"http": "http://proxy:port", "https": "http://proxy:port"}

def init_db():
    """Инициализация базы данных"""
    conn = sqlite3.connect("monitor_bot.db", check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY,
            url TEXT,
            active BOOLEAN DEFAULT 1,
            last_check TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
    
    # Создаем индексы
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ads_chat_id ON ads(chat_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ads_ad_id ON ads(ad_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_users_active ON users(active)")
    
    conn.commit()
    return conn, cur

db_conn, db_cur = init_db()

# --- ФУНКЦИИ ДЛЯ РАБОТЫ С БД ---
def save_ad_to_db(chat_id, ad_id, url, title, price):
    """Сохранение объявления в БД"""
    try:
        with db_lock:
            db_cur.execute("""
                INSERT OR IGNORE INTO ads (ad_id, chat_id, url, title, price)
                VALUES (?, ?, ?, ?, ?)
            """, (ad_id, chat_id, url, title, price))
            db_conn.commit()
            return True
    except Exception as e:
        logger.error(f"Ошибка сохранения в БД: {e}")
        return False

def is_ad_seen(chat_id, ad_id):
    """Проверка, видел ли пользователь это объявление"""
    try:
        with db_lock:
            db_cur.execute(
                "SELECT 1 FROM ads WHERE chat_id = ? AND ad_id = ? LIMIT 1",
                (chat_id, ad_id)
            )
            return db_cur.fetchone() is not None
    except Exception as e:
        logger.error(f"Ошибка проверки БД: {e}")
        return False

def get_user_url(chat_id):
    """Получение URL пользователя"""
    try:
        with db_lock:
            db_cur.execute(
                "SELECT url FROM users WHERE chat_id = ? AND active = 1",
                (chat_id,)
            )
            result = db_cur.fetchone()
            return result['url'] if result else None
    except Exception as e:
        logger.error(f"Ошибка получения URL: {e}")
        return None

def get_active_users():
    """Получение всех активных пользователей"""
    try:
        with db_lock:
            db_cur.execute("SELECT chat_id, url FROM users WHERE active = 1")
            return db_cur.fetchall()
    except Exception as e:
        logger.error(f"Ошибка получения пользователей: {e}")
        return []

def update_last_check(chat_id):
    """Обновление времени последней проверки"""
    try:
        with db_lock:
            db_cur.execute(
                "UPDATE users SET last_check = CURRENT_TIMESTAMP WHERE chat_id = ?",
                (chat_id,)
            )
            db_conn.commit()
    except Exception as e:
        logger.error(f"Ошибка обновления времени: {e}")

# --- ФУНКЦИИ ДЛЯ ПАРСИНГА АВИТО ---
def get_random_user_agent():
    """Генерация случайного User-Agent"""
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    ]
    return random.choice(user_agents)

def parse_avito_page(url, max_retries=3):
    """Парсинг страницы Авито с улучшенной обработкой"""
    headers = {
        'User-Agent': get_random_user_agent(),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Cache-Control': 'max-age=0',
        'Referer': 'https://www.avito.ru/',
        'DNT': '1',
    }
    
    for attempt in range(max_retries):
        try:
            logger.info(f"Попытка {attempt + 1} парсинга {url}")
            
            # Задержка перед запросом
            time.sleep(random.uniform(2, 5))
            
            response = requests.get(
                url,
                headers=headers,
                proxies=PROXIES,
                timeout=15,
                verify=True
            )
            
            logger.info(f"Статус: {response.status_code}, Размер: {len(response.text)} байт")
            
            if response.status_code != 200:
                logger.warning(f"Неверный статус: {response.status_code}")
                if attempt < max_retries - 1:
                    time.sleep(10)
                    continue
                return None, []
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Проверяем на блокировку
            if "Доступ ограничен" in response.text or "blocked" in response.text.lower():
                logger.error("Авито заблокировал доступ")
                return None, []
            
            # Способ 1: Ищем объявления через data-marker
            items = soup.find_all('div', {'data-marker': re.compile(r'item')})
            logger.info(f"Найдено объявлений (способ 1): {len(items)}")
            
            # Способ 2: Если первый способ не нашел, ищем по классам
            if not items:
                items = soup.find_all('div', class_=re.compile(r'iva-item-body|item'))
                logger.info(f"Найдено объявлений (способ 2): {len(items)}")
            
            # Способ 3: Ищем в JSON данных
            script_data = {}
            scripts = soup.find_all('script')
            for script in scripts:
                if script.string and 'window.__initialData__' in script.string:
                    try:
                        script_content = script.string
                        # Ищем JSON структуру
                        match = re.search(r'window\.__initialData__\s*=\s*(.*?);\s*$', script_content, re.MULTILINE | re.DOTALL)
                        if match:
                            json_str = match.group(1).strip()
                            # Убираем лишние кавычки
                            if json_str.startswith('"') and json_str.endswith('"'):
                                json_str = json_str[1:-1]
                                json_str = urllib.parse.unquote(json_str)
                            
                            data = json.loads(json_str)
                            # Ищем каталог
                            def find_items(obj, path=""):
                                if isinstance(obj, dict):
                                    if 'items' in obj and isinstance(obj['items'], list):
                                        if obj['items'] and isinstance(obj['items'][0], dict) and 'id' in obj['items'][0]:
                                            return obj['items']
                                    for key, value in obj.items():
                                        result = find_items(value, f"{path}.{key}")
                                        if result:
                                            return result
                                elif isinstance(obj, list):
                                    for i, item in enumerate(obj):
                                        result = find_items(item, f"{path}[{i}]")
                                        if result:
                                            return result
                                return None
                            
                            items_data = find_items(data)
                            if items_data:
                                for item in items_data:
                                    if isinstance(item, dict) and 'id' in item:
                                        item_id = str(item['id'])
                                        script_data[item_id] = {
                                            'title': item.get('title', ''),
                                            'description': item.get('description', ''),
                                            'price': item.get('price', ''),
                                            'images': item.get('images', []),
                                            'url': item.get('url', '')
                                        }
                    except Exception as e:
                        logger.error(f"Ошибка парсинга JSON: {e}")
                        continue
            
            # Если ни один способ не нашел объявления, сохраняем HTML для отладки
            if not items:
                logger.error("Не найдено объявлений ни одним способом")
                with open(f"debug_{int(time.time())}.html", "w", encoding="utf-8") as f:
                    f.write(response.text[:10000])
            
            return script_data, items
            
        except requests.exceptions.Timeout:
            logger.warning(f"Таймаут при запросе (попытка {attempt + 1})")
            if attempt < max_retries - 1:
                time.sleep(10)
                continue
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка сети: {e}")
            if attempt < max_retries - 1:
                time.sleep(15)
                continue
        except Exception as e:
            logger.error(f"Неожиданная ошибка: {e}")
            if attempt < max_retries - 1:
                time.sleep(10)
                continue
    
    return None, []

def extract_ad_info(item, script_data):
    """Извлечение информации из объявления"""
    try:
        # Получаем ID объявления
        ad_id = None
        
        # Способ 1: из data-item-id
        if item.has_attr('data-item-id'):
            ad_id = item.get('data-item-id')
        
        # Способ 2: из data-marker
        if not ad_id and item.has_attr('data-marker'):
            marker = item.get('data-marker', '')
            if marker.startswith('item-'):
                ad_id = marker.replace('item-', '')
        
        # Способ 3: ищем в содержимом
        if not ad_id:
            id_elem = item.find('a', {'data-marker': 'item-title'})
            if id_elem and id_elem.get('href'):
                match = re.search(r'/(\d+)$', id_elem.get('href'))
                if match:
                    ad_id = match.group(1)
        
        if not ad_id:
            logger.warning("Не удалось извлечь ID объявления")
            return None
        
        # Получаем заголовок
        title_elem = item.find('a', {'data-marker': 'item-title'})
        if not title_elem:
            title_elem = item.find('h3', class_=re.compile(r'title|item-title'))
        
        title = title_elem.get_text(strip=True) if title_elem else "Без названия"
        
        # Получаем цену
        price_elem = item.find('meta', {'itemprop': 'price'})
        if price_elem:
            price = price_elem.get('content', '')
        else:
            price_elem = item.find('span', {'data-marker': 'item-price'})
            if price_elem:
                price = price_elem.get_text(strip=True)
            else:
                price_elem = item.find('p', class_=re.compile(r'price|item-price'))
                price = price_elem.get_text(strip=True) if price_elem else "Цена не указана"
        
        # Форматируем цену
        if price and re.search(r'\d', price):
            price = re.sub(r'\s+', ' ', price.strip())
        
        # Получаем ссылку
        if title_elem and title_elem.get('href'):
            link = "https://www.avito.ru" + title_elem['href']
        else:
            link = f"https://www.avito.ru/{ad_id}"
        
        # Получаем дополнительные данные из script_data
        extra_data = script_data.get(ad_id, {})
        
        # Получаем описание
        description = extra_data.get('description', '')
        if not description:
            desc_elem = item.find('div', class_=re.compile(r'description|item-description-step-two'))
            if desc_elem:
                description = desc_elem.get_text(strip=True)[:300]
        
        # Получаем изображение
        image_url = None
        if extra_data.get('images'):
            image_url = extra_data['images'][0].get('640x480') or extra_data['images'][0].get('url', '')
        
        if not image_url:
            img_elem = item.find('img')
            if img_elem:
                image_url = img_elem.get('src') or img_elem.get('data-src', '')
        
        return {
            'id': ad_id,
            'title': title,
            'price': price,
            'url': link,
            'description': description,
            'image': image_url,
            'extra': extra_data
        }
        
    except Exception as e:
        logger.error(f"Ошибка извлечения информации: {e}")
        return None

def send_ad_to_user(chat_id, ad_info):
    """Отправка объявления пользователю"""
    try:
        if not ad_info:
            return False
        
        # Проверяем, не отправляли ли уже это объявление
        if is_ad_seen(chat_id, ad_info['id']):
            logger.debug(f"Объявление {ad_info['id']} уже было отправлено")
            return False
        
        # Формируем сообщение
        caption = (f"<b>{ad_info['title']}</b>\n\n"
                  f"💰 <b>{ad_info['price']}</b>\n\n")
        
        if ad_info['description']:
            caption += f"📝 {ad_info['description']}\n\n"
        
        caption += f"🔗 <a href='{ad_info['url']}'>Смотреть на Авито</a>"
        
        # Отправляем сообщение
        try:
            if ad_info['image'] and ad_info['image'].startswith('http'):
                msg = bot.send_photo(
                    chat_id=chat_id,
                    photo=ad_info['image'],
                    caption=caption,
                    parse_mode='HTML',
                    reply_markup=main_menu()
                )
            else:
                msg = bot.send_message(
                    chat_id=chat_id,
                    text=caption,
                    parse_mode='HTML',
                    reply_markup=main_menu(),
                    disable_web_page_preview=False
                )
            
            logger.info(f"Отправлено объявление {ad_info['id']} пользователю {chat_id}")
            
            # Сохраняем в БД
            save_ad_to_db(
                chat_id,
                ad_info['id'],
                ad_info['url'],
                ad_info['title'],
                ad_info['price']
            )
            
            return True
            
        except Exception as send_error:
            logger.error(f"Ошибка отправки: {send_error}")
            # Пробуем отправить без фото
            try:
                msg = bot.send_message(
                    chat_id=chat_id,
                    text=caption,
                    parse_mode='HTML',
                    reply_markup=main_menu()
                )
                save_ad_to_db(
                    chat_id,
                    ad_info['id'],
                    ad_info['url'],
                    ad_info['title'],
                    ad_info['price']
                )
                return True
            except Exception as e:
                logger.error(f"Не удалось отправить даже текст: {e}")
                return False
                
    except Exception as e:
        logger.error(f"Ошибка в send_ad_to_user: {e}")
        return False

# --- КНОПКИ И ОБРАБОТЧИКИ ---
def main_menu():
    """Создание меню с кнопками"""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    btn_stop = types.KeyboardButton("❌ Остановить")
    btn_status = types.KeyboardButton("📊 Статус")
    btn_test = types.KeyboardButton("🔍 Проверить сейчас")
    markup.add(btn_status, btn_test, btn_stop)
    return markup

@bot.message_handler(commands=['start', 'help'])
def welcome(message):
    """Приветственное сообщение"""
    welcome_text = (
        "👋 <b>Avito Monitor Bot</b>\n\n"
        "Я помогу отслеживать новые объявления на Avito!\n\n"
        "📌 <b>Как использовать:</b>\n"
        "1. Пришли мне ссылку на поиск Avito\n"
        "2. Я начну отслеживать новые объявления\n"
        "3. Получай уведомления о новых предложениях\n\n"
        "🔧 <b>Команды:</b>\n"
        "/start - показать это сообщение\n"
        "/test <url> - протестировать парсинг\n"
        "/stats - статистика\n\n"
        "⚠️ <b>Внимание:</b> Avito может блокировать частые запросы. "
        "Интервал проверки: 5-10 минут."
    )
    
    bot.send_message(
        message.chat.id,
        welcome_text,
        parse_mode='HTML',
        reply_markup=main_menu()
    )

@bot.message_handler(commands=['test'])
def test_parsing(message):
    """Тестирование парсинга"""
    chat_id = message.chat.id
    
    # Проверяем, есть ли ссылка в команде
    parts = message.text.split()
    if len(parts) > 1:
        url = parts[1]
    else:
        # Берем сохраненную ссылку
        url = get_user_url(chat_id)
        if not url:
            bot.reply_to(message, "❌ Сначала пришли ссылку для мониторинга")
            return
    
    bot.send_message(chat_id, "🔍 Тестирую парсинг...")
    
    try:
        script_data, items = parse_avito_page(url)
        
        if not items:
            bot.send_message(chat_id, "❌ Не удалось найти объявления. Проверьте ссылку.")
            return
        
        bot.send_message(chat_id, f"✅ Найдено объявлений: {len(items)}")
        
        # Показываем первое объявление
        if items:
            ad_info = extract_ad_info(items[0], script_data)
            if ad_info:
                send_ad_to_user(chat_id, ad_info)
            else:
                bot.send_message(chat_id, "⚠️ Не удалось извлечь информацию из первого объявления")
        
        # Сохраняем все ID для будущих проверок
        for i, item in enumerate(items[:10]):
            ad_info = extract_ad_info(item, script_data)
            if ad_info and ad_info['id']:
                save_ad_to_db(
                    chat_id,
                    ad_info['id'],
                    ad_info['url'],
                    ad_info['title'],
                    ad_info['price']
                )
        
        bot.send_message(chat_id, "✅ Тест завершен. ID объявлений сохранены.")
        
    except Exception as e:
        logger.error(f"Ошибка теста: {e}")
        bot.send_message(chat_id, f"❌ Ошибка: {str(e)}")

@bot.message_handler(commands=['stats'])
def show_stats(message):
    """Показать статистику"""
    chat_id = message.chat.id
    
    with db_lock:
        db_cur.execute(
            "SELECT COUNT(*) as total FROM ads WHERE chat_id = ?",
            (chat_id,)
        )
        total_ads = db_cur.fetchone()['total']
        
        db_cur.execute(
            "SELECT url, active, last_check FROM users WHERE chat_id = ?",
            (chat_id,)
        )
        user_info = db_cur.fetchone()
    
    if user_info:
        status = "✅ Активен" if user_info['active'] else "❌ Остановлен"
        stats_text = (
            f"📊 <b>Статистика</b>\n\n"
            f"Статус: {status}\n"
            f"Всего объявлений: {total_ads}\n"
            f"Последняя проверка: {user_info['last_check']}\n"
            f"Ссылка: {user_info['url'][:50]}..."
        )
    else:
        stats_text = "❌ Мониторинг не настроен. Пришлите ссылку."
    
    bot.send_message(chat_id, stats_text, parse_mode='HTML', reply_markup=main_menu())

@bot.message_handler(func=lambda m: m.text == "❌ Остановить")
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
        "⏹ Мониторинг остановлен.\n"
        "Для возобновления пришлите новую ссылку.",
        reply_markup=types.ReplyKeyboardRemove()
    )
    logger.info(f"Мониторинг остановлен для {chat_id}")

@bot.message_handler(func=lambda m: m.text == "📊 Статус")
def status_handler(message):
    """Обработчик кнопки статуса"""
    show_stats(message)

@bot.message_handler(func=lambda m: m.text == "🔍 Проверить сейчас")
def check_now_handler(message):
    """Принудительная проверка"""
    chat_id = message.chat.id
    url = get_user_url(chat_id)
    
    if not url:
        bot.send_message(chat_id, "❌ Сначала настройте мониторинг")
        return
    
    bot.send_message(chat_id, "🔄 Проверяю сейчас...")
    
    # Запускаем проверку в отдельном потоке
    def check():
        try:
            script_data, items = parse_avito_page(url)
            new_count = 0
            
            if items:
                for item in items[:20]:  # Проверяем первые 20
                    ad_info = extract_ad_info(item, script_data)
                    if ad_info and send_ad_to_user(chat_id, ad_info):
                        new_count += 1
                        time.sleep(1)  # Задержка между отправками
            
            if new_count > 0:
                bot.send_message(chat_id, f"✅ Найдено новых: {new_count}")
            else:
                bot.send_message(chat_id, "✅ Новых объявлений нет")
                
        except Exception as e:
            logger.error(f"Ошибка проверки: {e}")
            bot.send_message(chat_id, f"❌ Ошибка: {str(e)}")
    
    Thread(target=check, daemon=True).start()

@bot.message_handler(func=lambda m: "avito.ru" in m.text.lower())
def handle_avito_link(message):
    """Обработка ссылки на Авито"""
    chat_id = message.chat.id
    url = message.text.strip()
    
    # Валидация URL
    if not re.match(r'^https?://(www\.)?avito\.ru/.+', url):
        bot.reply_to(message, "❌ Неверная ссылка. Нужна ссылка на поиск Avito.")
        return
    
    # Сохраняем пользователя
    with db_lock:
        db_cur.execute("""
            INSERT OR REPLACE INTO users (chat_id, url, active, last_check)
            VALUES (?, ?, 1, CURRENT_TIMESTAMP)
        """, (chat_id, url))
        db_conn.commit()
    
    bot.send_message(
        chat_id,
        "✅ Ссылка сохранена! Начинаю мониторинг...\n"
        "Проверяю первые объявления...",
        reply_markup=main_menu()
    )
    
    # Тестируем парсинг и сохраняем первые объявления
    def initial_scan():
        try:
            script_data, items = parse_avito_page(url)
            
            if not items:
                bot.send_message(
                    chat_id,
                    "⚠️ Не удалось найти объявления. Проверьте ссылку."
                )
                return
            
            initial_count = 0
            for item in items[:15]:  # Сохраняем первые 15
                ad_info = extract_ad_info(item, script_data)
                if ad_info and ad_info['id']:
                    save_ad_to_db(
                        chat_id,
                        ad_info['id'],
                        ad_info['url'],
                        ad_info['title'],
                        ad_info['price']
                    )
                    initial_count += 1
            
            bot.send_message(
                chat_id,
                f"✅ Мониторинг запущен!\n"
                f"Сохранено объявлений: {initial_count}\n"
                f"Теперь буду присылать только новые.",
                reply_markup=main_menu()
            )
            
            # Отправляем первое объявление для примера
            if items:
                ad_info = extract_ad_info(items[0], script_data)
                if ad_info:
                    time.sleep(2)
                    send_ad_to_user(chat_id, ad_info)
                    
        except Exception as e:
            logger.error(f"Ошибка начального сканирования: {e}")
            bot.send_message(
                chat_id,
                f"❌ Ошибка при обработке: {str(e)}"
            )
    
    Thread(target=initial_scan, daemon=True).start()

# --- ФОНОВАЯ ПРОВЕРКА ---
def check_for_new_ads():
    """Фоновая проверка новых объявлений"""
    logger.info("🚀 Фоновая проверка запущена")
    
    while True:
        try:
            users = get_active_users()
            logger.info(f"Проверяем {len(users)} пользователей")
            
            for user in users:
                chat_id = user['chat_id']
                url = user['url']
                
                logger.info(f"Проверка для пользователя {chat_id}")
                
                try:
                    # Парсим страницу
                    script_data, items = parse_avito_page(url)
                    
                    if not items:
                        logger.warning(f"Не найдено объявлений для {chat_id}")
                        continue
                    
                    # Проверяем новые объявления
                    new_ads = 0
                    for item in items[:25]:  # Проверяем первые 25
                        ad_info = extract_ad_info(item, script_data)
                        if ad_info and not is_ad_seen(chat_id, ad_info['id']):
                            logger.info(f"Новое объявление {ad_info['id']} для {chat_id}")
                            
                            # Отправляем объявление
                            if send_ad_to_user(chat_id, ad_info):
                                new_ads += 1
                            
                            # Задержка между отправками
                            if new_ads < 5:  # Первые 5 сразу
                                time.sleep(random.uniform(3, 7))
                            else:  # Остальные с большей задержкой
                                time.sleep(random.uniform(10, 20))
                            
                            # Если уже много новых, делаем паузу
                            if new_ads >= 10:
                                logger.info(f"Много новых объявлений ({new_ads}) для {chat_id}")
                                break
                    
                    if new_ads > 0:
                        logger.info(f"Отправлено {new_ads} новых объявлений пользователю {chat_id}")
                    
                    # Обновляем время проверки
                    update_last_check(chat_id)
                    
                    # Случайная задержка между пользователями
                    time.sleep(random.uniform(15, 30))
                    
                except Exception as e:
                    logger.error(f"Ошибка для пользователя {chat_id}: {e}")
                    time.sleep(30)
                    continue
            
            # Пауза между циклами (5-10 минут)
            sleep_time = random.randint(300, 600)
            logger.info(f"Следующая проверка через {sleep_time // 60} минут")
            time.sleep(sleep_time)
            
        except Exception as e:
            logger.error(f"Критическая ошибка в фоновой проверке: {e}")
            time.sleep(60)

# --- ЗАПУСК БОТА ---
if __name__ == "__main__":
    # Запускаем фоновую проверку
    monitor_thread = Thread(target=check_for_new_ads, daemon=True)
    monitor_thread.start()
    
    logger.info("🤖 Бот запущен!")
    logger.info(f"Токен: {TOKEN[:10]}...")
    
    # Основной цикл бота
    while True:
        try:
            logger.info("Запуск polling...")
            bot.polling(
                none_stop=True,
                interval=1,
                timeout=30,
                long_polling_timeout=30
            )
        except Exception as e:
            logger.error(f"Ошибка в основном цикле бота: {e}")
            time.sleep(10)
