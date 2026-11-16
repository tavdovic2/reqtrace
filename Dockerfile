# reqTrace API: FastAPI + Docling + FastEmbed, CPU only.
FROM python:3.12.16-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Shared libraries needed by Docling's PDF and image stack.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CPU-only PyTorch first, so that Docling does not pull the much larger CUDA build.
RUN pip install torch==2.15.0 torchvision==0.29.2 --index-url https://download.pytorch.org/whl/cpu

# Dependencies first (cached while only application code changes), then the package itself.
COPY pyproject.toml README.md LICENSE ./
RUN mkdir app && touch app/__init__.py && pip install . && rm -rf app
COPY app ./app
RUN pip install --no-deps .

# Model downloads (Docling layout models, FastEmbed models) are cached on the data volume,
# so they happen once, on the first upload, and survive container rebuilds.
ENV DATA_DIR=/data \
    MODELS_CACHE_DIR=/data/models/fastembed \
    HF_HOME=/data/models/huggingface \
    HF_HUB_DISABLE_SYMLINKS_WARNING=1

RUN useradd --create-home --uid 1000 reqtrace && mkdir -p /data && chown reqtrace /data
USER reqtrace
VOLUME ["/data"]

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=4)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
