import os
import imaplib
import email
from email.header import decode_header
import time
import logging
import re
import schedule
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from deep_translator import GoogleTranslator
from supabase import create_client
# === Логирование ===
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# === Переменные окружения ===
EMAIL_USER = os.getenv("EMAIL_USER")      # monitor-russia-bot@gmail.com
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_IDS = [cid.strip() for cid in os.getenv("CHANNEL_ID1", "").split(",") if cid.strip()]
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
PORT = int(os.getenv("PORT", 10000))

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def clean_html(raw: str) -> str:
    return re.sub(r'<[^>]+>', '', raw)

def translate(text: str) -> str:
    try:
        return GoogleTranslator(source='auto', target='ru').translate(text)
    except:
        return text

def is_article_sent(message_id: str) -> bool:
    try:
        resp = supabase.table("published_articles").select("url").eq("url", message_id).execute()
        return len(resp.data) > 0
    except:
        return False

def mark_article_sent(message_id: str, title: str):
    try:
        supabase.table("published_articles").insert({"url": message_id, "title": title}).execute()
    except:
        pass

def extract_source_name(sender: str) -> str:
    mapping = {
        "goodjudgment.com": "GOODJ",
        "metaculus.com": "META",
        "weforum.org": "WEF",
        "bbc.co.uk": "BBCFUTURE",
    }
    for domain, name in mapping.items():
        if domain in sender.lower():
            return name
    return "NEWS"

def fetch_emails():
    logger.info("📧 Проверка почты...")
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(EMAIL_USER, EMAIL_PASSWORD)
        mail.select("inbox")

        # Ищем непрочитанные письма от интересующих отправителей
        _, data = mail.search(None, 'UNSEEN FROM "goodjudgment.com" OR FROM "metaculus.com" OR FROM "weforum.org"')
        email_ids = data[0].split()

        for eid in email_ids:
            _, msg_data = mail.fetch(eid, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])

            subject = msg.get("Subject", "")
            sender = msg.get("From", "")
            message_id = msg.get("Message-ID", str(eid))

            if not subject or is_article_sent(message_id):
                mail.store(eid, '+FLAGS', '\\Seen')  # пометить как прочитанное
                continue

            # Получаем тело письма
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode(errors='ignore')
                        break
                    elif part.get_content_type() == "text/html":
                        body = clean_html(part.get_payload(decode=True).decode(errors='ignore'))
                        break
            else:
                body = msg.get_payload(decode=True).decode(errors='ignore')

            if not body.strip():
                mail.store(eid, '+FLAGS', '\\Seen')
                continue

            # Обрезаем лид
            lead = body.split("\n")[0].split(". ")[0].strip()
            if not lead:
                lead = body[:150] + "..."

            prefix = extract_source_name(sender)
            title_ru = translate(subject)
            lead_ru = translate(lead)

            # Отправка
            for ch in CHANNEL_IDS:
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    json={
                        "chat_id": ch,
                        "text": f"<b>{prefix}</b>: {title_ru}\n\n{lead_ru}\n\nИсточник: [Email]",
                        "parse_mode": "HTML"
                    }
                )

            mark_article_sent(message_id, subject)
            logger.info(f"📤 Отправлено: {subject[:50]}...")
            mail.store(eid, '+FLAGS', '\\Seen')

        mail.close()
        mail.logout()

    except Exception as e:
        logger.exception(f"Ошибка почты: {e}")

# === HTTP-сервер для Render ===
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_http():
    server = HTTPServer(("", PORT), Handler)
    server.serve_forever()

# === Запуск ===
if __name__ == "__main__":
    threading.Thread(target=run_http, daemon=True).start()
    fetch_emails()
    schedule.every(10).minutes.do(fetch_emails)  # проверять почту каждые 10 мин

    while True:
        schedule.run_pending()
        time.sleep(60)
