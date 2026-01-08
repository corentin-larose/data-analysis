#!/usr/bin/env python3
import pypff
import configparser
import re
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# --- CONFIGURATION ---
BASE_DIR = Path(__file__).resolve().parent
config = configparser.ConfigParser()
config.read(BASE_DIR / 'config/config.ini')

db_conf = config['database']
DB_URL = f"mysql+pymysql://{db_conf['user']}:{db_conf['password']}@{db_conf['host']}:{db_conf['port']}/{db_conf['database']}?charset=utf8mb4"

# Regex pour capturer les adresses emails
EMAIL_REGEX = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')

def get_session():
    engine = create_engine(DB_URL)
    return sessionmaker(bind=engine)()

def process_pst(pst_path, session):
    pst = pypff.file()
    pst.open(str(pst_path))
    root = pst.get_root_folder()

    # Set pour dédupliquer au sein d'un même PST avant insertion
    email_buffer = set()
    total_processed = 0

    def walk_folders(folder):
        nonlocal total_processed
        for message in folder.sub_messages:
            try:
                # Extraction rapide
                raw_content = f"{message.sender_name} {message.get_transport_headers() or ''}"
                found_emails = EMAIL_REGEX.findall(raw_content.lower())

                for email_addr in found_emails:
                    email_buffer.add(email_addr)

                total_processed += 1

                # Insertion par paquets de 1000 pour la performance
                if len(email_buffer) >= 1000:
                    flush_buffer(session, email_buffer)

                if total_processed % 500 == 0:
                    print(f"      - {total_processed} messages analysés...", end='\r', flush=True)

            except Exception as e:
                pass # Erreurs mineures ignorées pour la vitesse

        for sub_folder in folder.sub_folders:
            walk_folders(sub_folder)

    def flush_buffer(sess, buffer):
        if not buffer: return
        # On transforme le set en liste de dictionnaires pour SQLAlchemy
        data = [{"email": e} for e in buffer]
        sess.execute(
            text("INSERT IGNORE INTO email_addresses (email) VALUES (:email)"),
            data
        )
        sess.commit()
        buffer.clear()

    walk_folders(root)
    flush_buffer(session, email_buffer) # Dernier paquet
    print(f"\n   ✅ Total PST : {total_processed} messages.")
    pst.close()

def main():
    session = get_session()
    source_path = BASE_DIR / "data"

    print(f"📂 Recherche dans : {source_path.absolute()}", flush=True)
    pst_files = list(source_path.rglob("*.pst"))
    print(f"🔍 {len(pst_files)} fichier(s) PST trouvé(s).", flush=True)

    for pst_file in pst_files:
        print(f"🚀 Traitement : {pst_file.name}", flush=True)
        try:
            process_pst(pst_file, session)
        except Exception as e:
            print(f"❌ Erreur critique sur {pst_file.name}: {e}", flush=True)
            session.rollback()

    session.close()

if __name__ == "__main__":
    main()