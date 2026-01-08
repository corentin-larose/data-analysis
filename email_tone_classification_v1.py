import mysql.connector
import json
import ollama
import logging
from typing import List, Dict

# Configuration
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': 'password',
    'database': 'data-analysis',
    'port': 3306
}

MODEL_NAME = 'mistral-nemo' # Recommandé pour la vitesse/efficacité
BATCH_SIZE = 10  # Nombre d'emails récupérés à chaque itération
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    force=True, # Indispensable si un autre logger a été initialisé avant
    handlers=[
        logging.FileHandler("tone_analysis.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def classify_email_tone(body: str) -> List[str]:
    """Appelle Ollama pour qualifier le ton."""
    if not body or len(body.strip()) < 10:
        return ["trop_court"]

    prompt = f"""
    Analyse le ton de l'email suivant.

    Catégories :
    neutral, professional, factual, informational, procedural, transactional, operational, respectful, polite,
    courteous, collaborative, constructive, cooperative, supportive, authoritative_legitimate, directive_professional,
    managerial, supervisory, expectation_setting, firm_but_fair, deadline_driven, priority_setting, accountable,
    performance_oriented, corrective, feedback_constructive, clarification_requested, misunderstanding_resolution,
    frustration_expressed, disagreement_respectful, concern_raised, dissatisfaction_professional, friendly_professional,
    cordial, light_humor_appropriate, implicit_but_standard, concise_to_the_point, culturally_direct,

    insistent, demanding, coercive, pressuring, intrusive, controlling, obsessive, time_pressure, authoritative,
    domineering, hierarchical_abuse, abuse_of_power, patronizing, infantilizing, intimidating, humiliating, belittling,
    demeaning, dismissive, condescending, gaslighting, manipulative, guilt_inducing, hostile, aggressive, threatening,
    menacing, punitive, retaliatory, passive_aggressive, sarcastic, ironic, double_bind, ambiguous_intent, veiled_threat,
    excluding, isolating, silencing, delegitimizing, undermining, emotionally_charged, anger, frustration, contempt,
    resentment, repetitive, pattern_like, escalating, persistent, sexualized_tone, inappropriate_familiarity,
    boundary_violating, suggestive, objectifying, flirtatious_unwanted, deflecting, blame_shifting, minimization,
    normalization, intimidation_through_norms
    
    Réponds UNIQUEMENT en JSON : {{"flags": ["categorie1", "categorie2"]}}

    Email : {body[:2000]} # On limite la taille pour éviter de saturer le contexte
    """

    try:
        response = ollama.generate(
            model=MODEL_NAME,
            prompt=prompt,
            format='json',
            options={'temperature': 0}
        )

        # Ajout d'un log pour voir ce que le LLM renvoie réellement en cas de doute
        logger.info(f"Réponse brute LLM : {response['response']}")

        result = json.loads(response['response'])
        return result.get('flags', [])
    except Exception as e:
        # exc_info=True va forcer l'affichage de toute la trace d'erreur dans le terminal et le log
        logger.error(f"Erreur lors de l'analyse du ton pour un email : {e}", exc_info=True)
        return ["erreur_ia"]

def main():
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)

        # 2. Boucle de traitement par batch
        # On ne sélectionne que les emails non encore traités (tone_flags IS NULL)
        processed_total = 0

        while True:
            cursor.execute(
                "SELECT A.id, A.cleaned_body FROM identity B LEFT JOIN email A ON A.sender_identity_id=B.id WHERE B.name IN ('Valérie COSTES-FORET', 'David LIORET') AND A.cleaned_body != '' AND A.tone_flags IS NULL LIMIT %s",
                (BATCH_SIZE,)
            )
            batch = cursor.fetchall()

            if not batch:
                logger.info("Tous les emails ont été traités ! 🎉")
                break

            updates = []
            for row in batch:
                email_id = row['id']
                content = row['cleaned_body']

                flags = classify_email_tone(content)
                updates.append((json.dumps(flags), email_id))

            # 3. Update groupé pour la performance
            cursor.executemany(
                "UPDATE email SET tone_flags = %s WHERE id = %s",
                updates
            )
            conn.commit()

            processed_total += len(batch)
            logger.info(f"Progression : {processed_total} emails analysés...")

    except mysql.connector.Error as err:
        logger.error(f"Erreur MariaDB : {err}")
    finally:
        if 'conn' in locals() and conn.is_connected():
            cursor.close()
            conn.close()

if __name__ == "__main__":
    main()