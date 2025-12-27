import mysql.connector
from neo4j import GraphDatabase
import configparser
import os
import sys

# --- Configuration Robuste ---
config = configparser.ConfigParser()
config_path = os.path.join(os.path.dirname(__file__), 'config', 'config.ini')
if not os.path.exists(config_path):
    print(f"Erreur : Fichier de configuration introuvable à {config_path}")
    sys.exit(1)
config.read(config_path)

# Paramètres MariaDB
db_config = {
    'host': config['database']['host'],
    'user': config['database']['user'],
    'password': config['database']['password'],
    'database': config['database']['database'],
    'port': config.getint('database', 'port'),
    'charset': 'utf8mb4' # Crucial pour les emojis/caractères spéciaux en 2025
}

NEO4J_URI = config['neo4j']['uri']
NEO4J_USER = config['neo4j']['user']
NEO4J_PASSWORD = config['neo4j']['password']

class Neo4jExporter:
    def __init__(self, uri, user, password):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self):
        self.driver.close()

    def import_data(self, maria_db_config):
        with self.driver.session() as session:
            print("Initialisation des contraintes Neo4j...")
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (p:Person) REQUIRE p.email IS UNIQUE")

            conn = mysql.connector.connect(**maria_db_config)
            # Utilisation de buffered=True pour éviter les problèmes de curseur sur de gros volumes
            cursor = conn.cursor(dictionary=True, buffered=True)

            print("Extraction des données depuis MariaDB...")
            query = """
                SELECT
                    id_sender.email_address AS sender_email,
                    id_rcpt.email_address AS recipient_email,
                    e.subject,
                    e.sent_at,
                    er.type AS recipient_type
                FROM email e
                JOIN identity id_sender ON e.sender_identity_id = id_sender.id
                JOIN email_recipient er ON e.id = er.email_id
                JOIN identity id_rcpt ON er.identity_id = id_rcpt.id
            """
            cursor.execute(query)

            # --- Optimisation : Batch Processing ---
            batch_size = 500
            batch = []
            count = 0

            for row in cursor:
                # Normalisation de la date pour Neo4j
                if row['sent_at']:
                    row['sent_at'] = row['sent_at'].isoformat()

                batch.append(row)
                count += 1

                if len(batch) >= batch_size:
                    self._process_batch(session, batch)
                    print(f"{count} relations traitées...")
                    batch = []

            # Traitement du dernier lot restant
            if batch:
                self._process_batch(session, batch)

            cursor.close()
            conn.close()
            print(f"Importation terminée. Total : {count} interactions importées.")

    @staticmethod
    def _process_batch(session, batch_data):
        # Utilisation de UNWIND pour une performance maximale (1 seule requête pour 500 lignes)
        query = """
        UNWIND $rows AS row
        MERGE (s:Person {email: row.sender_email})
        MERGE (r:Person {email: row.recipient_email})
        CREATE (s)-[:SENT_MESSAGE {
            subject: row.subject,
            date: datetime(row.sent_at),
            type: row.recipient_type
        }]->(r)
        """
        session.run(query, rows=batch_data)

if __name__ == "__main__":
    exporter = Neo4jExporter(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)
    try:
        exporter.import_data(db_config)
    except Exception as e:
        print(f"Erreur critique lors de l'import : {e}")
    finally:
        exporter.close()