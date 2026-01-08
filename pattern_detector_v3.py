"""
Détecteur de patterns répétitifs dans les emails via Elasticsearch
et mise à jour de la colonne cleaned_body dans MariaDB
Version optimisée pour éviter les timeouts
"""

from elasticsearch import Elasticsearch
from elasticsearch.helpers import scan
import mysql.connector
from mysql.connector import Error
import json
from typing import List, Dict, Optional
import re
from datetime import datetime
from tqdm import tqdm
from collections import Counter


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

    def __init__(self, es_host: str = "localhost", es_port: int = 9200, timeout: int = 60):
        self.es = Elasticsearch(
            [f"http://{es_host}:{es_port}"],
            request_timeout=timeout,
            max_retries=3,
            retry_on_timeout=True
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
        """Récupère les statistiques de l'index"""
        try:
            stats = self.es.count(index=self.index_name)
            return {'count': stats['count']}
        except Exception as e:
            print(f"⚠️  Impossible de récupérer les stats: {e}")
            return {'count': 0}

    def find_repetitive_patterns_simple(
        self,
        field: str = "body.raw",
        sample_size: int = 1000,
        min_occurrences: int = 20
    ) -> List[Dict]:
        """
        Méthode simplifiée: scan les emails et analyse les patterns côté Python
        Plus lent mais évite les timeouts ES
        """
        print(f"   📊 Scan de {sample_size} emails pour analyse...")

        # Configuration du scan avec timeout élevé
        query = {
            "query": {"match_all": {}},
            "_source": [field.replace('.raw', '')]
        }

        all_lines = []
        email_count = 0

        try:
            # Utiliser scroll au lieu d'agrégations
            for hit in scan(
                self.es,
                index=self.index_name,
                query=query,
                size=100,  # Taille du batch de scroll
                scroll='5m',
                request_timeout=60
            ):
                if email_count >= sample_size:
                    break

                body = hit['_source'].get('body', '')
                if body:
                    # Extraire les lignes
                    lines = [l.strip() for l in body.split('\n') if len(l.strip()) > 20]
                    all_lines.extend(lines)

                email_count += 1

                if email_count % 100 == 0:
                    print(f"      Traité: {email_count}/{sample_size} emails", end='\r')

            print(f"      Traité: {email_count} emails - Terminé" + " "*20)

        except Exception as e:
            print(f"\n   ⚠️  Erreur lors du scan: {e}")
            return []

        # Compter les occurrences
        print("   🔍 Analyse des patterns...")
        line_counts = Counter(all_lines)

        patterns = [
            {
                'text': line,
                'doc_count': count,
                'score': count / email_count
            }
            for line, count in line_counts.items()
            if count >= min_occurrences and len(line) >= 30
        ]

        return sorted(patterns, key=lambda x: x['doc_count'], reverse=True)

    def find_repetitive_shingles_optimized(
        self,
        field: str = "body.shingles_5",
        min_doc_count: int = 20,
        size: int = 50,
        timeout: str = "60s"
    ) -> List[Dict]:
        """
        Version optimisée avec timeout explicite et taille réduite
        """
        query = {
            "size": 0,
            "timeout": timeout,
            "aggs": {
                "common_patterns": {
                    "terms": {
                        "field": field,
                        "min_doc_count": min_doc_count,
                        "size": size,
                        "order": {"_count": "desc"}
                    }
                }
            }
        }

        try:
            response = self.es.search(
                index=self.index_name,
                body=query,
                request_timeout=60
            )

            if response.get('timed_out'):
                print("   ⚠️  Requête timeout - résultats partiels")

            buckets = response['aggregations']['common_patterns']['buckets']

            patterns = [
                {
                    'text': bucket['key'],
                    'doc_count': bucket['doc_count'],
                    'score': 1.0
                }
                for bucket in buckets
            ]

            return patterns

        except Exception as e:
            print(f"   ❌ Erreur: {e}")
            print("   ℹ️  Bascule vers méthode simplifiée...")
            # Fallback vers la méthode simple
            return self.find_repetitive_patterns_simple(
                field="body",
                sample_size=500,
                min_occurrences=min_doc_count
            )

    def find_repetitive_lines(
        self,
        min_doc_count: int = 30,
        size: int = 50
    ) -> List[Dict]:
        """
        Trouve les lignes complètes répétitives
        """
        query = {
            "size": 0,
            "timeout": "60s",
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
            response = self.es.search(
                index=self.index_name,
                body=query,
                request_timeout=60
            )
            buckets = response['aggregations']['common_lines']['buckets']

            return [
                {
                    'text': bucket['key'],
                    'doc_count': bucket['doc_count']
                }
                for bucket in buckets
                if len(bucket['key']) > 10
            ]

        except Exception as e:
            print(f"   ❌ Erreur: {e}")
            print("   ℹ️  Bascule vers méthode simplifiée...")
            return self.find_repetitive_patterns_simple(
                field="body",
                sample_size=500,
                min_occurrences=min_doc_count
            )

    def find_disclaimer_patterns(self, min_doc_count: int = 15) -> List[Dict]:
        """
        Cherche les disclaimers avec une approche plus simple
        """
        disclaimer_keywords = [
            "confidential", "confidentiel", "disclaimer",
            "ne pas imprimer", "do not print"
        ]

        # Approche simplifiée: scanner les emails contenant ces mots-clés
        patterns = []

        for keyword in disclaimer_keywords[:3]:  # Limiter à 3 mots-clés
            try:
                query = {
                    "query": {
                        "match": {"body": keyword}
                    },
                    "_source": ["body"],
                    "size": 100  # Limiter le sample
                }

                response = self.es.search(
                    index=self.index_name,
                    body=query,
                    request_timeout=30
                )

                # Extraire les lignes contenant le mot-clé
                for hit in response['hits']['hits']:
                    body = hit['_source'].get('body', '')
                    lines = body.split('\n')

                    for line in lines:
                        if keyword.lower() in line.lower() and len(line) > 30:
                            patterns.append(line.strip())

            except Exception as e:
                print(f"   ⚠️  Erreur pour '{keyword}': {e}")
                continue

        # Compter les occurrences
        line_counts = Counter(patterns)

        return [
            {
                'text': line,
                'doc_count': count,
                'score': 1.0
            }
            for line, count in line_counts.items()
            if count >= min_doc_count
        ][:20]  # Top 20

    def detect_all_patterns(self, output_file: str = "detected_patterns.json"):
        """
        Lance toutes les détections avec méthodes optimisées
        """
        print("\n" + "="*70)
        print("🔍 DÉTECTION DES PATTERNS RÉPÉTITIFS")
        print("="*70 + "\n")

        # Vérifier la connexion ES
        stats = self.get_index_stats()
        print(f"📊 Index '{self.index_name}': {stats['count']:,} documents\n")

        # 1. Patterns courts
        print("🔍 Détection des patterns courts (5 mots)...")
        try:
            short_patterns = self.find_repetitive_shingles_optimized(
                field="body.shingles_5",
                min_doc_count=20,
                size=30
            )
            self.detected_patterns['short_patterns'] = short_patterns
            print(f"   ✓ {len(short_patterns)} patterns trouvés")
        except Exception as e:
            print(f"   ⚠️  Ignoré: {e}")
            self.detected_patterns['short_patterns'] = []

        # 2. Patterns longs
        print("🔍 Détection des patterns longs (8-10 mots)...")
        try:
            long_patterns = self.find_repetitive_shingles_optimized(
                field="body.shingles_10",
                min_doc_count=15,
                size=30
            )
            self.detected_patterns['long_patterns'] = long_patterns
            print(f"   ✓ {len(long_patterns)} patterns trouvés")
        except Exception as e:
            print(f"   ⚠️  Ignoré: {e}")
            self.detected_patterns['long_patterns'] = []

        # 3. Lignes répétitives
        print("🔍 Détection des lignes répétitives...")
        try:
            line_patterns = self.find_repetitive_lines(min_doc_count=30, size=30)
            self.detected_patterns['line_patterns'] = line_patterns
            print(f"   ✓ {len(line_patterns)} lignes trouvées")
        except Exception as e:
            print(f"   ⚠️  Ignoré: {e}")
            self.detected_patterns['line_patterns'] = []

        # 4. Disclaimers
        print("🔍 Détection des disclaimers...")
        try:
            disclaimer_patterns = self.find_disclaimer_patterns(min_doc_count=10)
            self.detected_patterns['disclaimer_patterns'] = disclaimer_patterns
            print(f"   ✓ {len(disclaimer_patterns)} disclaimers trouvés")
        except Exception as e:
            print(f"   ⚠️  Ignoré: {e}")
            self.detected_patterns['disclaimer_patterns'] = []

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
        seen = set()  # Éviter les doublons

        # Patterns longs
        for pattern in self.detected_patterns.get('long_patterns', []):
            text = pattern['text']
            if len(text) >= min_length and text not in seen:
                escaped = re.escape(text)
                flexible = escaped.replace(r'\ ', r'\s+')
                regex_patterns.append(flexible)
                seen.add(text)

        # Lignes complètes
        for pattern in self.detected_patterns.get('line_patterns', []):
            text = pattern['text'].strip()
            if len(text) >= min_length and text not in seen:
                escaped = re.escape(text)
                regex_patterns.append(f'(?m)^{escaped}$')
                seen.add(text)

        # Disclaimers
        for pattern in self.detected_patterns.get('disclaimer_patterns', []):
            text = pattern['text']
            if len(text) >= min_length and text not in seen:
                escaped = re.escape(text)
                flexible = escaped.replace(r'\ ', r'\s+')
                regex_patterns.append(flexible)
                seen.add(text)

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

        total_patterns = 0
        for key, label in categories:
            patterns = self.detected_patterns.get(key, [])
            total_patterns += len(patterns)
            print(f"\n{label}: {len(patterns)} patterns")

            # Afficher top 3
            for i, pattern in enumerate(patterns[:3], 1):
                text = pattern['text'][:70]
                count = pattern['doc_count']
                print(f"   {i}. [{count:>4}x] {text}...")

        print(f"\n📦 Total: {total_patterns} patterns uniques détectés")
        print(f"🔧 Regex générées: {len(self.detected_patterns.get('regex_patterns', []))}")
        print("="*70)


class EmailCleaner:
    """Nettoie les emails en utilisant les patterns détectés"""

    def __init__(self, patterns_file: str = "detected_patterns.json"):
        try:
            with open(patterns_file, 'r', encoding='utf-8') as f:
                self.patterns = json.load(f)
        except FileNotFoundError:
            print(f"⚠️  Fichier {patterns_file} introuvable, utilisation des patterns par défaut")
            self.patterns = {'regex_patterns': []}

        self.regex_patterns = self._compile_patterns()

        # Patterns statiques robustes
        self.static_patterns = [
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
            r'(?i)(confidential|disclaimer|avertissement).{0,200}$',
            r'(?i)(ne pas imprimer|do not print|think before printing).{0,100}',
        ]

    def _compile_patterns(self) -> List[re.Pattern]:
        """Compile les regex patterns"""
        compiled = []

        for pattern_str in self.patterns.get('regex_patterns', []):
            try:
                compiled.append(re.compile(pattern_str, re.MULTILINE | re.IGNORECASE))
            except re.error:
                pass  # Ignorer les patterns invalides

        # Patterns statiques
        for pattern_str in self.static_patterns:
            try:
                compiled.append(re.compile(pattern_str, re.MULTILINE | re.IGNORECASE))
            except re.error:
                pass

        return compiled

    def clean_email_body(self, body: Optional[str]) -> Dict:
        """
        Nettoie le body d'un email
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
                password=self.db_config.password,
                connect_timeout=10
            )
            if self.connection.is_connected():
                print("✅ Connexion à MariaDB établie")
                return True
        except Error as e:
            print(f"❌ Erreur de connexion à MariaDB: {e}")
            return False

    def disconnect(self):
        """Ferme la connexion"""
        if self.connection and self.connection.is_connected():
            self.connection.close()
            print("✅ Connexion à MariaDB fermée")

    def get_email_count(self) -> int:
        """Retourne le nombre d'emails"""
        cursor = self.connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM email")
        count = cursor.fetchone()[0]
        cursor.close()
        return count

    def get_emails_batch(self, offset: int, limit: int) -> List[Dict]:
        """Récupère un lot d'emails"""
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
        """Met à jour la colonne cleaned_body"""
        try:
            cursor = self.connection.cursor()
            query = "UPDATE email SET cleaned_body = %s WHERE id = %s"
            cursor.execute(query, (cleaned_body, email_id))
            self.connection.commit()
            cursor.close()
            return True
        except Error as e:
            print(f"\n❌ Erreur update email {email_id}: {e}")
            return False

    def clean_all_emails(self, batch_size: int = 100):
        """Nettoie tous les emails et met à jour la base"""
        total_emails = self.get_email_count()
        print(f"\n📧 Nombre total d'emails à traiter: {total_emails:,}")

        total_chars_saved = 0
        total_tokens_saved = 0
        processed = 0
        errors = 0

        with tqdm(total=total_emails, desc="🧹 Nettoyage", unit="email") as pbar:
            offset = 0
            while offset < total_emails:
                emails = self.get_emails_batch(offset, batch_size)

                if not emails:
                    break

                for email in emails:
                    result = self.cleaner.clean_email_body(email['body'])

                    if self.update_cleaned_body(email['id'], result['cleaned_body']):
                        processed += 1
                        total_chars_saved += result['chars_saved']
                        total_tokens_saved += result['tokens_saved']
                    else:
                        errors += 1

                    pbar.update(1)

                offset += batch_size

        print("\n" + "="*70)
        print("📊 STATISTIQUES DE NETTOYAGE")
        print("="*70)
        print(f"✅ Emails traités:       {processed:,}")
        print(f"❌ Erreurs:              {errors:,}")
        print(f"💾 Caractères supprimés: {total_chars_saved:,}")
        print(f"💰 Tokens économisés:    {total_tokens_saved:,}")

        if processed > 0:
            avg_reduction = (total_chars_saved / (total_chars_saved + sum(
                self.cleaner.clean_email_body(e['body'])['cleaned_length']
                for e in self.get_emails_batch(0, min(100, total_emails))
            ))) * 100
            print(f"📉 Réduction moyenne:    {avg_reduction:.1f}%")

        print("="*70 + "\n")


def main():
    """Fonction principale"""

    print("\n" + "="*70)
    print("🚀 EMAIL PATTERN DETECTOR & CLEANER v2.0")
    print("="*70)

    # Configuration
    ES_HOST = "localhost"
    ES_PORT = 9200

    DB_CONFIG = DatabaseConfig(
        host="localhost",
        port=3306,
        database="data-analysis",
        user="root",
        password="password"  # ⚠️ À MODIFIER
    )

    # Étape 1: Détection
    print("\n📍 ÉTAPE 1/3: Détection des patterns\n")

    detector = EmailPatternDetector(es_host=ES_HOST, es_port=ES_PORT, timeout=60)
    patterns = detector.detect_all_patterns(output_file="email_patterns.json")
    detector.print_summary()

    # Étape 2: Préparation
    print("\n📍 ÉTAPE 2/3: Préparation du nettoyeur\n")

    cleaner = EmailCleaner("email_patterns.json")
    print(f"✅ {len(cleaner.regex_patterns)} patterns chargés")

    # Étape 3: Nettoyage
    print("\n📍 ÉTAPE 3/3: Mise à jour de la base\n")

    db_cleaner = DatabaseCleaner(DB_CONFIG, cleaner)

    if db_cleaner.connect():
        try:
            db_cleaner.clean_all_emails(batch_size=100)
        finally:
            db_cleaner.disconnect()

    print("\n✅ Traitement terminé!\n")


if __name__ == "__main__":
    main()