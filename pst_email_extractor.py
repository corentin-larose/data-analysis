#!/usr/bin/env python3
import os
import sys
import hashlib
import configparser
import subprocess
import shutil
import mailbox
import re
import base64
from pathlib import Path
from datetime import datetime
from email.header import decode_header
from email.utils import parsedate_to_datetime
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from bs4 import BeautifulSoup

# --- CONFIGURATION ---
config = configparser.ConfigParser()
BASE_DIR = Path(__file__).resolve().parent
config.read(BASE_DIR / 'config/config.ini')

db_conf = config['database']
DB_URL = f"mysql+pymysql://{db_conf['user']}:{db_conf['password']}@{db_conf['host']}:{db_conf['port']}/{db_conf['database']}?charset=utf8mb4"
ATTACH_DIR = (BASE_DIR / config['storage']['attachments_dir']).resolve()

engine = create_engine(DB_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)

EMAIL_PATTERN = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')

def decode_header_value(value):
    """Decodes MIME encoded headers (RFC 2047) safely."""
    if not value: return ""
    try:
        decoded_parts = decode_header(value)
        result = []
        for content, charset in decoded_parts:
            if isinstance(content, bytes):
                result.append(content.decode(charset or 'utf-8', errors='replace'))
            else:
                result.append(str(content))
        return "".join(result)
    except Exception: return str(value)

def extract_emails_from_string(raw_string):
    """Extracts unique lowercase emails from any string."""
    if not raw_string: return []
    matches = EMAIL_PATTERN.findall(raw_string.lower())
    return list(set(matches))

def get_or_create_identity(email_address, session):
    """Retrieves or creates an identity in the DB."""
    if not email_address: return None
    res = session.execute(text("SELECT id FROM identity WHERE email_address = :e"), {"e": email_address}).fetchone()
    if res: return res[0]
    res = session.execute(text("INSERT INTO identity (email_address) VALUES (:e)"), {"e": email_address})
    return res.lastrowid

def decode_body_part(part):
    """Intelligently decodes a message part using its declared charset."""
    charset = part.get_content_charset() or 'iso-8859-1'
    payload = part.get_payload(decode=True)
    if not payload: return b"", ""
    try:
        return payload, payload.decode(charset, errors='replace')
    except Exception:
        for enc in ['windows-1252', 'utf-8']:
            try: return payload, payload.decode(enc, errors='replace')
            except: continue
    return payload, payload.decode('utf-8', errors='replace')

def normalize_body(html_or_text):
    if not html_or_text: return ""
    try:
        soup = BeautifulSoup(html_or_text, "lxml")
        return " ".join(soup.get_text(separator=' ').split())
    except Exception: return str(html_or_text)

def process_mbox_file(mbox_path, mailbox_id, session):
    messages_indexed = 0
    try:
        if mbox_path.stat().st_size == 0: return 0
        mbox = mailbox.mbox(mbox_path)
        for msg in mbox:
            # 1. Metadata
            subject = decode_header_value(msg['subject'])
            sender_emails = extract_emails_from_string(decode_header_value(msg['from']))
            sender_email = sender_emails[0] if sender_emails else ""

            raw_rec = decode_header_value(msg['to'] or "") + " " + decode_header_value(msg['cc'] or "")
            all_rec_list = sorted(extract_emails_from_string(raw_rec))

            mail_date = datetime.now()
            try: mail_date = parsedate_to_datetime(msg['date'])
            except: pass

            # 2. Body & Encoding
            body_bytes, body_str = b"", ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() in ["text/plain", "text/html"]:
                        body_bytes, body_str = decode_body_part(part)
                        if body_str: break
            else:
                body_bytes, body_str = decode_body_part(msg)

            raw_body_b64 = base64.b64encode(body_bytes).decode('ascii')
            norm_body = normalize_body(body_str)

            # 3. Fingerprint
            finger_data = f"{sender_email}|{'|'.join(all_rec_list)}|{subject}|{norm_body}"
            fingerprint = hashlib.sha256(finger_data.encode('utf-8')).hexdigest()

            # 4. DB Check & Insert
            existing = session.execute(text("SELECT id FROM email WHERE message_fingerprint = :f"), {"f": fingerprint}).fetchone()
            if not existing:
                sender_id = get_or_create_identity(sender_email, session) if sender_email else None
                has_attach = any(p.get_filename() for p in msg.walk() if not p.is_multipart())

                res = session.execute(text("""
                    INSERT INTO email (message_fingerprint, subject, sender_identity_id, sent_at, raw_body, normalized_body, has_attachments)
                    VALUES (:f, :s, :sid, :dt, :rb, :nb, :ha)
                """), {"f": fingerprint, "s": subject, "sid": sender_id, "dt": mail_date, "rb": raw_body_b64, "nb": norm_body, "ha": has_attach})
                email_id = res.lastrowid

                for email_addr in all_rec_list:
                    rid = get_or_create_identity(email_addr, session)
                    session.execute(text("INSERT IGNORE INTO email_recipient (email_id, identity_id) VALUES (:eid, :rid)"), {"eid": email_id, "rid": rid})

                # 5. Attachments (CAS storage)
                for part in msg.walk():
                    filename = part.get_filename()
                    if filename:
                        decoded_fn = decode_header_value(filename)
                        content = part.get_payload(decode=True)
                        if content:
                            f_hash = hashlib.sha256(content).hexdigest()
                            f_path = ATTACH_DIR / f_hash
                            if not f_path.exists():
                                with open(f_path, "wb") as f: f.write(content)
                            session.execute(text("INSERT INTO attachment (email_id, filename, size, storage_path) VALUES (:eid, :fn, :sz, :sp)"),
                                            {"eid": email_id, "fn": decoded_fn, "sz": len(content), "sp": str(f_path)})
            else:
                email_id = existing[0]

            session.execute(text("INSERT IGNORE INTO email_mailbox (email_id, mailbox_id) VALUES (:eid, :mid)"), {"eid": email_id, "mid": mailbox_id})
            messages_indexed += 1
            if messages_indexed % 50 == 0: session.commit()
        return messages_indexed
    except Exception as e:
        print(f"   ❌ Error: {e}"); return 0

def main(source_dir):
    ATTACH_DIR.mkdir(parents=True, exist_ok=True)
    session = SessionLocal()
    temp_dir = BASE_DIR / "temp_extract"
    if temp_dir.exists(): shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        for pst_path in Path(source_dir).rglob("*.pst"):
            print(f"📂 Processing: {pst_path.name}")
            abs_pst = str(pst_path.absolute())
            res = session.execute(text("SELECT id FROM mailbox WHERE pst_filename = :f"), {"f": abs_pst}).fetchone()
            mailbox_id = res[0] if res else session.execute(text("INSERT INTO mailbox (owner_identifier, pst_filename) VALUES (:o, :f)"), {"o": pst_path.stem, "f": abs_pst}).lastrowid
            session.commit()

            pst_temp = temp_dir / pst_path.stem
            pst_temp.mkdir(parents=True, exist_ok=True)
            subprocess.run(["readpst", "-S", "-M", "-e", "-r", "-o", str(pst_temp), abs_pst], check=False, capture_output=True)

            total = sum(process_mbox_file(f, mailbox_id, session) for f in pst_temp.rglob("*") if f.is_file() and not f.name.startswith('.'))
            session.commit()
            print(f"✅ Finished: {pst_path.name} ({total} emails)")
    finally:
        if temp_dir.exists(): shutil.rmtree(temp_dir)
        session.close()

if __name__ == "__main__":
    if len(sys.argv) < 2: sys.exit(1)
    main(sys.argv[1])