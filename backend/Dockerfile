FROM python:3.13-slim
# uv ставится из PyPI: ghcr.io бывает недоступен из российских сетей.
RUN pip install --no-cache-dir uv==0.11.32
# Системные библиотеки для OpenCV (headless) и ONNX Runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libstdc++6 libgomp1 zlib1g \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
# WITH_OCR=0 — образ без OCR (меньше на ~300 МБ) для серверов с OCR_ENGINE=none.
ARG WITH_OCR=1
RUN if [ "$WITH_OCR" = "1" ]; then EXTRA="--extra ocr"; else EXTRA=""; fi \
    && uv sync --frozen --no-dev $EXTRA --no-install-project
# Модели OCR (~13 МБ) скачиваются при сборке и проверяются по SHA-256.
ENV OCR_MODELS_DIR=/opt/ocr-models
COPY scripts/fetch_ocr_models.py scripts/
RUN if [ "$WITH_OCR" = "1" ]; then python scripts/fetch_ocr_models.py "$OCR_MODELS_DIR"; fi
COPY . .
# По умолчанию — API; бот запускается с командой `python -m bot.main`.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
