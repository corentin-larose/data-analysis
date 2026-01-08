#!/usr/bin/env python3
import mysql.connector
from elasticsearch import Elasticsearch, helpers
import time
import logging
import requests

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
BATCH_SIZE = 100  # Augmentation du lot pour la performance

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def get_db_connection():
    conn = mysql.connector.connect(**DB_CONFIG)
    # Augmenter la limite de GROUP_CONCAT pour les mails avec bcp de destinataires
    cursor = conn.cursor()
    cursor.execute("SET SESSION group_concat_max_len = 1000000;")
    cursor.close()
    return conn

def clean_for_json(text):
    """Supprime les caractères de contrôle non autorisés en JSON/ES."""
    if not text:
        return ""
    if not isinstance(text, str):
        text = str(text)
    # Supprime les caractères de contrôle sauf saut de ligne et tabulation
    return "".join(ch for ch in text if ch.isprintable() or ch in "\n\r\t")

def set_es_settings_direct(interval):
    """Utilise requests pour contourner les erreurs 400 du client v9 sur ES 8"""
    url = f"{ES_HOST}/{INDEX_NAME}/_settings"
    payload = {"index.refresh_interval": interval}
    try:
        r = requests.put(url, json=payload, timeout=10)
        if r.status_code == 200:
            logger.info(f"Paramètre refresh_interval réglé sur {interval}")
        else:
            logger.warning(f"Réglage ES via requests échoué: {r.status_code} {r.text}")
    except Exception as e:
        logger.warning(f"Erreur lors du réglage direct: {e}")

def fetch_emails_generator(cursor):
    """
    Récupère uniquement l'ID, le sujet et le corps nettoyé.
    Aucune jointure pour une performance maximale sur 1M+ de lignes.
    """
    query = "SELECT id, subject, cleaned_body FROM email"
    cursor.execute(query)

    while True:
        rows = cursor.fetchmany(BATCH_SIZE)
        if not rows:
            break
        for row in rows:
            try:
                yield {
                    "_index": INDEX_NAME,
                    "_id": str(row[0]),
                    "_source": {
                        "email_id": row[0],
                        "subject": clean_for_json(row[1]),
                        "body": clean_for_json(row[2])
                    }
                }
            except Exception as e:
                logger.error(f"Erreur preparation ID {row[0]}: {e}")

def main():
    # Client configuré pour être le plus permissif possible
    es = Elasticsearch(
        ES_HOST,
        verify_certs=False,
        request_timeout=120
    )

    db_conn = None
    cursor = None

    try:
        db_conn = get_db_connection()
        cursor = db_conn.cursor(buffered=True)

        # 1. Optimisation avant import (via requests pour éviter les erreurs 400 de compatibilité)
        set_es_settings_direct("-1")

        start_time = time.time()
        logger.info("Début de l'importation massive...")

        # 2. Importation par lots via helpers.bulk
        # raise_on_error=False permet de ne pas stopper tout l'import si un mail pose problème
        success_count, errors = helpers.bulk(
            es,
            fetch_emails_generator(cursor),
            chunk_size=BATCH_SIZE,
            request_timeout=300,
            raise_on_error=False
        )

        # 3. Rétablir les réglages et finaliser
        set_es_settings_direct("1s")

        logger.info("Lancement du Force Merge (optimisation finale)...")
        try:
            requests.post(f"{ES_HOST}/{INDEX_NAME}/_forcemerge", timeout=600)
        except:
            pass

        end_time = time.time()
        duration = end_time - start_time

        logger.info(f"Importation terminée !")
        logger.info(f"Emails indexés avec succès : {success_count}")
        logger.info(f"Temps écoulé : {duration:.2f} secondes")
        if errors:
            logger.warning(f"Nombre de documents en échec : {len(errors)}")

    except Exception as e:
        logger.error(f"Une erreur critique est survenue : {e}")
        set_es_settings_direct("1s")
    finally:
        if cursor:
            cursor.close()
        if db_conn:
            db_conn.close()

if __name__ == "__main__":
    main()