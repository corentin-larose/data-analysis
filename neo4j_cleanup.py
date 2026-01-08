#!/usr/bin/env python3
from neo4j import GraphDatabase
import configparser
import os
import sys

# --- Configuration ---
config = configparser.ConfigParser()
config_path = os.path.join(os.path.dirname(__file__), 'config', 'config.ini')
config.read(config_path)

try:
    NEO4J_URI = config['neo4j']['uri']
    NEO4J_USER = config['neo4j']['user']
    NEO4J_PASSWORD = config['neo4j']['password']
except KeyError as e:
    print(f"❌ Erreur de configuration : Clé {e} manquante dans config.ini")
    sys.exit(1)

def cleanup_database():
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    
    print(f"🧹 Connexion à Neo4j ({NEO4J_URI})...")
    
    try:
        with driver.session() as session:
            # 1. Suppression des données
            print("🗑️  Suppression des nœuds et relations...")
            result = session.run("MATCH (n) DETACH DELETE n RETURN count(n) as deleted_count")
            deleted_nodes = result.single()["deleted_count"]
            
            # 2. Optionnel : Suppression des contraintes (pour repartir vraiment à zéro)
            print("⚙️  Nettoyage des contraintes...")
            constraints = session.run("SHOW CONSTRAINTS")
            for record in constraints:
                # Sur les versions récentes de Neo4j, on utilise DROP CONSTRAINT nom
                session.run(f"DROP CONSTRAINT {record['name']}")

            print(f"✅ Nettoyage terminé. {deleted_nodes} nœuds supprimés.")
            
    except Exception as e:
        print(f"❌ Erreur lors du nettoyage : {e}")
    finally:
        driver.close()

if __name__ == "__main__":
    confirm = input("⚠️  Êtes-vous sûr de vouloir supprimer TOUTES les données de Neo4j ? (y/N) : ")
    if confirm.lower() == 'y':
        cleanup_database()
    else:
        print("Annulé.")
