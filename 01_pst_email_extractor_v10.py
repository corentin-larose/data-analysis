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
import multiprocessing
import warnings
from pathlib import Path
from datetime import datetime
from email.header import decode_header
from email.utils import parsedate_to_datetime
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning, MarkupResemblesLocatorWarning

# --- FILTRAGE DES WARNINGS ---
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)

# --- CONFIGURATION ---
BASE_DIR = Path(__file__).resolve().parent
config = configparser.ConfigParser()
config.read(BASE_DIR / 'config/config.ini')

db_conf = config['database']
DB_URL_TEMPLATE = f"mysql+pymysql://{db_conf['user']}:{db_conf['password']}@{db_conf['host']}:{db_conf['port']}/{db_conf['database']}?charset=utf8mb4"
ATTACH_DIR = (BASE_DIR / config['storage'].get('attachments_dir', './data/attachments')).resolve()

ALLOWED_EMAILS = set(addr.strip().lower() for addr in config.get('filtering', 'allowed_emails', fallback='').split(',') if addr.strip())

IGNORE_SUBJECT_PREFIXES = (
    "lu :", "read:", "accusé de réception", "disposition notification",
    "accepté :", "accepted:", "refusé :", "declined:", "provisoire :", "tentative:",
    "mis à jour :", "updated:", "réponse automatique", "automatic reply",
    "out of office", "absence de bureau", "undeliverable:", "échec de remise"
)

EMAIL_PATTERN = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
IDENTITY_CACHE = {}

def get_session():
    engine = create_engine(DB_URL_TEMPLATE, pool_pre_ping=True)
    return sessionmaker(bind=engine)()

def decode_header_value(value):
    if not value: return ""
    try:
        decoded_parts = decode_header(str(value))
        result = []
        for content, charset in decoded_parts:
            if isinstance(content, bytes):
                result.append(content.decode(charset or 'utf-8', errors='replace'))
            else:
                result.append(str(content))
        return "".join(result).strip()
    except: return str(value).strip()

def extract_emails_from_string(raw_string):
    if not raw_string: return []
    return list(set(EMAIL_PATTERN.findall(raw_string.lower())))

def get_or_create_identity(email_address, session):
    """Récupère ou crée une identité avec cache local au processus."""
    if not email_address: return None
    if email_address in IDENTITY_CACHE: return IDENTITY_CACHE[email_address]

    res = session.execute(text("SELECT id FROM identity WHERE email_address = :e"), {"e": email_address}).fetchone()
    if res:
        IDENTITY_CACHE[email_address] = res[0]
        return res[0]

    try:
        session.execute(text("INSERT IGNORE INTO identity (email_address) VALUES (:e)"), {"e": email_address})
        session.commit()
        res = session.execute(text("SELECT id FROM identity WHERE email_address = :e"), {"e": email_address}).fetchone()
        if res:
            IDENTITY_CACHE[email_address] = res[0]
            return res[0]
    except Exception:
        session.rollback()
    return None

def decode_body_part(part):
    charset = part.get_content_charset() or 'iso-8859-1'
    payload = part.get_payload(decode=True)
    if not payload: return b"", ""
    try:
        return payload, payload.decode(charset, errors='replace')
    except:
        return payload, payload.decode('utf-8', errors='replace')

def normalize_body(html_or_text):
    if not html_or_text: return ""
    try:
        soup = BeautifulSoup(html_or_text, "lxml")
        return " ".join(soup.get_text(separator=' ').split())
    except: return str(html_or_text).strip()

def should_ignore_message(msg, subject):
    subj_lower = subject.lower()
    if subj_lower.startswith(IGNORE_SUBJECT_PREFIXES): return True
    if msg.get_content_type() == "text/calendar": return True
    return False

def process_mbox_file(mbox_path, folder_path, mailbox_id, session):
    messages_indexed = 0
    if mbox_path.stat().st_size == 0: return 0

    mbox = mailbox.mbox(mbox_path)
    for msg in mbox:
        email_id = None
        try:
            subject = decode_header_value(msg['subject'])
            if should_ignore_message(msg, subject): continue

            # 1. Metadata
            msg_id = decode_header_value(msg.get('Message-ID')).strip()
            in_reply_to = decode_header_value(msg.get('In-Reply-To')).strip()
            references = decode_header_value(msg.get('References')).strip()

            raw_sender = decode_header_value(msg['from'])
            sender_emails = extract_emails_from_string(raw_sender)
            sender_email = sender_emails[0] if sender_emails else ""

            raw_rec = decode_header_value(msg['to'] or "") + " " + decode_header_value(msg['cc'] or "")
            all_rec_list = sorted(extract_emails_from_string(raw_rec))

            # --- FILTRAGE DES ADRESSES ---
            # 1. Exclusion des adresses types "no-reply"
            sender_lower = sender_email.lower()
            if any(x in sender_lower for x in ["no-reply", "noreply", "ne-pas-repondre"]):
                continue

            # 2. Logique liste blanche / adresses vides
            is_sender_allowed = sender_email in ALLOWED_EMAILS
            any_rec_allowed = any(email in ALLOWED_EMAILS for email in all_rec_list)

            if not ((is_sender_allowed and any_rec_allowed) or
                    (not sender_email and any_rec_allowed) or
                    (is_sender_allowed and not all_rec_list)):
                continue

            try:
                mail_date = parsedate_to_datetime(msg['date'])
                # Protection MySQL (range 1000-01-01 à 9999-12-31)
                if mail_date.year < 1000 or mail_date.year > 9999:
                    mail_date = datetime.now()
            except:
                mail_date = datetime.now()

            # 2. Extraction
            body_bytes, body_str = b"", ""
            attachments_found = []
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type == "text/calendar": continue
                if not body_str and content_type in ["text/plain", "text/html"]:
                    body_bytes, body_str = decode_body_part(part)
                elif part.get_filename():
                    attachments_found.append(part)

            norm_body = normalize_body(body_str)
            finger_data = f"{sender_email}|{'|'.join(all_rec_list)}|{subject}|{norm_body}"
            fingerprint = hashlib.sha256(finger_data.encode('utf-8', errors='replace')).hexdigest()

            # 3. DB Insertion
            res_email = session.execute(text("SELECT id FROM email WHERE message_fingerprint = :f"), {"f": fingerprint}).fetchone()
            if not res_email:
                try:
                    sender_id = get_or_create_identity(sender_email, session)
                    raw_body_b64 = base64.b64encode(body_bytes).decode('ascii')
                    res_ins = session.execute(text("""
                        INSERT INTO email (message_fingerprint, subject, sender_identity_id, sent_at,
                                         raw_body, normalized_body, cleaned_body,
                                         has_attachments, message_id, in_reply_to, thread_references)
                        VALUES (:f, :s, :sid, :dt, :rb, :nb, :cb, :ha, :mid, :irt, :ref)
                    """), {
                        "f": fingerprint, "s": subject, "sid": sender_id, "dt": mail_date,
                        "rb": raw_body_b64, "nb": norm_body, "cb": norm_body,
                        "ha": len(attachments_found) > 0, "mid": msg_id, "irt": in_reply_to, "ref": references
                    })
                    email_id = res_ins.lastrowid

                    for email_addr in all_rec_list:
                        rid = get_or_create_identity(email_addr, session)
                        session.execute(text("INSERT IGNORE INTO email_recipient (email_id, identity_id) VALUES (:eid, :rid)"), {"eid": email_id, "rid": rid})

                    for part in attachments_found:
                        decoded_fn = decode_header_value(part.get_filename())
                        if len(decoded_fn) > 200: decoded_fn = decoded_fn[:190] + Path(decoded_fn).suffix
                        ext = Path(decoded_fn).suffix.lower()

                        content = part.get_payload(decode=True)
                        if content:
                            f_hash = hashlib.sha256(content).hexdigest()
                            shard_prefix = Path(f_hash[:2]) / f_hash[2:4]
                            (ATTACH_DIR / shard_prefix).mkdir(parents=True, exist_ok=True)
                            if not (ATTACH_DIR / shard_prefix / f_hash).exists():
                                with open(ATTACH_DIR / shard_prefix / f_hash, "wb") as f: f.write(content)
                            session.execute(text("""
                                INSERT INTO attachment (email_id, filename, size, storage_path, extension, mime_type_declared)
                                VALUES (:eid, :fn, :sz, :sp, :ex, :mime)
                            """), {"eid": email_id, "fn": decoded_fn, "sz": len(content), "sp": str(shard_prefix / f_hash), "ex": ext[:20], "mime": part.get_content_type()})

                except Exception as e_ins:
                    session.rollback()
                    if "Duplicate entry" in str(e_ins):
                        res_retry = session.execute(text("SELECT id FROM email WHERE message_fingerprint = :f"), {"f": fingerprint}).fetchone()
                        email_id = res_retry[0] if res_retry else None
                    else: continue
            else:
                email_id = res_email[0]

            if email_id:
                session.execute(text("INSERT IGNORE INTO email_mailbox (email_id, mailbox_id, folder_path) VALUES (:eid, :mid, :fp)"),
                                {"eid": email_id, "mid": mailbox_id, "fp": folder_path})

            messages_indexed += 1
            if messages_indexed % 1000 == 0:
                session.commit()
                print(f"      [{mbox_path.name}] {messages_indexed} items processed...")
        except Exception as e:
            session.rollback()
            print(f"   ⚠️ Error processing message: {e}")

    session.commit()
    return messages_indexed

def worker_process_pst(pst_path):
    session = get_session()
    temp_dir = BASE_DIR / f"data/temp_{pst_path.stem}_{os.getpid()}"
    if temp_dir.exists(): shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True)
    try:
        print(f"🚀 Processing: {pst_path.name}")
        abs_pst = str(pst_path.absolute())
        res = session.execute(text("SELECT id FROM mailbox WHERE pst_filename = :f"), {"f": abs_pst}).fetchone()
        mailbox_id = res[0] if res else session.execute(text("INSERT INTO mailbox (owner_identifier, pst_filename) VALUES (:o, :f)"),
                                                         {"o": pst_path.stem, "f": abs_pst}).lastrowid
        session.commit()
        subprocess.run(["readpst", "-S", "-M", "-e", "-r", "-o", str(temp_dir), abs_pst], check=False, capture_output=True)
        total = sum(process_mbox_file(f, str(f.parent.relative_to(temp_dir)), mailbox_id, session) for f in temp_dir.rglob("*") if f.is_file() and not f.name.startswith('.'))
        print(f"✅ Finished {pst_path.name}: {total} emails.")
    finally:
        if temp_dir.exists(): shutil.rmtree(temp_dir)
        session.close()

def main(source_dir):
    ATTACH_DIR.mkdir(parents=True, exist_ok=True)
    pst_files = sorted(list(Path(source_dir).rglob("*.pst")))
    if not pst_files: return
    print(f"Starting pipeline with {multiprocessing.cpu_count()} workers.")
    with multiprocessing.Pool(processes=multiprocessing.cpu_count()) as pool:
        pool.map(worker_process_pst, pst_files)

if __name__ == "__main__":
    if len(sys.argv) < 2: sys.exit(1)
    main(sys.argv[1])
