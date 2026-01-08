import argparse
import json
import configparser
from pathlib import Path
from sqlalchemy import create_engine, text
from openai import OpenAI

def get_config():
    """Charge la configuration depuis config/config.ini"""
    config = configparser.ConfigParser()
    config_path = Path(__file__).parent / "config" / "config.ini"
    if not config_path.exists():
        raise FileNotFoundError(f"Fichier de configuration introuvable : {config_path}")

    config.read(config_path)
    return config

def get_db_engine(config):
    """Crée l'engine SQLAlchemy à partir de la config INI"""
    user = config['database']['user']
    password = config['database']['password']
    host = config['database']['host']
    database = config['database']['database']
    port = config.get('database', 'port', fallback='3306')

    url = f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}"
    return create_engine(url)

def clean_emails_batch(batch_count):
    config = get_config()
    engine = get_db_engine(config)

    # Configuration pour Ollama
    client = OpenAI(
        base_url="http://localhost:11434/v1",
        api_key="ollama"
    )

    # 1. Extraction des données
    query_select = text("""
        SELECT id, normalized_body
        FROM email
        WHERE cleaned_body IS NULL
        AND normalized_body IS NOT NULL
        LIMIT :limit
    """)

    with engine.connect() as conn:
        results = conn.execute(query_select, {"limit": batch_count}).fetchall()
        emails_to_process = [{"id": r.id, "body": r.normalized_body} for r in results]

    if not emails_to_process:
        print("Aucun email à traiter.")
        return

    # 2. Appel au LLM
    prompt = """
    You are a deterministic text-cleaning engine.

    Your task is to CLEAN email bodies by DELETING text only.

    You are NOT allowed to rewrite, paraphrase, summarize, or reorder text.

    FORMAL CONSTRAINTS (MANDATORY):
    - The value of "cleaned_body" MUST be a contiguous subset of the original "body".
    - Every character in "cleaned_body" MUST appear verbatim in the original "body".
    - You may ONLY remove complete sections such as:
      - Email signatures
      - Legal disclaimers
      - Previous email threads, replies, or quoted messages
    - You MUST NOT modify spelling, punctuation, casing, or wording.
    - If nothing must be removed, return the original "body" unchanged.

    INVALID OUTPUT CONDITIONS:
    - Any paraphrasing or rewording
    - Any summary or description
    - Any explanatory text
    - Any text not present in the original "body"

    INPUT FORMAT:
    A JSON array of objects:
    [{ "id": number, "body": string }, ...]

    OUTPUT FORMAT (STRICT):
    {
      "results": [
        { "id": number, "cleaned_body": string }
      ]
    }

    IMPORTANT:
    If you cannot comply strictly with these constraints for an email,
    return the original "body" unchanged for that email.
    """

    try:
        response = client.chat.completions.create(
            model="qwen2.5:14b",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(emails_to_process)}
            ],
            temperature=0,
            response_format={"type": "json_object"}
        )

        content = response.choices[0].message.content
        print(f"--- Réponse du LLM ---\n{content}\n----------------------")

        content = content.strip()
        if content.startswith("```json"):
            content = content[7:-3].strip()
        elif content.startswith("```"):
            content = content[3:-3].strip()

        raw_content = json.loads(content)

        # Logique plus souple pour extraire les items
        items = []
        if isinstance(raw_content, dict):
            # On cherche la première valeur qui est une liste (que ce soit 'results', 'events', 'emails'...)
            for val in raw_content.values():
                if isinstance(val, list):
                    items = val
                    break
        elif isinstance(raw_content, list):
            items = raw_content

        # 3. Mise à jour en base de données
        if items:
            
            query_update = text("UPDATE email SET cleaned_body = :cleaned_body WHERE id = :id")
            with engine.begin() as conn:
                for item in items:
                    # On vérifie que les clés nécessaires existent
                    if 'id' in item and ('cleaned_body' in item or 'description' in item):
                        # On accepte 'description' au cas où il s'obstine à l'appeler ainsi
                        body = item.get('cleaned_body') or item.get('description')
                        conn.execute(query_update, {
                            "cleaned_body": body,
                            "id": item['id']
                        })
            print(f"Succès : {len(items)} emails mis à jour.")
        else:
            print("Le LLM a renvoyé une structure vide.")

    except Exception as e:
        print(f"Erreur lors du traitement : {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Nettoyage des emails via LLM (Ollama)")
    parser.add_argument("batch_count", type=int, help="Nombre d'emails à traiter")
    args = parser.parse_args()

    clean_emails_batch(args.batch_count)