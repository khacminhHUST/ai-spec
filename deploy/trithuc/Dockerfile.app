# Backend MEP – image dùng chung cho `api` và `worker`.
# Build (từ thư mục deploy/trithuc): docker compose build
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/models/hf \
    MEP_EASYOCR_DIR=/models/easyocr \
    MEP_OCR_ENGINE=easyocr

# libgl/glib: opencv (Docling, EasyOCR)
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
# torch bản CPU trước: requirements.txt ghim torch PyPI, trên Linux bản đó kéo theo CUDA (vài GB).
# "==2.11.0" trong requirements khớp luôn "2.11.0+cpu" nên pip không cài lại.
# Server có GPU NVIDIA: đổi index sang https://download.pytorch.org/whl/cu128.
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch==2.11.0 torchvision==0.26.0 \
 && pip install -r requirements.txt

COPY app ./app
COPY config ./config
COPY skills ./skills
COPY deploy/trithuc/entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
# 1 tiến trình: SandboxManager nằm trong bộ nhớ tiến trình (CLAUDE.md – giới hạn production #6)
CMD ["uvicorn", "app.trithuc_main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
