# PST Mailbox Extractor to MariaDB (2025 Edition)

This project provides a professional-grade tool to recursively scan and extract Microsoft Outlook PST files, indexing
them into a MariaDB database. It features native email deduplication (using SHA-256 fingerprinting) and automated
attachment extraction using the modern Libratom framework.

## Prerequisites

### System Dependencies (Debian/Ubuntu/macOS)

Libratom requires the libpff development headers to interface with PST files.

**On Ubuntu/Debian:**
sudo apt update
sudo apt install -y python3-dev libpff-dev build-essential

**On macOS (via Homebrew):**
brew install autoconf automake libtool
xcode-select --install

### Python Environment

It is highly recommended to use a virtual environment.

**Note for Python 3.14+:** If you encounter 'BackendUnavailable: Cannot import setuptools.build_meta', you must upgrade
your build tools manually within the virtual environment.

python3 -m venv venv
source venv/bin/activate

# Mandatory upgrade for modern Python versions

python3 -m pip install --upgrade pip setuptools wheel

# Install project dependencies

pip install -r requirements.txt

---

## Setup & Configuration

### 1. Database Initialization

Login to your MariaDB/MySQL instance and run the provided SQL schema:

mysql -u root -p < docs/database.sql

### 2. Configuration File

Update the config/config.ini file with your local database credentials and storage preferences:

[database]
host = localhost
user = your_user
password = your_password
database = data-analysis
port = 3306

[storage]

# Directory where extracted attachments will be stored

attachments_dir = ./data/attachments

---

## Usage

The main script scans a source directory recursively for any .pst files and processes them using the Libratom engine.

### Run the Extraction

python3 pst_extractor.py /path/to/your/pst/files

### Key Features:

1. Recursive Scan: Automatically discovers all PST files in the target directory tree.
2. Smart Deduplication: If an email is found in multiple PST files, it is stored only once in the 'email' table and
   linked to all source mailboxes for full traceability.
3. Content Normalization: HTML bodies are sanitized and normalized using BeautifulSoup to ensure identical emails
   produce the same fingerprint regardless of minor formatting variations.
4. Secure Attachment Storage: Attachments are saved to disk in subdirectories named after the parent email's unique
   SHA-256 fingerprint.

---

## Data Structure

- MariaDB Database: Stores structured metadata, sender/recipient relations, and message bodies (raw and normalized).
- Disk Storage: Binary attachments are stored at: ./data/attachments/{email_fingerprint}/{filename}.

---

## Important Notes

- Performance: Libratom is optimized for large-scale mail triage. Multi-GB PST files remain I/O intensive.
- Encoding: Full utf8mb4 support is enabled to handle modern email content, including emojis and complex character sets.
- Deduplication Logic: The unique fingerprint is calculated based on: sent_at + sender + recipients + subject +
  normalized_body.