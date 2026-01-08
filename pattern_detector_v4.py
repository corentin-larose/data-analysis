"""
Détecteur de patterns répétitifs dans les emails
Version optimisée - analyse côté Python pour éviter les timeouts ES
"""

from elasticsearch import Elasticsearch
from elasticsearch.helpers import scan
import mysql.connector
from mysql.connector import Error
import json
from typing import List, Dict, Optional
import re
from collections import Counter
from tqdm import tqdm


class DatabaseConfig:
    """Configuration de la base de données"""
    def __init__(self, host: str = "localhost", port: int = 3306,
                 database: str = "data-analysis", user: str = "root",
                 password: str = ""):
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password


class EmailPatternDetector:
    """Détecte les patterns répétitifs en analysant les emails côté Python"""

    def __init__(self, es_host: str = "localhost", es_port: int = 9200):
        self.es = Elasticsearch(
            [f"http://{es_host}:{es_port}"],
            request_timeout=30
        )
        self.index_name = "emails"
        self.detected_patterns = {
            'short_patterns': [],
            'long_patterns': [],
            'line_patterns': [],
            'disclaimer_patterns': [],
            'regex_patterns': []
        }

    def get_index_stats(self) -> Dict:
        """Récupère les stats de l'index"""
        try:
            count_result = self.es.count(index=self.index_name)
            return {'count': count_result['count']}
        except Exception as e:
            print(f"⚠️  Erreur stats: {e}")
            return {'count': 0}

    def extract_ngrams(self, text: str, n: int = 5) -> List[str]:
        """Extrait les n-grams d'un texte"""
        if not text:
            return []

        words = text.lower().split()
        if len(words) < n:
            return []

        ngrams = []
        for i in range(len(words) - n + 1):
            ngram = ' '.join(words[i:i+n])
            if len(ngram) >= 20:  # Minimum 20 caractères
                ngrams.append(ngram)

        return ngrams

    def scan_emails(self, max_emails: int = 2000) -> List[str]:
        """
        Scan les emails depuis Elasticsearch
        Limite à max_emails pour éviter les timeouts
        """
        print(f"   📥 Récupération de {max_emails} emails depuis ES...")

        query = {
            "query": {"match_all": {}},
            "_source": ["body"]
        }

        bodies = []
        count = 0

        try:
            for hit in scan(
                self.es,
                index=self.index_name,
                query=query,
                size=50,
                scroll='2m',
                request_timeout=30
            ):
                if count >= max_emails:
                    break

                body = hit['_source'].get('body', '')
                if body and len(body) > 50:
                    bodies.append(body)
                    count += 1

                if count % 100 == 0:
                    print(f"      {count}/{max_emails} emails...", end='\r')

            print(f"      ✓ {len(bodies)} emails récupérés" + " "*20)

        except Exception as e:
            print(f"\n   ⚠️  Erreur scan: {e}")
            print("      Tentative de récupération partielle...")

        return bodies

    def find_repetitive_ngrams(
        self,
        bodies: List[str],
        n: int = 5,
        min_occurrences: int = 20
    ) -> List[Dict]:
        """
        Trouve les n-grams répétitifs dans les emails
        """
        print(f"   🔍 Extraction des {n}-grams...")

        all_ngrams = []

        for body in tqdm(bodies, desc=f"      Analyse", unit="email"):
            ngrams = self.extract_ngrams(body, n)
            all_ngrams.extend(ngrams)

        print(f"   📊 Comptage des occurrences...")
        ngram_counts = Counter(all_ngrams)

        # Filtrer par occurrences minimales
        patterns = [
            {
                'text': ngram,
                'doc_count': count,
                'score': count / len(bodies) if len(bodies) > 0 else 0
            }
            for ngram, count in ngram_counts.items()
            if count >= min_occurrences
        ]

        return sorted(patterns, key=lambda x: x['doc_count'], reverse=True)[:50]

    def find_repetitive_lines(
        self,
        bodies: List[str],
        min_occurrences: int = 30
    ) -> List[Dict]:
        """
        Trouve les lignes complètes répétitives
        """
        print("   🔍 Extraction des lignes...")

        all_lines = []

        for body in tqdm(bodies, desc="      Analyse", unit="email"):
            lines = body.split('\n')
            for line in lines:
                line = line.strip()
                # Filtrer les lignes intéressantes
                if 20 < len(line) < 500:  # Entre 20 et 500 chars
                    all_lines.append(line)

        print(f"   📊 Comptage ({len(all_lines):,} lignes trouvées)...")
        line_counts = Counter(all_lines)

        patterns = [
            {
                'text': line,
                'doc_count': count
            }
            for line, count in line_counts.items()
            if count >= min_occurrences
        ]

        return sorted(patterns, key=lambda x: x['doc_count'], reverse=True)[:50]

    def find_disclaimer_patterns(
        self,
        bodies: List[str],
        min_occurrences: int = 10
    ) -> List[Dict]:
        """
        Trouve les disclaimers en cherchant les mots-clés typiques
        """
        print("   🔍 Recherche de disclaimers...")

        disclaimer_keywords = [
            'confidential', 'confidentiel', 'disclaimer', 'avertissement',
            'ne pas imprimer', 'do not print', 'think before printing',
            'destinataire', 'intended recipient', 'unauthorized',
            'virus', 'legally binding'
        ]

        disclaimer_lines = []

        for body in tqdm(bodies, desc="      Analyse", unit="email"):
            lines = body.split('\n')
            for line in lines:
                line_lower = line.lower()
                # Vérifier si la ligne contient un mot-clé
                if any(kw in line_lower for kw in disclaimer_keywords):
                    if 30 < len(line) < 500:
                        disclaimer_lines.append(line.strip())

        print(f"   📊 Comptage ({len(disclaimer_lines):,} lignes de disclaimer)...")
        line_counts = Counter(disclaimer_lines)

        patterns = [
            {
                'text': line,
                'doc_count': count,
                'score': 1.0
            }
            for line, count in line_counts.items()
            if count >= min_occurrences
        ]

        return sorted(patterns, key=lambda x: x['doc_count'], reverse=True)[:30]

    def detect_all_patterns(
        self,
        output_file: str = "detected_patterns.json",
        max_emails: int = 2000
    ):
        """
        Lance toutes les détections
        """
        print("\n" + "="*70)
        print("🔍 DÉTECTION DES PATTERNS RÉPÉTITIFS")
        print("="*70 + "\n")

        # Stats
        stats = self.get_index_stats()
        total_emails = stats['count']
        print(f"📊 Index '{self.index_name}': {total_emails:,} documents")

        if total_emails == 0:
            print("❌ Aucun email trouvé dans l'index!")
            return self.detected_patterns

        # Limiter pour éviter les timeouts
        sample_size = min(max_emails, total_emails)
        print(f"📦 Analyse sur un échantillon de {sample_size:,} emails\n")

        # Récupérer les emails
        bodies = self.scan_emails(sample_size)

        if not bodies:
            print("❌ Impossible de récupérer les emails")
            return self.detected_patterns

        print()

        # 1. Patterns courts (5 mots)
        print("🔍 Détection des patterns courts (5 mots)...")
        try:
            short_patterns = self.find_repetitive_ngrams(
                bodies, n=5, min_occurrences=20
            )
            self.detected_patterns['short_patterns'] = short_patterns
            print(f"   ✓ {len(short_patterns)} patterns trouvés\n")
        except Exception as e:
            print(f"   ⚠️  Erreur: {e}\n")
            self.detected_patterns['short_patterns'] = []

        # 2. Patterns longs (10 mots)
        print("🔍 Détection des patterns longs (10 mots)...")
        try:
            long_patterns = self.find_repetitive_ngrams(
                bodies, n=10, min_occurrences=15
            )
            self.detected_patterns['long_patterns'] = long_patterns
            print(f"   ✓ {len(long_patterns)} patterns trouvés\n")
        except Exception as e:
            print(f"   ⚠️  Erreur: {e}\n")
            self.detected_patterns['long_patterns'] = []

        # 3. Lignes répétitives
        print("🔍 Détection des lignes répétitives...")
        try:
            line_patterns = self.find_repetitive_lines(bodies, min_occurrences=30)
            self.detected_patterns['line_patterns'] = line_patterns
            print(f"   ✓ {len(line_patterns)} lignes trouvées\n")
        except Exception as e:
            print(f"   ⚠️  Erreur: {e}\n")
            self.detected_patterns['line_patterns'] = []

        # 4. Disclaimers
        print("🔍 Détection des disclaimers...")
        try:
            disclaimer_patterns = self.find_disclaimer_patterns(bodies, min_occurrences=10)
            self.detected_patterns['disclaimer_patterns'] = disclaimer_patterns
            print(f"   ✓ {len(disclaimer_patterns)} disclaimers trouvés\n")
        except Exception as e:
            print(f"   ⚠️  Erreur: {e}\n")
            self.detected_patterns['disclaimer_patterns'] = []

        # Génération des regex
        print("🔧 Génération des patterns regex...")
        regex_patterns = self.generate_regex_patterns(min_length=30)
        print(f"   ✓ {len(regex_patterns)} regex générées\n")

        # Sauvegarde
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(self.detected_patterns, f, indent=2, ensure_ascii=False)

        print(f"💾 Patterns sauvegardés dans {output_file}")

        return self.detected_patterns

    def generate_regex_patterns(self, min_length: int = 30) -> List[str]:
        """
        Convertit les patterns en regex
        """
        regex_patterns = []
        seen = set()

        # Traiter tous les types de patterns
        all_patterns = (
            self.detected_patterns.get('long_patterns', []) +
            self.detected_patterns.get('line_patterns', []) +
            self.detected_patterns.get('disclaimer_patterns', [])
        )

        for pattern in all_patterns:
            text = pattern['text'].strip()

            if len(text) >= min_length and text not in seen:
                # Échapper et rendre flexible
                escaped = re.escape(text)
                # Permettre variations d'espaces
                flexible = escaped.replace(r'\ ', r'\s+')
                regex_patterns.append(flexible)
                seen.add(text)

        self.detected_patterns['regex_patterns'] = regex_patterns
        return regex_patterns

    def print_summary(self):
        """Affiche un résumé"""
        print("\n" + "="*70)
        print("📊 RÉSUMÉ DES PATTERNS DÉTECTÉS")
        print("="*70)

        categories = [
            ('short_patterns', '🔹 Patterns courts (5 mots)'),
            ('long_patterns', '🔹 Patterns longs (10 mots)'),
            ('line_patterns', '🔹 Lignes répétitives'),
            ('disclaimer_patterns', '🔹 Disclaimers')
        ]

        total = 0
        for key, label in categories:
            patterns = self.detected_patterns.get(key, [])
            total += len(patterns)
            print(f"\n{label}: {len(patterns)} patterns")

            # Top 3
            for i, pattern in enumerate(patterns[:3], 1):
                text = pattern['text'][:65]
                count = pattern.get('doc_count', 0)
                print(f"   {i}. [{count:>4}x] {text}...")

        print(f"\n📦 Total: {total} patterns détectés")
        print(f"🔧 Regex: {len(self.detected_patterns.get('regex_patterns', []))}")
        print("="*70)


class EmailCleaner:
    """Nettoie les emails"""

    def __init__(self, patterns_file: str = "detected_patterns.json"):
        try:
            with open(patterns_file, 'r', encoding='utf-8') as f:
                self.patterns = json.load(f)
        except:
            print(f"⚠️  Fichier {patterns_file} introuvable")
            self.patterns = {'regex_patterns': []}

        self.regex_patterns = self._compile_patterns()

    def _compile_patterns(self) -> List[re.Pattern]:
        """Compile les patterns"""
        compiled = []

        # Patterns détectés
        for pattern_str in self.patterns.get('regex_patterns', []):
            try:
                compiled.append(re.compile(pattern_str, re.MULTILINE | re.IGNORECASE))
            except:
                pass

        # Patterns statiques robustes
        static = [
            r'(?m)\n--\s*\n.*$',
            r'(?m)\n__\s*\n.*$',
            r'(?m)^>.*$',
            r'(?i)sent from my (iphone|android|mobile|blackberry).*$',
            r'(?i)get outlook for.*$',
            r'(?i)envoyé de mon (iphone|android|mobile).*$',
            r'(?i)obtenez outlook pour.*$',
            r'(?m)^_{5,}$',
            r'(?m)^-{5,}$',
            r'(?m)^={5,}$',
            r'(?i)(confidential|confidentiel).{0,200}?(unauthorized|non autorisée)',
            r'(?i)(disclaimer|avertissement).{0,300}$',
            r'(?i)(ne pas imprimer|do not print|think before printing).{0,100}',
            r'(?i)(destinataire|intended recipient).{0,200}',
        ]

        for pattern_str in static:
            try:
                compiled.append(re.compile(pattern_str, re.MULTILINE | re.IGNORECASE))
            except:
                pass

        return compiled

    def clean_email_body(self, body: Optional[str]) -> Dict:
        """Nettoie un email"""
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

        # Application des patterns
        for pattern in self.regex_patterns:
            try:
                cleaned = pattern.sub('', cleaned)
            except:
                continue

        # Nettoyage final
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
        cleaned = re.sub(r'[ \t]+', ' ', cleaned)
        cleaned = cleaned.strip()

        # Stats
        cleaned_length = len(cleaned)
        saved_chars = original_length - cleaned_length
        reduction_pct = (saved_chars / original_length * 100) if original_length > 0 else 0

        return {
            'cleaned_body': cleaned,
            'original_length': original_length,
            'cleaned_length': cleaned_length,
            'chars_saved': saved_chars,
            'reduction_pct': reduction_pct,
            'tokens_saved': saved_chars // 4
        }


class DatabaseCleaner:
    """Met à jour MariaDB"""

    def __init__(self, db_config: DatabaseConfig, cleaner: EmailCleaner):
        self.db_config = db_config
        self.cleaner = cleaner
        self.connection = None

    def connect(self):
        """Connexion à la base"""
        try:
            self.connection = mysql.connector.connect(
                host=self.db_config.host,
                port=self.db_config.port,
                database=self.db_config.database,
                user=self.db_config.user,
                password=self.db_config.password,
                connect_timeout=10
            )
            if self.connection.is_connected():
                print("✅ Connexion à MariaDB établie")
                return True
        except Error as e:
            print(f"❌ Erreur MariaDB: {e}")
            return False

    def disconnect(self):
        """Fermeture"""
        if self.connection and self.connection.is_connected():
            self.connection.close()
            print("✅ Connexion fermée")

    def get_email_count(self) -> int:
        """Compte les emails"""
        cursor = self.connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM email")
        count = cursor.fetchone()[0]
        cursor.close()
        return count

    def get_emails_batch(self, offset: int, limit: int) -> List[Dict]:
        """Récupère un lot"""
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
        """Update"""
        try:
            cursor = self.connection.cursor()
            query = "UPDATE email SET cleaned_body = %s WHERE id = %s"
            cursor.execute(query, (cleaned_body, email_id))
            self.connection.commit()
            cursor.close()
            return True
        except Error:
            return False

    def clean_all_emails(self, batch_size: int = 100):
        """Traite tous les emails"""
        total = self.get_email_count()
        print(f"\n📧 {total:,} emails à traiter")

        total_saved = 0
        processed = 0

        with tqdm(total=total, desc="🧹 Nettoyage", unit="email") as pbar:
            offset = 0
            while offset < total:
                emails = self.get_emails_batch(offset, batch_size)

                if not emails:
                    break

                for email in emails:
                    result = self.cleaner.clean_email_body(email['body'])

                    if self.update_cleaned_body(email['id'], result['cleaned_body']):
                        processed += 1
                        total_saved += result['tokens_saved']

                    pbar.update(1)

                offset += batch_size

        print("\n" + "="*70)
        print("📊 RÉSULTATS")
        print("="*70)
        print(f"✅ Traités:  {processed:,}")
        print(f"💰 Tokens:   ~{total_saved:,} économisés")
        print("="*70 + "\n")


def main():
    """Main"""

    print("\n" + "="*70)
    print("🚀 EMAIL PATTERN DETECTOR & CLEANER v3.0")
    print("   (Analyse côté Python - pas de timeout ES)")
    print("="*70)

    # Config
    ES_HOST = "localhost"
    ES_PORT = 9200

    DB_CONFIG = DatabaseConfig(
        host="localhost",
        port=3306,
        database="emails_db",
        user="root",
        password="your_password"  # ⚠️ MODIFIER
    )

    # Étape 1: Détection
    print("\n📍 ÉTAPE 1/3: Détection des patterns\n")

    detector = EmailPatternDetector(es_host=ES_HOST, es_port=ES_PORT)

    # Analyser 2000 emails (ajustable selon ta machine)
    patterns = detector.detect_all_patterns(
        output_file="email_patterns.json",
        max_emails=60000  # Augmente si ta machine le permet
    )

    detector.print_summary()

    # Étape 2: Préparation
    print("\n📍 ÉTAPE 2/3: Préparation\n")

    cleaner = EmailCleaner("email_patterns.json")
    print(f"✅ {len(cleaner.regex_patterns)} patterns chargés")

    # Étape 3: Nettoyage
    print("\n📍 ÉTAPE 3/3: Mise à jour base\n")

    db_cleaner = DatabaseCleaner(DB_CONFIG, cleaner)

    if db_cleaner.connect():
        try:
            db_cleaner.clean_all_emails(batch_size=100)
        finally:
            db_cleaner.disconnect()

    print("\n✅ Terminé!\n")


if __name__ == "__main__":
    main()