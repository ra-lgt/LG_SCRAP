import imaplib
import email
from email.header import decode_header
from bs4 import BeautifulSoup   # <-- install: pip install beautifulsoup4
import re

IMAP_SERVER = "imap.gmail.com"
EMAIL = "asm.electronics.pune@gmail.com"
PASSWORD = "uxfhzpavjlmmchov"  # Gmail App Password



def delete_mail_by_id(mail, msg_id):
    """
    Marks an email as deleted and expunges it from Gmail.
    """
    try:
        # Mark email as deleted
        mail.store(msg_id, '+FLAGS', '\\Deleted')

        # Permanently remove deleted emails
        mail.expunge()

        print(f"🗑️ Deleted email ID: {msg_id}")

    except Exception as e:
        print("Error deleting mail:", e)


def extract_otp(body):
    """Extracts any OTP number that has minimum 3 digits (3 or more)."""
    match = re.search(r'\b(\d{3,})\b', body)
    return match.group(1) if match else None

def get_latest_mail_from():
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL, PASSWORD)
        mail.select("inbox")

        status, messages = mail.search(None, f'(FROM "{EMAIL}")')

        if status != "OK":
            return None

        email_ids = messages[0].split()
        if not email_ids:
            return None

        latest_id = email_ids[-1]

        status, msg_data = mail.fetch(latest_id, "(RFC822)")
        msg = email.message_from_bytes(msg_data[0][1])

        # Decode subject
        subject, encoding = decode_header(msg["Subject"])[0]
        if isinstance(subject, bytes):
            subject = subject.decode(encoding or "utf-8")

        sender = msg.get("From")

        # Extract body (supports both text/plain & text/html)
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                if ctype == "text/plain":
                    body = part.get_payload(decode=True).decode(errors="ignore")
                    break
                if ctype == "text/html":
                    html = part.get_payload(decode=True).decode(errors="ignore")
                    soup = BeautifulSoup(html, "html.parser")
                    body = soup.get_text(separator="\n").strip()
        else:
            ctype = msg.get_content_type()
            if ctype == "text/plain":
                body = msg.get_payload(decode=True).decode(errors="ignore")
            elif ctype == "text/html":
                html = msg.get_payload(decode=True).decode(errors="ignore")
                soup = BeautifulSoup(html, "html.parser")
                body = soup.get_text(separator="\n").strip()

        return {
            "subject": subject,
            "from": sender,
            "body": body.strip(),
            "latest_id":latest_id
        }

    except Exception as e:
        print("Error:", e)
        return None


# msg = get_latest_mail_from("LG_GRADE_A_SALES@lge.com")
# print(msg["body"])
# print(extract_otp(msg["body"]))