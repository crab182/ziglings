FROM python:3.12-slim

WORKDIR /app

# TORCH_VARIANT: "cpu" (default) or a CUDA tag like "cu124" for GPU builds.
ARG TORCH_VARIANT=cpu

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir "torch>=2.7.0" --index-url https://download.pytorch.org/whl/${TORCH_VARIANT} \
    && pip install --no-cache-dir -r requirements.txt

# Pre-download models so first query doesn't block
RUN python -c "\
from sentence_transformers import SentenceTransformer, CrossEncoder; \
SentenceTransformer('all-MiniLM-L6-v2'); \
CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"

# Pre-download Docling layout/table models so first PDF upload doesn't block
RUN python -c "from docling.utils.model_downloader import download_models; download_models()"

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
