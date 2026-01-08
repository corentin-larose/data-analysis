#!/usr/bin/env python3

import argparse
import hashlib
import shutil
from tqdm import tqdm
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
import csv

import magic
from PIL import Image
from opennsfw2 import predict_image
import warnings

Image.MAX_IMAGE_PIXELS = None  # supprime la limite

def sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(8192), b""):
            h.update(b)
    return h.hexdigest()


def is_image(path):
    try:
        if not magic.from_file(str(path), mime=True).startswith("image/"):
            return False
        with Image.open(path) as img:
            img.verify()
        return True
    except Exception:
        return False


def process_file(args):
    path, threshold, out_dir = args
    try:
        result = predict_image(str(path))

        # robust handling
        if isinstance(result, dict):
            score = result.get("nsfw", 0.0)
        elif isinstance(result, (float, int)):
            score = float(result)
        else:
            score = 0.0

        if score >= threshold:
            digest = sha256(path)
            ext = "jpg"  # tu peux aussi détecter via PIL si tu veux
            out = out_dir / f"{digest}.{ext}"
            shutil.copy2(path, out)
            return (str(path), score, "COPIED")

        return (str(path), score, "OK")

    except Exception as e:
        return (str(path), 0.0, f"ERROR: {e}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("--output", default="found")
    parser.add_argument("--threshold", type=float, default=0.7)
    parser.add_argument("--workers", type=int, default=4)

    args = parser.parse_args()

    root = Path(args.path)
    out = Path(args.output)
    out.mkdir(exist_ok=True)

    files = [f for f in root.rglob("*") if f.is_file()]

    print(f"📁 Fichiers détectés : {len(files)}")

    valid_images = [f for f in files if is_image(f)]
    print(f"🖼️  Images valides : {len(valid_images)}")

    log_file = out / "scan_log.csv"

    with open(log_file, "w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["file", "nsfw_score", "status"])

        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            for result in tqdm(
                pool.map(
                    process_file,
                    [(f, args.threshold, out) for f in valid_images]
                ),
                total=len(valid_images),
            ):
                writer.writerow(result)


if __name__ == "__main__":
    main()