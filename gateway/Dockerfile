FROM python:3.11-slim

WORKDIR /app
ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV PIP_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cpu
ENV PIP_DEFAULT_TIMEOUT=120

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV ROUTELLM_CACHE_DIR=/app/.routellm_cache
ENV MODELSCOPE_CACHE_DIR=/app/.modelscope_cache
ENV HF_ENDPOINT=https://hf-mirror.com
ENV OPENAI_API_KEY=local-placeholder
CMD ["sh", "-c", "python warmup.py && uvicorn app:app --host 0.0.0.0 --port 4000"]
