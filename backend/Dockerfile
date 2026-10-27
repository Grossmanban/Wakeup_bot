FROM python:3.13-slim
# uv ставится из PyPI: ghcr.io бывает недоступен из российских сетей.
RUN pip install --no-cache-dir uv==0.11.32
# Библиотеки, нужные OpenCV внутри PaddleOCR.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
# WITH_OCR=0 — образ без PaddleOCR (меньше на ~1 ГБ) для серверов с OCR_ENGINE=none.
ARG WITH_OCR=1
RUN if [ "$WITH_OCR" = "1" ]; then EXTRA="--extra ocr"; else EXTRA=""; fi \
    && uv sync --frozen --no-dev $EXTRA --no-install-project
COPY . .
# По умолчанию — API; бот запускается с командой `python -m bot.main`.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
