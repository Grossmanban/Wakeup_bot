"""Скачать модели OCR при сборке образа: на сервере во время работы сеть для этого не нужна.

    python scripts/fetch_ocr_models.py /opt/ocr-models

Файлы проверяются по SHA-256; уже скачанные и целые не загружаются повторно.
"""

import hashlib
import sys
import urllib.request
from pathlib import Path

BASE = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/onnx/PP-OCRv5"
MODELS = {
    "ch_PP-OCRv5_det_mobile.onnx": (
        f"{BASE}/det/ch_PP-OCRv5_det_mobile.onnx",
        "4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae",
    ),
    "eslav_PP-OCRv5_rec_mobile.onnx": (
        f"{BASE}/rec/eslav_PP-OCRv5_rec_mobile.onnx",
        "08705d6721849b1347d26187f15a5e362c431963a2a62bfff4feac578c489aab",
    ),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for name, (url, digest) in MODELS.items():
        path = target / name
        if path.exists() and sha256(path) == digest:
            continue
        tmp = path.with_suffix(".part")
        with urllib.request.urlopen(url, timeout=120) as r, tmp.open("wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
        if sha256(tmp) != digest:
            tmp.unlink()
            raise SystemExit(f"контрольная сумма не совпала: {name}")
        tmp.rename(path)
        print(f"ok {name}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "ocr-models"))
