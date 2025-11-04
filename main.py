import os
import time
import logging
import re
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import schedule
import requests
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator
from supabase import create_client

# === Логирование ===
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# === Настройки ===
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_IDS = [cid.strip() for cid in os.getenv("CHANNEL_ID1", "").split(",") if cid.strip()]
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
PORT = int(os.getenv("PORT", 10000))

# Проверка переменных
for var in ["TELEGRAM_BOT_TOKEN", "CHANNEL_ID1", "SUPABASE_URL", "SUPABASE_KEY"]:
    if not os.getenv(var):
        logger.error(f"❌ Переменная {var} не задана!")
        exit(1)

# === Supabase ===
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# === Вспомогательные функции ===
def clean_html(raw: str) -> str:
    if not raw:
        return ""
    return re.sub(r'<[^>]+>', '', raw).strip()

def translate(text: str) -> str:
    if not text.strip():
        return ""
    try:
        return GoogleTranslator(source='auto', target='ru').translate(text)
    except Exception as e:
        logger.warning(f"Перевод не удался: {e}")
        return text

def is_article_sent(url: str) -> bool:
    try:
        resp = supabase.table("published_articles").select("url").eq("url", url).execute()
        return len(resp.data) > 0
    except Exception as e:
        logger.error(f"Ошибка Supabase при проверке: {e}")
        return False

def mark_article_sent(url: str, title: str):
    try:
        supabase.table("published_articles").insert({"url": url, "title": title}).execute()
        logger.info(f"✅ Сохранено: {url}")
    except Exception as e:
        logger.error(f"Ошибка Supabase при сохранении: {e}")

def send_to_telegram(prefix: str, title: str, lead: str, url: str):
    try:
        title_ru = translate(title)
        lead_ru = translate(lead)
        message = f"<b>{prefix}</b>: {title_ru}\n\n{lead_ru}\n\nИсточник: {url}"
        for ch in CHANNEL_IDS:
            resp = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": ch, "text": message, "parse_mode": "HTML"},
                timeout=10
            )
            if resp.status_code == 200:
                logger.info(f"📤 Отправлено: {title[:60]}...")
            else:
                logger.error(f"❌ Ошибка Telegram: {resp.status_code} {resp.text}")
    except Exception as e:
        logger.exception(f"Ошибка отправки: {e}")

# === Парсеры ===

def parse_good_judgment():
    url = "https://goodjudgment.com/open-questions/"
    try:
        resp = requests.get(url, timeout=10)
        soup = BeautifulSoup(resp.text, 'html.parser')
        for item in soup.select('.question-title a'):
            title = item.get_text(strip=True)
            article_url = item['href']
            if not article_url.startswith('http'):
                article_url = 'https://goodjudgment.com' + article_url
            if is_article_sent(article_url):
                continue
            lead = "Superforecasting question"
            send_to_telegram("GOODJ", title, lead, article_url)
            mark_article_sent(article_url, title)
            time.sleep(1)
    except Exception as e:
        logger.error(f"Ошибка Good Judgment: {e}")

def parse_metaculus():
    api_url = "https://www.metaculus.com/api2/questions/?status=open&limit=10"
    try:
        resp = requests.get(api_url, timeout=10).json()
        for q in resp['results']:
            title = q.get('title', 'Без названия')
            page_url = q.get('page_url', '')
            if not page_url:
                continue
            url = "https://www.metaculus.com" + page_url
            if is_article_sent(url):
                continue
            desc = q.get('description', '')[:200] + "..."
            send_to_telegram("META", title, desc, url)
            mark_article_sent(url, title)
            time.sleep(1)
    except Exception as e:
        logger.error(f"Ошибка Metaculus: {e}")

def parse_dni_gt():
    url = "https://www.dni.gov/index.php/gt2040-home"
    try:
        resp = requests.get(url, timeout=10)
        soup = BeautifulSoup(resp.text, 'html.parser')
        # Ищем ссылку на PDF или заголовок отчёта
        title = "DNI Global Trends 2040"
        lead = "Declassified US intelligence forecast on global trends to 2040."
        # Найдём PDF-ссылку
        pdf_link = None
        for a in soup.find_all('a', href=True):
            if 'pdf' in a['href'].lower() and 'global' in a['href'].lower():
                pdf_link = a['href']
                break
        if pdf_link:
            full_url = requests.compat.urljoin(url, pdf_link)
            if not is_article_sent(full_url):
                send_to_telegram("DNI", title, lead, full_url)
                mark_article_sent(full_url, title)
        else:
            # Если PDF нет — просто отправим главную
            if not is_article_sent(url):
                send_to_telegram("DNI", title, lead, url)
                mark_article_sent(url, title)
    except Exception as e:
        logger.error(f"Ошибка DNI: {e}")

def parse_johns_hopkins():
    url = "https://www.centerforhealthsecurity.org/"
    try:
        resp = requests.get(url, timeout=10)
        soup = BeautifulSoup(resp.text, 'html.parser')
        # Ищем последние новости или отчёты
        for item in soup.select('h3 a, h2 a, .news-item a'):
            title = item.get_text(strip=True)
            article_url = item['href']
            if not article_url.startswith('http'):
                article_url = 'https://www.centerforhealthsecurity.org' + article_url
            if is_article_sent(article_url):
                continue
            lead = "Report from Johns Hopkins Center for Health Security"
            send_to_telegram("JHCHS", title, lead, article_url)
            mark_article_sent(article_url, title)
            time.sleep(1)
    except Exception as e:
        logger.error(f"Ошибка Johns Hopkins: {e}")

def parse_wef():
    # Хотя у WEF есть RSS, парсим напрямую для единообразия
    feed_url = "https://www.weforum.org/feed"
    try:
        import feedparser
        feed = feedparser.parse(feed_url)
        for entry in feed.entries[:5]:
            url = entry.get("link", "").strip()
            if not url or is_article_sent(url):
                continue
            title = entry.get("title", "").strip()
            desc = clean_html(entry.get("summary", "")).strip()
            if not title or not desc:
                continue
            lead = desc.split(". ")[0].strip() or desc[:150] + "..."
            send_to_telegram("WEF", title, lead, url)
            mark_article_sent(url, title)
            time.sleep(1)
    except Exception as e:
        logger.error(f"Ошибка WEF: {e}")

def parse_future_timeline():
    url = "https://www.futuretimeline.net/"
    try:
        resp = requests.get(url, timeout=10)
        soup = BeautifulSoup(resp.text, 'html.parser')
        for item in soup.select('li a'):
            title = item.get_text(strip=True)
            article_url = item['href']
            if article_url.startswith('/'):
                article_url = 'https://www.futuretimeline.net' + article_url
            if not article_url.startswith('http') or 'futuretimeline.net' not in article_url:
                continue
            if is_article_sent(article_url):
                continue
            lead = "Forecast from Future Timeline"
            send_to_telegram("FUTTL", title, lead, article_url)
            mark_article_sent(article_url, title)
            time.sleep(1)
    except Exception as e:
        logger.error(f"Ошибка Future Timeline: {e}")

# === Основная функция ===
def fetch_all():
    logger.info("🔍 Запуск парсинга не-RSS источников...")
    parse_good_judgment()
    parse_metaculus()
    parse_dni_gt()
    parse_johns_hopkins()
    parse_wef()
    parse_future_timeline()
    logger.info("✅ Парсинг завершён.")

# === HTTP-сервер для Render ===
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ["/", "/health"]:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.end_headers()

def run_http():
    server = HTTPServer(("", PORT), Handler)
    logger.info(f"🌐 HTTP сервер на порту {PORT}")
    server.serve_forever()

# === Запуск ===
if __name__ == "__main__":
    logger.info("🚀 Запуск второго бота (non-RSS источники)...")
    threading.Thread(target=run_http, daemon=True).start()
    fetch_all()
    schedule.every(6).hours.do(fetch_all)  # реже — раз в 6 часов
    while True:
        schedule.run_pending()
        time.sleep(60)
