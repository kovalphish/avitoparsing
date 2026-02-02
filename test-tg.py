import requests

TOKEN = "8570991374:AAGOxulL0W679vZ6g4P0HhbAkqY14JxhhU8"
CHAT_ID = 8140749540

url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
r = requests.post(url, data={"chat_id": CHAT_ID, "text": "Проверка связи! Если видишь это — отправка работает."})

print(f"Статус: {r.status_code}")
print(f"Ответ: {r.text}")