#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path
import mailbox
import zlib
import shutil

if len(sys.argv) != 3:
    print(f"Usage: {sys.argv[0]} <source_directory> <destination_directory>")
    sys.exit(1)

SRC_DIR = Path(sys.argv[1]).resolve()
DST_DIR = Path(sys.argv[2]).resolve()

MBOX_DIR = DST_DIR / "mbox"
EML_DIR = DST_DIR / "eml"
ATTACH_DIR = DST_DIR / "attachments"

for d in [MBOX_DIR, EML_DIR, ATTACH_DIR]:
    d.mkdir(parents=True, exist_ok=True)

for cmd in ["readpst", "ripmime"]:
    if subprocess.run(["which", cmd], capture_output=True).returncode != 0:
        print(f"❌ Outil manquant : {cmd}")
        sys.exit(1)

for pst_file in SRC_DIR.rglob("*.pst"):
    base = pst_file.stem
    print(f"➜ Extraction PST : {pst_file}")

    out_mbox_dir = MBOX_DIR / base
    out_mbox_dir.mkdir(parents=True, exist_ok=True)

    subprocess.run(["readpst", "-o", str(out_mbox_dir), str(pst_file)], check=True)

    for mbox_file in out_mbox_dir.rglob("*.mbox"):
        print(f"  → mbox : {mbox_file}")

        eml_subdir = EML_DIR / base / mbox_file.stem
        eml_subdir.mkdir(parents=True, exist_ok=True)

        attach_subdir = ATTACH_DIR / base
        attach_subdir.mkdir(parents=True, exist_ok=True)

        mbox = mailbox.mbox(str(mbox_file))

        for idx, msg in enumerate(mbox):
            eml_bytes = msg.as_bytes()
            crc32 = f"{zlib.crc32(eml_bytes) & 0xffffffff:08X}"

            # Écriture de l'EML avec CRC32
            eml_file = eml_subdir / f"{idx:05d}_{crc32}.eml"
            with eml_file.open("wb") as f:
                f.write(eml_bytes)

            # Extraire les PJ dans un dossier temporaire
            temp_attach_dir = attach_subdir / "tmp"
            temp_attach_dir.mkdir(parents=True, exist_ok=True)
            subprocess.run([
                "ripmime",
                "-i", str(eml_file),
                "-d", str(temp_attach_dir),
                "-q",
                "--overwrite",
                "--no-paranoid"
            ], check=True)

            # Renommer toutes les PJ pour inclure le CRC32
            for file in temp_attach_dir.iterdir():
                if file.is_file():
                    new_name = f"{file.stem}_{crc32}{file.suffix}"
                    file.rename(attach_subdir / new_name)
            # Supprimer le dossier temporaire
            shutil.rmtree(temp_attach_dir)

    print(f"✔ Terminé pour : {pst_file}")

print(f"🎉 Extraction complète terminée.\n📂 Résultats dans : {DST_DIR}")