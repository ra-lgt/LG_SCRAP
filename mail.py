import imaplib
import email
from email.header import decode_header
from bs4 import BeautifulSoup
import re

IMAP_SERVER = "imap.gmail.com"
EMAIL = "asm.electronics.pune@gmail.com"
PASSWORD = "uxfhzpavjlmmchov"  # Gmail App Password


def connect_mail():
    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    mail.login(EMAIL, PASSWORD)
    mail.select("inbox")
    return mail


def delete_mail_by_id(mail, msg_id):
    """Delete email safely using same IMAP connection."""
    try:
        mail.store(msg_id, "+FLAGS", "\\Deleted")
        mail.expunge()
        print(f"🗑️ Deleted email ID: {msg_id.decode()}")
    except Exception as e:
        print("❌ Error deleting mail:", e)


def extract_otp(body):
    """Extract OTP with at least 3 digits."""
    match = re.search(r"\b(\d{3,})\b", body)
    return match.group(1) if match else None


def get_latest_mail_from(sender_email):
    """Return latest email only if the subject contains 'OTP Validation'."""
    try:
        mail = connect_mail()

        status, messages = mail.search(None, f'(FROM "{sender_email}")')
        if status != "OK":
            return None

        email_ids = messages[0].split()
        if not email_ids:
            return None

        latest_id = email_ids[-1]

        # Fetch mail data
        status, msg_data = mail.fetch(latest_id, "(RFC822)")
        msg = email.message_from_bytes(msg_data[0][1])

        # Decode subject
        subject_raw = decode_header(msg["Subject"])[0]
        subject = (
            subject_raw[0].decode(subject_raw[1] or "utf-8")
            if isinstance(subject_raw[0], bytes)
            else subject_raw[0]
        )

        # ⭐ FILTER: Subject must contain "OTP Validation"
        if "otp validation" not in subject.lower():
            mail.logout()
            return None

        sender = msg.get("From")

        # Extract body (HTML or plain text)
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                if ct == "text/plain":
                    body = part.get_payload(decode=True).decode(errors="ignore")
                    break
                if ct == "text/html":
                    html = part.get_payload(decode=True).decode(errors="ignore")
                    soup = BeautifulSoup(html, "html.parser")
                    body = soup.get_text("\n").strip()
        else:
            ct = msg.get_content_type()
            if ct == "text/plain":
                body = msg.get_payload(decode=True).decode(errors="ignore")
            elif ct == "text/html":
                html = msg.get_payload(decode=True).decode(errors="ignore")
                soup = BeautifulSoup(html, "html.parser")
                body = soup.get_text("\n").strip()

        return {
            "subject": subject,
            "from": sender,
            "body": body.strip(),
            "latest_id": latest_id,
            "mail": mail,  # KEEP MAIL OPEN → allow deletion
        }

    except Exception as e:
        print("❌ Error:", e)
        return None
