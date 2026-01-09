#!/usr/bin/env python3
import json
import mysql.connector
from elasticsearch import Elasticsearch, helpers
import time
import logging
import requests
import sys

# Configuration
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': 'password',
    'database': 'data-analysis',
    'port': 3306
}

ES_HOST = "http://localhost:9200"
INDEX_NAME = "emails"
BATCH_SIZE = 1000  # Optimal pour 1M de documents

# Configuration propre du logging pour nohup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    force=True,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


def get_db_connection():
    # Ajout de charset pour supporter les emojis/caractères spéciaux des mails
    conn = mysql.connector.connect(**DB_CONFIG, charset='utf8mb4', collation='utf8mb4_unicode_ci')
    # Augmenter la limite de GROUP_CONCAT pour les mails avec bcp de destinataires
    cursor = conn.cursor()
    cursor.execute("SET SESSION group_concat_max_len = 1000000;")
    cursor.close()
    return conn

def clean_for_json(text):
    """Nettoie le texte pour éviter les rejets JSON par Elasticsearch."""
    if not text:
        return ""
    if not isinstance(text, str):
        text = str(text)
    # Supprime les caractères de contrôle non imprimables
    return "".join(ch for ch in text if ch.isprintable() or ch in "\n\r\t")


def set_es_settings_direct(interval):
    """Désactive le refresh pour accélérer l'import massif."""
    url = f"{ES_HOST}/{INDEX_NAME}/_settings"
    payload = {"index.refresh_interval": interval}
    try:
        # On utilise requests pour être sûr de ne pas avoir de conflit de header de version
        r = requests.put(url, json=payload, timeout=15)
        if r.status_code == 200:
            logger.info(f"Paramètre refresh_interval réglé sur : {interval}")
        else:
            logger.warning(f"Réglage ES échoué : {r.status_code}")
    except Exception as e:
        logger.warning(f"Erreur réglage direct : {e}")


def fetch_emails_generator(cursor):
    """
    Récupère l'email complet.
    L'ORDER BY e.id permet à MySQL de streamer plus efficacement sans créer de table temporaire massive.
    """
    query = """
        SELECT
            e.id,
            e.sent_at,
            e.subject,
            i.email_address as sender,
            i.name as send_name,
            e.normalized_body,
            GROUP_CONCAT(ri.email_address SEPARATOR ',') as recipients,
            GROUP_CONCAT(ri.name SEPARATOR ',') as recipient_names,
            e.tone_flags,
            e.interesting
        FROM email e
        LEFT JOIN identity i ON e.sender_identity_id = i.id
        LEFT JOIN email_recipient er ON e.id = er.email_id
        LEFT JOIN identity ri ON er.identity_id = ri.id
        GROUP BY e.id
        ORDER BY e.id ASC
    """
    cursor.execute(query)

    while True:
        # BATCH_SIZE réduit à 500 pour éviter de saturer la RAM si les corps de mails sont gros
        rows = cursor.fetchmany(500)
        if not rows:
            break
        for row in rows:
            try:
                # Validation minimale de la date
                sent_at_iso = None
                if row[1]:
                    try:
                        sent_at_iso = row[1].isoformat()
                    except:
                        pass

                # Parse tone_flags JSON
                tone_flags = []
                if row[8]:
                    try:
                        tone_flags = json.loads(row[8]) if isinstance(row[8], str) else row[8]
                        # S'assurer que c'est une liste
                        if not isinstance(tone_flags, list):
                            tone_flags = []
                    except (json.JSONDecodeError, TypeError):
                        tone_flags = []

                yield {
                    "_index": INDEX_NAME,
                    "_id": str(row[0]),
                    "_source": {
                        "email_id": row[0],
                        "sent_at": sent_at_iso,
                        "subject": clean_for_json(row[2]),
                        "sender": row[3] if row[3] else "unknown",
                        "sender_name": clean_for_json(row[4]),
                        "body": clean_for_json(row[5]),
                        "recipients": row[6].split(',') if row[6] else [],
                        "recipient_names": row[7].split(',') if row[7] else [],
                        "tone_flags": tone_flags,
                        "interesting": bool(row[9])
                    }
                }
            except Exception as e:
                logger.error(f"Erreur préparation doc {row[0]}: {e}")

def main():
    # Initialisation du client avec headers de compatibilité forcés
    es = Elasticsearch(
        ES_HOST,
        verify_certs=False,
        request_timeout=300,
        headers={"Accept": "application/vnd.elasticsearch+json; compatible-with=8"}
    )

    db_conn = None
    cursor = None

    try:
        db_conn = get_db_connection()
        cursor = db_conn.cursor(buffered=True)

        # 1. Préparation
        set_es_settings_direct("-1")

        start_time = time.time()
        logger.info(f"Lancement de l'indexation vers l'index '{INDEX_NAME}'...")

        # 2. Importation Bulk avec suivi
        indexed_count = 0
        error_count = 0
        chunk_start_time = time.time()

        # helpers.streaming_bulk permet de suivre le compte au fur et à mesure
        for ok, item in helpers.streaming_bulk(
                es,
                fetch_emails_generator(cursor),
                chunk_size=BATCH_SIZE,
                request_timeout=300,
                raise_on_error=False
        ):
            if ok:
                indexed_count += 1
            else:
                error_count += 1
                logger.error(f"Erreur sur un document : {item}")

            if indexed_count > 0 and indexed_count % 10000 == 0:
                elapsed = time.time() - chunk_start_time
                speed = 10000 / elapsed if elapsed > 0 else 0
                logger.info(f"Progression : {indexed_count} emails indexés... ({speed:.0f} docs/sec)")
                sys.stdout.flush()  # Indispensable pour voir les logs dans elastic.log en temps réel
                chunk_start_time = time.time()

        # 3. Finalisation
        set_es_settings_direct("1s")
        logger.info("Optimisation de l'index (Force Merge)...")
        try:
            requests.post(f"{ES_HOST}/{INDEX_NAME}/_forcemerge?max_num_segments=1", timeout=600)
        except Exception as e:
            logger.warning(f"Force merge non terminé ou erreur : {e}")

        end_time = time.time()
        duration = end_time - start_time
        logger.info(f"Terminé !")
        logger.info(f"Documents indexés avec succès : {indexed_count}")
        logger.info(f"Temps total : {duration:.2f}s (Moyenne : {indexed_count / duration:.0f} docs/sec)")

        if error_count > 0:
            logger.warning(f"Nombre d'échecs (documents ignorés) : {error_count}")

    except Exception as e:
        logger.error(f"Erreur critique : {e}")
        set_es_settings_direct("1s")
    finally:
        if cursor: cursor.close()
        if db_conn: db_conn.close()


if __name__ == "__main__":
    main()
