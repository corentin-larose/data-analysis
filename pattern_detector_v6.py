"""
Détecteur de patterns répétitifs dans les emails
Version optimisée - lecture directe depuis MariaDB
"""

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
                 password: str = "password"):
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password


class EmailPatternDetector:
    """Détecte les patterns répétitifs en lisant depuis MariaDB"""

    def __init__(self, db_config: DatabaseConfig):
        self.db_config = db_config
        self.connection = None
        self.detected_patterns = {
            'short_patterns': [],
            'long_patterns': [],
            'line_patterns': [],
            'disclaimer_patterns': [],
            'regex_patterns': []
        }

    def connect(self):
        """Établit la connexion à MariaDB"""
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
                return True
        except Error as e:
            print(f"❌ Erreur de connexion à MariaDB: {e}")
            return False

    def disconnect(self):
        """Ferme la connexion"""
        if self.connection and self.connection.is_connected():
            self.connection.close()

    def get_email_count(self) -> int:
        """Compte les emails avec normalized_body"""
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT COUNT(*) 
            FROM email 
            WHERE normalized_body IS NOT NULL 
              AND normalized_body != ''
        """)
        count = cursor.fetchone()[0]
        cursor.close()
        return count

    def load_emails(self, max_emails: int = 50000) -> List[str]:
        """
        Charge les emails depuis la table email
        Lit depuis normalized_body
        """
        print(f"   📥 Chargement de {max_emails:,} emails depuis MariaDB...")

        cursor = self.connection.cursor()

        query = """
            SELECT normalized_body
            FROM email
            WHERE normalized_body IS NOT NULL 
              AND normalized_body != ''
              AND LENGTH(normalized_body) > 50
            LIMIT %s
        """

        cursor.execute(query, (max_emails,))

        bodies = []
        for (body,) in tqdm(cursor, desc="      Lecture", unit="email", total=max_emails):
            if body:
                bodies.append(body)

        cursor.close()

        print(f"      ✓ {len(bodies):,} emails chargés")

        return bodies

    def extract_ngrams(self, text: str, n: int = 5) -> List[str]:
        """Extrait les n-grams d'un texte"""
        if not text:
            return []

        words = text.lower().split()
        if len(words) < n:
            return []

        ngrams = []
        for i in range(len(words) - n + 1):
            ngram = ' '.join(words[i:i + n])
            if len(ngram) >= 20:  # Minimum 20 caractères
                ngrams.append(ngram)

        return ngrams

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

        print(f"   📊 Comptage des occurrences ({len(all_ngrams):,} n-grams trouvés)...")
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

        return sorted(patterns, key=lambda x: x['doc_count'], reverse=True)[:100]

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

        return sorted(patterns, key=lambda x: x['doc_count'], reverse=True)[:100]

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
            'virus', 'legally binding', 'propriété', 'diffusion',
            'unsubscribe', 'se désabonner', 'cliquez ici'
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

        return sorted(patterns, key=lambda x: x['doc_count'], reverse=True)[:50]

    def detect_all_patterns(
            self,
            output_file: str = "detected_patterns.json",
            max_emails: int = 50000
    ):
        """
        Lance toutes les détections
        """
        print("\n" + "=" * 70)
        print("🔍 DÉTECTION DES PATTERNS RÉPÉTITIFS")
        print("=" * 70 + "\n")

        # Connexion
        if not self.connect():
            print("❌ Impossible de se connecter à la base")
            return self.detected_patterns

        try:
            # Stats
            total_emails = self.get_email_count()
            print(f"📊 Table 'email': {total_emails:,} emails avec normalized_body")

            if total_emails == 0:
                print("❌ Aucun email trouvé avec normalized_body!")
                return self.detected_patterns

            # Limiter pour optimiser
            sample_size = min(max_emails, total_emails)
            print(f"📦 Analyse sur {sample_size:,} emails\n")

            # Charger les emails
            bodies = self.load_emails(sample_size)

            if not bodies:
                print("❌ Impossible de charger les emails")
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

        finally:
            self.disconnect()

        return self.detected_patterns

    def generate_regex_patterns(self, min_length: int = 30) -> List[str]:
        """
        Convertit les patterns en regex
        IMPORTANT: Trie par longueur décroissante pour appliquer les plus longs d'abord
        """
        regex_patterns = []
        seen = set()

        # Traiter tous les types de patterns
        all_patterns = (
                self.detected_patterns.get('long_patterns', []) +
                self.detected_patterns.get('line_patterns', []) +
                self.detected_patterns.get('disclaimer_patterns', [])
        )

        # Collecter tous les patterns avec leur longueur
        patterns_with_length = []
        for pattern in all_patterns:
            text = pattern['text'].strip()
            if len(text) >= min_length and text not in seen:
                patterns_with_length.append((text, len(text)))
                seen.add(text)

        # TRIER PAR LONGUEUR DÉCROISSANTE (les plus longs en premier)
        patterns_with_length.sort(key=lambda x: x[1], reverse=True)

        # Générer les regex dans l'ordre
        for text, _ in patterns_with_length:
            # Échapper et rendre flexible
            escaped = re.escape(text)
            # Permettre variations d'espaces
            flexible = escaped.replace(r'\ ', r'\s+')
            regex_patterns.append(flexible)

        self.detected_patterns['regex_patterns'] = regex_patterns

        print(
            f"   ℹ️  Patterns triés par longueur (plus long: {patterns_with_length[0][1] if patterns_with_length else 0} chars)")

        return regex_patterns

    def print_summary(self):
        """Affiche un résumé"""
        print("\n" + "=" * 70)
        print("📊 RÉSUMÉ DES PATTERNS DÉTECTÉS")
        print("=" * 70)

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

            # Top 5
            for i, pattern in enumerate(patterns[:5], 1):
                text = pattern['text'][:65]
                count = pattern.get('doc_count', 0)
                print(f"   {i}. [{count:>4}x] {text}...")

        print(f"\n📦 Total: {total} patterns détectés")
        print(f"🔧 Regex: {len(self.detected_patterns.get('regex_patterns', []))}")
        print("=" * 70)


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
        """
        Compile les patterns
        IMPORTANT: Les regex sont déjà triées par longueur décroissante
        """
        compiled = []

        # Patterns détectés (déjà triés par longueur décroissante)
        for pattern_str in self.patterns.get('regex_patterns', []):
            try:
                # Ajout de re.DOTALL par rapport à la V5
                compiled.append(re.compile(pattern_str, re.MULTILINE | re.IGNORECASE | re.DOTALL))
            except:
                pass

        # Patterns statiques robustes - triés du plus long au plus court
        static = [
            # Disclaimers complets (les plus longs d'abord)
            r'(?i)(confidential|confidentiel).{0,500}?(unauthorized|non autorisée|interdite).{0,200}',
            r'(?i)(disclaimer|avertissement).{0,400}?(modification|alter|falsif).{0,200}',
            r'(?i)(destinataire|intended recipient).{0,300}?(erreur|error|immediately).{0,200}',
            r'(?i)if you have received this.{0,300}?(delete|détruire|supprimer).{0,100}',
            r'(?i)(ne pas imprimer|do not print|think before printing).{0,150}?(environnement|environment)?',

            # Blocs avec séparateurs
            r'(?m)\*{20,}[\s\S]{0,500}?\*{20,}',
            r'(?m)={20,}[\s\S]{0,500}?={20,}',
            r'(?m)-{20,}[\s\S]{0,500}?-{20,}',

            # Signatures et footers
            r'(?m)\n--\s*\n.*$',
            r'(?m)\n__\s*\n.*$',
            r'(?i)sent from my (iphone|android|mobile|blackberry).*$',
            r'(?i)get outlook for.*$',
            r'(?i)envoyé de(puis)? mon (iphone|android|mobile|smartphone).*$',
            r'(?i)obtenez outlook pour.*$',

            # Citations et séparateurs simples
            r'(?m)^>.*$',
            r'(?m)^_{5,}$',
            r'(?m)^-{5,}$',
            r'(?m)^={5,}$',
            r'(?m)^\*{5,}$'
        ]

        for pattern_str in static:
            try:
                compiled.append(re.compile(pattern_str, re.MULTILINE | re.IGNORECASE | re.DOTALL))
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
    """Met à jour la colonne cleaned_body dans MariaDB"""

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
        """Compte les emails à traiter"""
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT COUNT(*) 
            FROM email 
            WHERE normalized_body IS NOT NULL 
              AND normalized_body != ''
        """)
        count = cursor.fetchone()[0]
        cursor.close()
        return count

    def get_emails_batch(self, offset: int, limit: int) -> List[Dict]:
        """Récupère un lot d'emails depuis normalized_body"""
        cursor = self.connection.cursor(dictionary=True)
        query = """
            SELECT id, normalized_body as body
            FROM email
            WHERE normalized_body IS NOT NULL 
              AND normalized_body != ''
            LIMIT %s OFFSET %s
        """
        cursor.execute(query, (limit, offset))
        emails = cursor.fetchall()
        cursor.close()
        return emails

    def update_cleaned_body(self, email_id: int, cleaned_body: str) -> bool:
        """Update cleaned_body"""
        try:
            cursor = self.connection.cursor()
            query = "UPDATE email SET cleaned_body = %s WHERE id = %s"
            cursor.execute(query, (cleaned_body, email_id))
            self.connection.commit()
            cursor.close()
            return True
        except Error:
            return False

    def clean_all_emails(self, batch_size: int = 1000):
        """Traite tous les emails"""
        total = self.get_email_count()
        print(f"\n📧 {total:,} emails à traiter (avec normalized_body)")

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

        print("\n" + "=" * 70)
        print("📊 RÉSULTATS FINAUX")
        print("=" * 70)
        print(f"✅ Emails traités:      {processed:,}")
        print(f"💰 Tokens économisés:   ~{total_saved:,}")

        if processed > 0:
            avg_reduction = (total_saved * 4) / processed
            print(f"📉 Réduction moyenne:   ~{avg_reduction:.0f} chars/email")

        print("=" * 70 + "\n")


def main():
    """Main"""

    print("\n" + "=" * 70)
    print("🚀 EMAIL PATTERN DETECTOR & CLEANER v4.0")
    print("   (Lecture directe depuis MariaDB - normalized_body)")
    print("=" * 70)

    # Config
    DB_CONFIG = DatabaseConfig(
        host="localhost",
        port=3306,
        database="data-analysis",
        user="root",
        password="password"  # ⚠️ MODIFIER ICI
    )

    # Étape 1: Détection
    print("\n📍 ÉTAPE 1/3: Détection des patterns\n")

    detector = EmailPatternDetector(DB_CONFIG)

    # Analyser 50000 emails (tu peux augmenter si besoin)
    patterns = detector.detect_all_patterns(
        output_file="email_patterns.json",
        max_emails=50000  # ⚠️ AJUSTABLE
    )

    detector.print_summary()

    # Étape 2: Préparation
    print("\n📍 ÉTAPE 2/3: Préparation du cleaner\n")

    cleaner = EmailCleaner("email_patterns.json")
    print(f"✅ {len(cleaner.regex_patterns)} patterns de nettoyage chargés")

    # Étape 3: Nettoyage
    print("\n📍 ÉTAPE 3/3: Mise à jour de cleaned_body\n")

    db_cleaner = DatabaseCleaner(DB_CONFIG, cleaner)

    if db_cleaner.connect():
        try:
            db_cleaner.clean_all_emails(batch_size=1000)  # Lots de 1000 pour aller vite
        finally:
            db_cleaner.disconnect()

    print("\n✅ Traitement terminé!\n")


if __name__ == "__main__":
    main()
