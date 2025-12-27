# Étape 1 — Script Python d’indexation des boîtes PST

## Objectif

Ce script constitue **la première brique du pipeline d’enquête interne**.

Il a pour objectif de :
- parcourir des boîtes mails **au format PST** (une par interlocuteur)
- extraire les emails et pièces jointes **sans modification du contenu source**
- dédupliquer les emails présents dans plusieurs boîtes
- stocker les données **structurées et traçables** dans **MariaDB**

⚠️ Aucune analyse sémantique ou IA n’est effectuée à ce stade.

---

## Périmètre fonctionnel

### Entrées
- Répertoire contenant les fichiers `.pst`
- Une boîte PST = un interlocuteur

### Sorties
- Base MariaDB alimentée avec :
  - emails normalisés
  - métadonnées
  - liens entre emails et boîtes PST
  - pièces jointes

---

## Principes non négociables

1. **Le contenu brut n’est jamais modifié**
2. **Toute transformation est stockée séparément**
3. **Les emails sont dédupliqués**
4. **Chaque opération est traçable**

---

## Dépendances Python

### Librairies système

```bash
sudo apt install -y libpst-dev
```

### Librairies Python

```bash
pip install \
  python-pypff \
  sqlalchemy \
  pymysql \
  python-dateutil \
  pytz \
  beautifulsoup4 \
  lxml
```

---

## Schéma logique de base de données (simplifié)

### Table `mailbox`

| Champ | Type | Description |
|------|------|-------------|
| id | INT | Identifiant interne |
| owner_identifier | VARCHAR | Identité de l’interlocuteur |
| pst_filename | VARCHAR | Nom du fichier PST |
| imported_at | DATETIME | Date d’import |

---

### Table `email`

| Champ | Type | Description |
|------|------|-------------|
| id | BIGINT | Identifiant interne |
| message_fingerprint | CHAR(64) | Hash de déduplication |
| subject | TEXT | Objet |
| sender | VARCHAR | Expéditeur |
| sent_at | DATETIME | Date d’envoi |
| raw_body | LONGTEXT | Contenu brut |
| normalized_body | LONGTEXT | Copie normalisée |
| has_attachments | BOOLEAN | Présence de PJ |

Index recommandé : `message_fingerprint`

---

### Table `email_recipient`

| Champ | Type | Description |
|------|------|-------------|
| email_id | BIGINT | Référence email |
| recipient | VARCHAR | Destinataire |
| type | ENUM | TO / CC / BCC |

---

### Table `email_mailbox`

| Champ | Type | Description |
|------|------|-------------|
| email_id | BIGINT | Email |
| mailbox_id | INT | Boîte PST source |

---

### Table `attachment`

| Champ | Type | Description |
|------|------|-------------|
| id | BIGINT | Identifiant |
| email_id | BIGINT | Email parent |
| filename | VARCHAR | Nom |
| mime_type | VARCHAR | Type MIME |
| size | INT | Taille |
| storage_path | VARCHAR | Chemin disque |

---

## Logique du script `index_pst.py`

### Étape 1 — Initialisation
- Connexion MariaDB
- Chargement de la configuration
- Vérification des PST disponibles

### Étape 2 — Parcours des boîtes PST
- Ouverture via `pypff`
- Parcours récursif des dossiers

### Étape 3 — Extraction des emails
- Expéditeur, destinataires
- Date, objet, corps

### Étape 4 — Normalisation minimale
- Préserver `raw_body`
- Nettoyage HTML léger pour `normalized_body`

### Étape 5 — Déduplication
Empreinte SHA-256 basée sur :
```
sent_at + sender + recipients + subject + normalized_body
```

### Étape 6 — Pièces jointes
- Sauvegarde disque
- Métadonnées en base

### Étape 7 — Journalisation
- Emails traités
- Doublons
- Erreurs

---

## Critère de validation
- Aucun email perdu
- Aucun contenu modifié
- Import reproductible
