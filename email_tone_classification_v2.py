import mysql.connector
import json
import ollama
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict

# Configuration
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': 'password',
    'database': 'data-analysis',
    'port': 3306
}

MODEL_NAME = 'mistral-nemo'
BATCH_SIZE = 100  # On augmente un peu le batch pour nourrir les threads
MAX_WORKERS = 6  # Nombre de threads simultanés (Idéal pour M1 Max)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    force=True,
    handlers=[logging.FileHandler("tone_analysis.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


def classify_email_tone(row: Dict) -> tuple:
    """Fonction exécutée par les threads."""
    email_id = row['id']
    body = row['cleaned_body']

    if not body or len(body.strip()) < 10:
        return email_id, ["trop_court"]

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
    
    Email : {body[:2000]}
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
        return email_id, result.get('flags', [])
    except Exception as e:
        logger.error(f"Erreur ID {email_id}: {e}")
        return email_id, ["erreur_ia"]


def main():
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)

        # Assurer la colonne JSON
        try:
            cursor.execute("ALTER TABLE email ADD COLUMN tone_flags JSON DEFAULT NULL")
            conn.commit()
        except:
            pass

        processed_total = 0

        while True:
            cursor.execute(
                """SELECT DISTINCT e.id, e.subject, e.cleaned_body
                   FROM email e
                            JOIN identity sender ON e.sender_identity_id = sender.id
                            JOIN email_recipient er ON e.id = er.email_id
                            JOIN identity recipient ON er.identity_id = recipient.id
                   WHERE sender.name IN ('Valérie COSTES-FORET')
                     AND recipient.name IN ('David LIORET')
                     AND e.cleaned_body != ''
                     AND e.tone_flags IS NULL
                   ORDER BY e.sent_at ASC LIMIT %s""",
                (BATCH_SIZE,)
            )
            rows = cursor.fetchall()
            if not rows:
                logger.info("Traitement terminé ! 🎉")
                break

            # Utilisation du ThreadPoolExecutor pour paralléliser les appels LLM
            updates = []
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                results = list(executor.map(classify_email_tone, rows))

                for email_id, flags in results:
                    updates.append((json.dumps(flags), email_id))

            # Update groupé pour la performance SQL
            cursor.executemany("UPDATE email SET tone_flags = %s WHERE id = %s", updates)
            conn.commit()

            processed_total += len(rows)
            logger.info(f"Progression : {processed_total} emails traités...")

    except Exception as e:
        logger.error(f"Erreur critique : {e}", exc_info=True)
    finally:
        if 'conn' in locals() and conn.is_connected():
            cursor.close()
            conn.close()


if __name__ == "__main__":
    main()
