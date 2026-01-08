"""
Détecteur de patterns répétitifs dans les emails via Elasticsearch
Utilise significant_text sur les champs shingles pour identifier les boilerplates
"""

from elasticsearch import Elasticsearch
import json
from typing import List, Dict, Set
import re


class EmailPatternDetector:
    """Détecte et extrait les patterns répétitifs des emails"""

    def __init__(self, es_host: str = "localhost", es_port: int = 9200):
        self.es = Elasticsearch([f"http://{es_host}:{es_port}"])
        self.index_name = "emails"
        self.detected_patterns = {
            'short_patterns': [],      # 5 mots
            'long_patterns': [],       # 8-10 mots
            'line_patterns': [],       # Lignes complètes
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
            print(f"Erreur lors de la recherche: {e}")
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
            print(f"Erreur lors de la recherche de lignes: {e}")
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
            "destinataire", "diffusion", "unauthorized"
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
            print(f"Erreur recherche disclaimers: {e}")
            return []

    def detect_all_patterns(self, output_file: str = "detected_patterns.json"):
        """
        Lance toutes les détections et sauvegarde les résultats
        """
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
                # Échapper les caractères spéciaux regex
                escaped = re.escape(text)
                # Permettre des variations mineures (espaces, ponctuation)
                flexible = escaped.replace(r'\ ', r'\s+')
                regex_patterns.append(flexible)

        # Lignes complètes
        for pattern in self.detected_patterns.get('line_patterns', []):
            text = pattern['text'].strip()
            if len(text) >= min_length:
                escaped = re.escape(text)
                # Matcher en début ou fin de ligne
                regex_patterns.append(f'^{escaped}$')

        self.detected_patterns['regex_patterns'] = regex_patterns
        return regex_patterns

    def print_summary(self):
        """Affiche un résumé des patterns détectés"""
        print("\n" + "="*70)
        print("📊 RÉSUMÉ DES PATTERNS DÉTECTÉS")
        print("="*70)

        for category, patterns in self.detected_patterns.items():
            if category == 'regex_patterns':
                continue

            print(f"\n🔹 {category.upper().replace('_', ' ')}: {len(patterns)} patterns")

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
            r'\n--\s*\n.*$',  # Signature après --
            r'\n__\s*\n.*$',  # Signature après __
            r'^>.*$',         # Citations
            r'(?i)sent from my (iphone|android|mobile).*$',
            r'(?i)get outlook for.*$',
        ]

    def _compile_patterns(self) -> List[re.Pattern]:
        """Compile les regex patterns"""
        compiled = []

        for pattern_str in self.patterns.get('regex_patterns', []):
            try:
                compiled.append(re.compile(pattern_str, re.MULTILINE | re.IGNORECASE))
            except re.error as e:
                print(f"Erreur compilation regex: {e}")

        # Patterns statiques
        for pattern_str in self.static_patterns:
            compiled.append(re.compile(pattern_str, re.MULTILINE | re.IGNORECASE))

        return compiled

    def clean_email_body(self, body: str) -> Dict:
        """
        Nettoie le body d'un email

        Returns:
            Dict avec body nettoyé et statistiques
        """
        original_length = len(body)
        cleaned = body

        # Application de tous les patterns
        for pattern in self.regex_patterns:
            cleaned = pattern.sub('', cleaned)

        # Nettoyage final
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)  # Max 2 sauts de ligne
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


def main():
    """Exemple d'utilisation"""

    # 1. Détection des patterns
    print("🚀 Lancement de la détection de patterns répétitifs\n")

    detector = EmailPatternDetector(es_host="localhost", es_port=9200)

    # Détection
    patterns = detector.detect_all_patterns(output_file="email_patterns.json")

    # Résumé
    detector.print_summary()

    # Génération des regex
    print("\n🔧 Génération des patterns regex...")
    regex_patterns = detector.generate_regex_patterns(min_length=30)
    print(f"   ✓ {len(regex_patterns)} regex générées")

    # Sauvegarder avec les regex
    with open("email_patterns.json", 'w', encoding='utf-8') as f:
        json.dump(detector.detected_patterns, f, indent=2, ensure_ascii=False)

    print("\n✅ Détection terminée ! Patterns prêts pour le nettoyage.")

    # 2. Test de nettoyage (exemple)
    print("\n" + "="*70)
    print("🧹 Test de nettoyage d'email")
    print("="*70)

    # Exemple d'email avec boilerplate
    test_email = """Bonjour,

Voici les informations demandées pour le projet.

Cordialement,
Jean Dupont

--
Jean Dupont
Directeur Commercial
Tél: 01 23 45 67 89
Email: j.dupont@example.com

AVERTISSEMENT : Ce message et toutes les pièces jointes sont confidentiels et
établis à l'intention exclusive de ses destinataires. Toute utilisation ou
diffusion non autorisée est interdite.

Ne pas imprimer ce mail sauf si nécessaire. Pensez à l'environnement.
"""

    cleaner = EmailCleaner("email_patterns.json")
    result = cleaner.clean_email_body(test_email)

    print(f"\n📄 Original ({result['original_length']} chars, ~{result['original_length']//4} tokens):")
    print(test_email[:200] + "...")

    print(f"\n✨ Nettoyé ({result['cleaned_length']} chars, ~{result['cleaned_length']//4} tokens):")
    print(result['cleaned_body'])

    print(f"\n💰 Économie : {result['reduction_pct']:.1f}% ({result['tokens_saved']} tokens)")


if __name__ == "__main__":
    main()