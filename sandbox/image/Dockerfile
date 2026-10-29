# Image sandbox chạy code của agent (docs/agent-harness.md mục 4).
# Build context = thư mục sandbox/ :  docker build -f sandbox/image/Dockerfile -t mep-sandbox:latest sandbox
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    MPLCONFIGDIR=/tmp/.mpl \
    MPLBACKEND=Agg \
    MEP_TEMPLATES=/opt/mep/templates

# LibreOffice headless (chuyển PDF, kiểm tra file) + font hỗ trợ tiếng Việt
# (Liberation = thay thế metric Times New Roman/Arial; DejaVu, Noto cho PDF reportlab)
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libreoffice-writer-nogui libreoffice-calc-nogui \
      fonts-dejavu-core fonts-liberation2 fonts-noto-core fontconfig \
 && rm -rf /var/lib/apt/lists/* \
 && fc-cache -f

COPY image/requirements.txt /opt/mep/requirements.txt
RUN pip install --no-cache-dir -r /opt/mep/requirements.txt

COPY helpers /opt/mep/helpers
RUN pip install --no-cache-dir /opt/mep/helpers && rm -rf /opt/mep/helpers/build
COPY templates /opt/mep/templates

# User không phải root; /workspace là volume (input/output), /tmp là tmpfs – rootfs chạy read-only
RUN useradd --uid 1000 --user-group --no-create-home --shell /usr/sbin/nologin sandbox \
 && mkdir -p /workspace/input /workspace/output \
 && chown -R sandbox:sandbox /workspace

USER sandbox
WORKDIR /workspace
CMD ["sleep", "infinity"]
