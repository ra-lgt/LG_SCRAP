import imaplib
import email
from email.header import decode_header

# Gmail IMAP server
IMAP_SERVER = "imap.gmail.com"
EMAIL = "asm.electronics.pune@gmail.com"
PASSWORD = "uxfhzpavjlmmchov "  # Not normal password!

# Connect
mail = imaplib.IMAP4_SSL(IMAP_SERVER)
mail.login(EMAIL, PASSWORD)

# Select inbox
mail.select("inbox")

# Search for unread emails
status, messages = mail.search(None, '(UNSEEN)')

email_ids = messages[0].split()

for e_id in email_ids:
    status, msg_data = mail.fetch(e_id, "(RFC822)")
    msg = email.message_from_bytes(msg_data[0][1])

    subject, encoding = decode_header(msg["Subject"])[0]
    if isinstance(subject, bytes):
        subject = subject.decode(encoding or "utf-8")
    
    print("📩 Subject:", subject)
    print("From:", msg.get("From"))

    # If email has text content
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                print("Body:", part.get_payload(decode=True).decode())
    else:
        print("Body:", msg.get_payload(decode=True).decode())
