"""
Détecteur de patterns répétitifs dans les emails via Elasticsearch
et mise à jour de la colonne cleaned_body dans MariaDB
"""

from elasticsearch import Elasticsearch
import mysql.connector
from mysql.connector import Error
import json
from typing import List, Dict, Optional
import re
from datetime import datetime
from tqdm import tqdm


class DatabaseConfig:
    """Configuration de la base de données"""
    def __init__(self, host: str = "localhost", port: int = 3306,
                 database: str = "emails_db", user: str = "root",
                 password: str = ""):
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password


class EmailPatternDetector:
    """Détecte et extrait les patterns répétitifs des emails"""

    def __init__(self, es_host: str = "localhost", es_port: int = 9200):
        self.es = Elasticsearch([f"http://{es_host}:{es_port}"])
        self.index_name = "emails"
        self.detected_patterns = {
            'short_patterns': [],      # 5 mots
            'long_patterns': [],       # 8-10 mots
            'line_patterns': [],       # Lignes complètes
            'disclaimer_patterns': [], # Disclaimers spécifiques
            'regex_patterns': []       # Patterns convertis en regex
        }

    def find_repetitive_shingles(
        self,
        field: str = "body.shingles_5",
        min_doc_count: int = 20,
        min_score: float = 0.5,
        size: int = 100
    ) -> List[Dict]:
        """
        Trouve les séquences de mots répétitives

        Args:
            field: Champ à analyser (body.shingles_5 ou body.shingles_10)
            min_doc_count: Nombre minimum d'emails contenant le pattern
            min_score: Score de significativité minimum
            size: Nombre de patterns à retourner
        """
        query = {
            "size": 0,
            "aggs": {
                "common_patterns": {
                    "significant_text": {
                        "field": field,
                        "min_doc_count": min_doc_count,
                        "size": size,
                        "filter_duplicate_text": True
                    }
                }
            }
        }

        try:
            response = self.es.search(index=self.index_name, body=query)
            buckets = response['aggregations']['common_patterns']['buckets']

            patterns = []
            for bucket in buckets:
                if bucket.get('score', 0) >= min_score:
                    patterns.append({
                        'text': bucket['key'],
                        'doc_count': bucket['doc_count'],
                        'score': bucket.get('score', 0),
                        'bg_count': bucket.get('bg_count', 0)
                    })

            return sorted(patterns, key=lambda x: x['doc_count'], reverse=True)

        except Exception as e:
            print(f"❌ Erreur lors de la recherche: {e}")
            return []

    def find_repetitive_lines(
        self,
        min_doc_count: int = 30,
        size: int = 50
    ) -> List[Dict]:
        """
        Trouve les lignes complètes répétitives (signatures, disclaimers)
        """
        query = {
            "size": 0,
            "aggs": {
                "common_lines": {
                    "terms": {
                        "field": "body.lines",
                        "min_doc_count": min_doc_count,
                        "size": size,
                        "order": {"_count": "desc"}
                    }
                }
            }
        }

        try:
            response = self.es.search(index=self.index_name, body=query)
            buckets = response['aggregations']['common_lines']['buckets']

            return [
                {
                    'text': bucket['key'],
                    'doc_count': bucket['doc_count']
                }
                for bucket in buckets
                if len(bucket['key']) > 10  # Ignorer lignes trop courtes
            ]

        except Exception as e:
            print(f"❌ Erreur lors de la recherche de lignes: {e}")
            return []

    def find_disclaimer_patterns(self, min_doc_count: int = 15) -> List[Dict]:
        """
        Cherche spécifiquement les disclaimers et mentions légales
        """
        # Mots-clés typiques des disclaimers
        disclaimer_keywords = [
            "confidential", "confidentiel", "disclaimer", "avertissement",
            "ne pas imprimer", "do not print", "think before printing",
            "virus", "legally binding", "intended recipient",
            "destinataire", "diffusion", "unauthorized", "propriété",
            "cliquez ici", "se désabonner", "unsubscribe"
        ]

        query = {
            "query": {
                "bool": {
                    "should": [
                        {"match": {"body": keyword}}
                        for keyword in disclaimer_keywords
                    ],
                    "minimum_should_match": 1
                }
            },
            "size": 0,
            "aggs": {
                "disclaimer_patterns": {
                    "significant_text": {
                        "field": "body.shingles_10",
                        "min_doc_count": min_doc_count,
                        "size": 50
                    }
                }
            }
        }

        try:
            response = self.es.search(index=self.index_name, body=query)
            buckets = response['aggregations']['disclaimer_patterns']['buckets']

            return [
                {
                    'text': bucket['key'],
                    'doc_count': bucket['doc_count'],
                    'score': bucket.get('score', 0)
                }
                for bucket in buckets
            ]

        except Exception as e:
            print(f"❌ Erreur recherche disclaimers: {e}")
            return []

    def detect_all_patterns(self, output_file: str = "detected_patterns.json"):
        """
        Lance toutes les détections et sauvegarde les résultats
        """
        print("\n" + "="*70)
        print("🔍 DÉTECTION DES PATTERNS RÉPÉTITIFS")
        print("="*70 + "\n")

        print("🔍 Détection des patterns courts (5 mots)...")
        short_patterns = self.find_repetitive_shingles(
            field="body.shingles_5",
            min_doc_count=20
        )
        self.detected_patterns['short_patterns'] = short_patterns
        print(f"   ✓ {len(short_patterns)} patterns trouvés")

        print("🔍 Détection des patterns longs (8-10 mots)...")
        long_patterns = self.find_repetitive_shingles(
            field="body.shingles_10",
            min_doc_count=15
        )
        self.detected_patterns['long_patterns'] = long_patterns
        print(f"   ✓ {len(long_patterns)} patterns trouvés")

        print("🔍 Détection des lignes répétitives...")
        line_patterns = self.find_repetitive_lines(min_doc_count=30)
        self.detected_patterns['line_patterns'] = line_patterns
        print(f"   ✓ {len(line_patterns)} lignes trouvées")

        print("🔍 Détection des disclaimers...")
        disclaimer_patterns = self.find_disclaimer_patterns(min_doc_count=10)
        self.detected_patterns['disclaimer_patterns'] = disclaimer_patterns
        print(f"   ✓ {len(disclaimer_patterns)} disclaimers trouvés")

        # Génération des regex
        print("\n🔧 Génération des patterns regex...")
        regex_patterns = self.generate_regex_patterns(min_length=30)
        print(f"   ✓ {len(regex_patterns)} regex générées")

        # Sauvegarde
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(self.detected_patterns, f, indent=2, ensure_ascii=False)

        print(f"\n💾 Patterns sauvegardés dans {output_file}")

        return self.detected_patterns

    def generate_regex_patterns(self, min_length: int = 30) -> List[str]:
        """
        Convertit les patterns détectés en regex pour nettoyage
        """
        regex_patterns = []

        # Patterns longs (priorité)
        for pattern in self.detected_patterns.get('long_patterns', []):
            text = pattern['text']
            if len(text) >= min_length:
                escaped = re.escape(text)
                flexible = escaped.replace(r'\ ', r'\s+')
                regex_patterns.append(flexible)

        # Lignes complètes
        for pattern in self.detected_patterns.get('line_patterns', []):
            text = pattern['text'].strip()
            if len(text) >= min_length:
                escaped = re.escape(text)
                regex_patterns.append(f'(?m)^{escaped}$')

        # Disclaimers
        for pattern in self.detected_patterns.get('disclaimer_patterns', []):
            text = pattern['text']
            if len(text) >= min_length:
                escaped = re.escape(text)
                flexible = escaped.replace(r'\ ', r'\s+')
                regex_patterns.append(flexible)

        self.detected_patterns['regex_patterns'] = regex_patterns
        return regex_patterns

    def print_summary(self):
        """Affiche un résumé des patterns détectés"""
        print("\n" + "="*70)
        print("📊 RÉSUMÉ DES PATTERNS DÉTECTÉS")
        print("="*70)

        categories = [
            ('short_patterns', '🔹 Patterns courts (5 mots)'),
            ('long_patterns', '🔹 Patterns longs (8-10 mots)'),
            ('line_patterns', '🔹 Lignes répétitives'),
            ('disclaimer_patterns', '🔹 Disclaimers')
        ]

        for key, label in categories:
            patterns = self.detected_patterns.get(key, [])
            print(f"\n{label}: {len(patterns)} patterns")

            # Afficher top 5
            for i, pattern in enumerate(patterns[:5], 1):
                text = pattern['text'][:80]
                count = pattern['doc_count']
                print(f"   {i}. [{count:>4} emails] {text}...")

        print("\n" + "="*70)


class EmailCleaner:
    """Nettoie les emails en utilisant les patterns détectés"""

    def __init__(self, patterns_file: str = "detected_patterns.json"):
        with open(patterns_file, 'r', encoding='utf-8') as f:
            self.patterns = json.load(f)

        self.regex_patterns = self._compile_patterns()

        # Patterns statiques complémentaires
        self.static_patterns = [
            r'(?m)\n--\s*\n.*$',  # Signature après --
            r'(?m)\n__\s*\n.*$',  # Signature après __
            r'(?m)^>.*$',         # Citations (lignes avec >)
            r'(?i)sent from my (iphone|android|mobile|blackberry).*$',
            r'(?i)get outlook for.*$',
            r'(?i)envoyé de mon (iphone|android|mobile).*$',
            r'(?m)^_{5,}$',       # Séparateurs de underscores
            r'(?m)^-{5,}$',       # Séparateurs de tirets
            r'(?m)^={5,}$',       # Séparateurs de égals
        ]

    def _compile_patterns(self) -> List[re.Pattern]:
        """Compile les regex patterns"""
        compiled = []

        for pattern_str in self.patterns.get('regex_patterns', []):
            try:
                compiled.append(re.compile(pattern_str, re.MULTILINE | re.IGNORECASE))
            except re.error as e:
                print(f"⚠️  Erreur compilation regex: {e}")

        # Patterns statiques
        for pattern_str in self.static_patterns:
            compiled.append(re.compile(pattern_str, re.MULTILINE | re.IGNORECASE))

        return compiled

    def clean_email_body(self, body: Optional[str]) -> Dict:
        """
        Nettoie le body d'un email

        Returns:
            Dict avec body nettoyé et statistiques
        """
        if not body:
            return {
                'cleaned_body': '',
                'original_length': 0,
                'cleaned_length': 0,
                'chars_saved': 0,
                'reduction_pct': 0,
                'tokens_saved': 0
            }

        original_length = len(body)
        cleaned = body

        # Application de tous les patterns
        for pattern in self.regex_patterns:
            cleaned = pattern.sub('', cleaned)

        # Nettoyage final
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)  # Max 2 sauts de ligne
        cleaned = re.sub(r'[ \t]+', ' ', cleaned)     # Normaliser espaces
        cleaned = cleaned.strip()

        # Stats
        cleaned_length = len(cleaned)
        saved_chars = original_length - cleaned_length
        reduction_pct = (saved_chars / original_length * 100) if original_length > 0 else 0

        # Estimation tokens (≈ 4 chars par token)
        original_tokens = original_length // 4
        cleaned_tokens = cleaned_length // 4
        tokens_saved = original_tokens - cleaned_tokens

        return {
            'cleaned_body': cleaned,
            'original_length': original_length,
            'cleaned_length': cleaned_length,
            'chars_saved': saved_chars,
            'reduction_pct': reduction_pct,
            'tokens_saved': tokens_saved
        }


class DatabaseCleaner:
    """Met à jour la colonne cleaned_body dans MariaDB"""

    def __init__(self, db_config: DatabaseConfig, cleaner: EmailCleaner):
        self.db_config = db_config
        self.cleaner = cleaner
        self.connection = None

    def connect(self):
        """Établit la connexion à la base de données"""
        try:
            self.connection = mysql.connector.connect(
                host=self.db_config.host,
                port=self.db_config.port,
                database=self.db_config.database,
                user=self.db_config.user,
                password=self.db_config.password
            )
            if self.connection.is_connected():
                print("✅ Connexion à MariaDB établie")
                return True
        except Error as e:
            print(f"❌ Erreur de connexion à MariaDB: {e}")
            return False

    def disconnect(self):
        """Ferme la connexion à la base de données"""
        if self.connection and self.connection.is_connected():
            self.connection.close()
            print("✅ Connexion à MariaDB fermée")

    def get_email_count(self) -> int:
        """Retourne le nombre d'emails dans la base"""
        cursor = self.connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM email")
        count = cursor.fetchone()[0]
        cursor.close()
        return count

    def get_emails_batch(self, offset: int, limit: int) -> List[Dict]:
        """
        Récupère un lot d'emails

        Returns:
            Liste de dicts avec id et normalized_body (ou raw_body si normalized_body est NULL)
        """
        cursor = self.connection.cursor(dictionary=True)
        query = """
            SELECT id,
                   COALESCE(normalized_body, raw_body) as body
            FROM email
            LIMIT %s OFFSET %s
        """
        cursor.execute(query, (limit, offset))
        emails = cursor.fetchall()
        cursor.close()
        return emails

    def update_cleaned_body(self, email_id: int, cleaned_body: str) -> bool:
        """Met à jour la colonne cleaned_body pour un email"""
        try:
            cursor = self.connection.cursor()
            query = "UPDATE email SET cleaned_body = %s WHERE id = %s"
            cursor.execute(query, (cleaned_body, email_id))
            self.connection.commit()
            cursor.close()
            return True
        except Error as e:
            print(f"❌ Erreur update email {email_id}: {e}")
            return False

    def clean_all_emails(self, batch_size: int = 100):
        """
        Nettoie tous les emails et met à jour la base
        """
        total_emails = self.get_email_count()
        print(f"\n📧 Nombre total d'emails à traiter: {total_emails}")

        total_chars_saved = 0
        total_tokens_saved = 0
        processed = 0
        errors = 0

        # Progress bar
        with tqdm(total=total_emails, desc="🧹 Nettoyage des emails",
                  unit="email") as pbar:

            offset = 0
            while offset < total_emails:
                # Récupérer un lot
                emails = self.get_emails_batch(offset, batch_size)

                if not emails:
                    break

                # Traiter chaque email du lot
                for email in emails:
                    email_id = email['id']
                    body = email['body']

                    # Nettoyer
                    result = self.cleaner.clean_email_body(body)

                    # Mettre à jour la base
                    if self.update_cleaned_body(email_id, result['cleaned_body']):
                        processed += 1
                        total_chars_saved += result['chars_saved']
                        total_tokens_saved += result['tokens_saved']
                    else:
                        errors += 1

                    pbar.update(1)

                offset += batch_size

        # Statistiques finales
        print("\n" + "="*70)
        print("📊 STATISTIQUES DE NETTOYAGE")
        print("="*70)
        print(f"✅ Emails traités:      {processed:,}")
        print(f"❌ Erreurs:             {errors:,}")
        print(f"💾 Caractères supprimés: {total_chars_saved:,}")
        print(f"💰 Tokens économisés:   {total_tokens_saved:,}")

        if processed > 0:
            avg_reduction = (total_chars_saved / (total_chars_saved +
                            sum(self.cleaner.clean_email_body(e['body'])['cleaned_length']
                                for e in self.get_emails_batch(0, min(100, total_emails))))) * 100
            print(f"📉 Réduction moyenne:   {avg_reduction:.1f}%")

        print("="*70 + "\n")


def main():
    """Fonction principale"""

    print("\n" + "="*70)
    print("🚀 EMAIL PATTERN DETECTOR & CLEANER")
    print("="*70)

    # Configuration
    ES_HOST = "localhost"
    ES_PORT = 9200

    DB_CONFIG = DatabaseConfig(
        host="localhost",
        port=3306,
        database="emails_db",
        user="root",
        password="your_password"  # ⚠️ À MODIFIER
    )

    # Étape 1: Détection des patterns
    print("\n📍 ÉTAPE 1/3: Détection des patterns répétitifs\n")

    detector = EmailPatternDetector(es_host=ES_HOST, es_port=ES_PORT)
    patterns = detector.detect_all_patterns(output_file="email_patterns.json")
    detector.print_summary()

    # Étape 2: Préparation du cleaner
    print("\n📍 ÉTAPE 2/3: Préparation du nettoyeur\n")

    cleaner = EmailCleaner("email_patterns.json")
    print(f"✅ {len(cleaner.regex_patterns)} patterns de nettoyage chargés")

    # Étape 3: Nettoyage et mise à jour de la base
    print("\n📍 ÉTAPE 3/3: Nettoyage et mise à jour de la base de données\n")

    db_cleaner = DatabaseCleaner(DB_CONFIG, cleaner)

    if db_cleaner.connect():
        try:
            db_cleaner.clean_all_emails(batch_size=100)
        finally:
            db_cleaner.disconnect()

    print("\n✅ Traitement terminé!\n")


if __name__ == "__main__":
    main()