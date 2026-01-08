"""
Script de test pour valider le nettoyage avant traitement complet
Version optimisée avec gestion des erreurs
"""

import mysql.connector
from mysql.connector import Error
from pattern_detector_v3 import EmailCleaner, DatabaseConfig
from elasticsearch import Elasticsearch
import json
import sys


def test_elasticsearch_connection(es_host: str = "localhost", es_port: int = 9200) -> bool:
    """Test la connexion à Elasticsearch"""
    print("🔌 Test de connexion à Elasticsearch...")
    try:
        es = Elasticsearch(
            [f"http://{es_host}:{es_port}"],
            request_timeout=10
        )

        if es.ping():
            info = es.info()
            print(f"✅ Connecté à Elasticsearch {info['version']['number']}")

            # Vérifier l'index
            try:
                count = es.count(index="emails")
                print(f"📧 {count['count']:,} emails dans l'index 'emails'")
                return True
            except Exception as e:
                print(f"⚠️  Index 'emails' non trouvé ou vide: {e}")
                print("   Assurez-vous d'avoir indexé vos emails")
                return False
        else:
            print("❌ Impossible de contacter Elasticsearch")
            return False

    except Exception as e:
        print(f"❌ Erreur de connexion Elasticsearch: {e}")
        print("   Vérifiez que Elasticsearch est démarré")
        return False


def test_mariadb_connection(db_config: DatabaseConfig) -> bool:
    """Test la connexion à MariaDB"""
    print("\n🔌 Test de connexion à MariaDB...")
    try:
        connection = mysql.connector.connect(
            host=db_config.host,
            port=db_config.port,
            database=db_config.database,
            user=db_config.user,
            password=db_config.password,
            connect_timeout=10
        )

        if connection.is_connected():
            db_info = connection.get_server_info()
            print(f"✅ Connecté à MariaDB version {db_info}")

            cursor = connection.cursor()

            # Compter les emails
            cursor.execute("SELECT COUNT(*) FROM email")
            count = cursor.fetchone()[0]
            print(f"📧 {count:,} emails dans la table 'email'")

            # Vérifier la colonne cleaned_body
            cursor.execute("""
                SELECT COUNT(*) FROM email
                WHERE cleaned_body IS NOT NULL AND cleaned_body != ''
            """)
            cleaned_count = cursor.fetchone()[0]
            print(f"🧹 {cleaned_count:,} emails déjà nettoyés")

            # Vérifier les colonnes disponibles
            cursor.execute("""
                SELECT COUNT(*) FROM email
                WHERE normalized_body IS NOT NULL AND normalized_body != ''
            """)
            normalized_count = cursor.fetchone()[0]

            cursor.execute("""
                SELECT COUNT(*) FROM email
                WHERE raw_body IS NOT NULL AND raw_body != ''
            """)
            raw_count = cursor.fetchone()[0]

            print(f"📝 {normalized_count:,} emails avec normalized_body")
            print(f"📝 {raw_count:,} emails avec raw_body")

            cursor.close()
            connection.close()
            return True

    except Error as e:
        print(f"❌ Erreur de connexion MariaDB: {e}")
        print("\n💡 Suggestions:")
        print("   - Vérifiez que MariaDB est démarré")
        print("   - Vérifiez les credentials (user/password)")
        print("   - Vérifiez le nom de la base de données")
        return False


def preview_cleaning(
    db_config: DatabaseConfig,
    patterns_file: str = "email_patterns.json",
    num_samples: int = 5
):
    """Affiche un aperçu du nettoyage"""
    print(f"\n🔍 Aperçu du nettoyage sur {num_samples} emails\n")
    print("="*80)

    # Charger le cleaner
    try:
        cleaner = EmailCleaner(patterns_file)
        print(f"✅ {len(cleaner.regex_patterns)} patterns de nettoyage chargés\n")
    except Exception as e:
        print(f"⚠️  Impossible de charger {patterns_file}: {e}")
        print("   Utilisation des patterns par défaut uniquement")
        cleaner = EmailCleaner(patterns_file)

    # Connexion à la base
    try:
        connection = mysql.connector.connect(
            host=db_config.host,
            port=db_config.port,
            database=db_config.database,
            user=db_config.user,
            password=db_config.password,
            connect_timeout=10
        )

        cursor = connection.cursor(dictionary=True)

        # Récupérer des emails aléatoires avec du contenu
        query = """
            SELECT id, subject,
                   COALESCE(normalized_body, raw_body) as body,
                   LENGTH(COALESCE(normalized_body, raw_body)) as body_length
            FROM email
            WHERE COALESCE(normalized_body, raw_body) IS NOT NULL
              AND LENGTH(COALESCE(normalized_body, raw_body)) > 100
            ORDER BY RAND()
            LIMIT %s
        """
        cursor.execute(query, (num_samples,))
        emails = cursor.fetchall()

        if not emails:
            print("⚠️  Aucun email trouvé avec du contenu")
            cursor.close()
            connection.close()
            return

        total_saved = 0
        total_original = 0

        for i, email in enumerate(emails, 1):
            subject = email['subject'] or "(sans sujet)"
            print(f"\n📧 Email #{i} (ID: {email['id']})")
            print(f"Subject: {subject[:60]}...")
            print("-" * 80)

            # Nettoyer
            result = cleaner.clean_email_body(email['body'])

            total_original += result['original_length']
            total_saved += result['chars_saved']

            # Statistiques
            print(f"📊 Original:  {result['original_length']:>6} chars (~{result['original_length']//4:>5} tokens)")
            print(f"📊 Nettoyé:   {result['cleaned_length']:>6} chars (~{result['cleaned_length']//4:>5} tokens)")
            print(f"💰 Économie:  {result['chars_saved']:>6} chars (~{result['tokens_saved']:>5} tokens) | {result['reduction_pct']:.1f}%")

            # Extraits
            print(f"\n📄 Avant (200 premiers chars):")
            preview = email['body'][:200].replace('\n', ' ↵ ')
            print(f"   {preview}...")

            print(f"\n✨ Après (200 premiers chars):")
            preview_clean = result['cleaned_body'][:200].replace('\n', ' ↵ ')
            print(f"   {preview_clean}...")

            print("=" * 80)

        cursor.close()
        connection.close()

        # Statistiques globales
        if total_original > 0:
            avg_reduction = (total_saved / total_original) * 100
            print(f"\n📊 STATISTIQUES SUR {num_samples} EMAILS:")
            print(f"   Réduction moyenne:  {avg_reduction:.1f}%")
            print(f"   Chars économisés:   {total_saved:,}")
            print(f"   Tokens économisés:  ~{total_saved // 4:,}")

    except Error as e:
        print(f"❌ Erreur: {e}")


def check_patterns_file(patterns_file: str = "email_patterns.json"):
    """Vérifie le fichier de patterns"""
    print(f"\n📋 Vérification: {patterns_file}\n")

    try:
        with open(patterns_file, 'r', encoding='utf-8') as f:
            patterns = json.load(f)

        print("✅ Fichier chargé avec succès\n")

        categories = [
            ('short_patterns', 'Patterns courts (5 mots)'),
            ('long_patterns', 'Patterns longs (8-10 mots)'),
            ('line_patterns', 'Lignes répétitives'),
            ('disclaimer_patterns', 'Disclaimers'),
            ('regex_patterns', 'Regex générées')
        ]

        total = 0
        for key, label in categories:
            count = len(patterns.get(key, []))
            total += count
            print(f"  • {label}: {count}")

        print(f"\n📦 Total: {total} patterns")

        if total == 0:
            print("\n⚠️  Aucun pattern détecté!")
            print("   Lancez: python pattern_detector.py")
            return False

        # Exemples
        if patterns.get('long_patterns'):
            print("\n📝 Exemples de patterns (top 3):")
            for i, pattern in enumerate(patterns['long_patterns'][:3], 1):
                text = pattern['text'][:70]
                count = pattern.get('doc_count', 0)
                print(f"  {i}. [{count:>4}x] {text}...")

        return True

    except FileNotFoundError:
        print(f"❌ Fichier introuvable: {patterns_file}")
        print("\n💡 Pour générer les patterns:")
        print("   python pattern_detector.py")
        return False
    except json.JSONDecodeError:
        print(f"❌ Fichier JSON invalide: {patterns_file}")
        return False


def estimate_processing_time(db_config: DatabaseConfig):
    """Estime le temps de traitement"""
    print("\n⏱️  ESTIMATION DU TEMPS DE TRAITEMENT\n")

    try:
        connection = mysql.connector.connect(
            host=db_config.host,
            port=db_config.port,
            database=db_config.database,
            user=db_config.user,
            password=db_config.password,
            connect_timeout=10
        )

        cursor = connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM email")
        total = cursor.fetchone()[0]
        cursor.close()
        connection.close()

        # Estimation: ~100-150 emails/sec
        estimated_seconds = total / 120
        estimated_minutes = estimated_seconds / 60

        print(f"📧 Total d'emails:     {total:,}")
        print(f"⚡ Vitesse estimée:    ~120 emails/sec")
        print(f"⏱️  Temps estimé:       ~{estimated_minutes:.1f} minutes")

        if estimated_minutes > 10:
            print(f"\n💡 Pour {total:,} emails, le traitement prendra du temps.")
            print("   Pensez à lancer le script en arrière-plan:")
            print("   nohup python pattern_detector.py > output.log 2>&1 &")

    except Error as e:
        print(f"❌ Erreur: {e}")


def run_all_tests(db_config: DatabaseConfig, es_host: str = "localhost", es_port: int = 9200):
    """Lance tous les tests"""
    print("\n" + "="*80)
    print("🧪 TESTS DE VALIDATION")
    print("="*80)

    all_ok = True

    # Test 1: Elasticsearch
    if not test_elasticsearch_connection(es_host, es_port):
        all_ok = False
        print("\n⚠️  Elasticsearch n'est pas accessible")

    # Test 2: MariaDB
    if not test_mariadb_connection(db_config):
        all_ok = False
        print("\n❌ Impossible de continuer sans connexion à MariaDB")
        return False

    # Test 3: Patterns
    patterns_ok = check_patterns_file("email_patterns.json")
    if not patterns_ok:
        print("\n⚠️  Aucun pattern détecté - génération recommandée")
        all_ok = False

    # Test 4: Aperçu (si patterns disponibles)
    if patterns_ok:
        preview_cleaning(db_config, num_samples=3)

    # Test 5: Estimation
    estimate_processing_time(db_config)

    return all_ok


def main():
    """Menu principal"""

    # Configuration
    DB_CONFIG = DatabaseConfig(
        host="localhost",
        port=3306,
        database="data-analysis",
        user="root",
        password="password"  # ⚠️ À MODIFIER
    )

    ES_HOST = "localhost"
    ES_PORT = 9200

    print("\n" + "="*80)
    print("🧪 EMAIL CLEANER - TESTS & VALIDATION")
    print("="*80)

    # Tests
    all_ok = run_all_tests(DB_CONFIG, ES_HOST, ES_PORT)

    # Conclusion
    print("\n" + "="*80)
    if all_ok:
        print("✅ TOUS LES TESTS SONT OK!")
        print("\n🚀 Vous pouvez maintenant lancer le traitement complet:")
        print("   python pattern_detector.py")
    else:
        print("⚠️  CERTAINS TESTS ONT ÉCHOUÉ")
        print("\n💡 Actions recommandées:")
        print("   1. Vérifiez qu'Elasticsearch est démarré")
        print("   2. Vérifiez que vos emails sont indexés dans ES")
        print("   3. Vérifiez les credentials MariaDB")
        print("   4. Si ES est OK mais patterns manquants:")
        print("      python pattern_detector.py")

    print("="*80 + "\n")


if __name__ == "__main__":
    main()