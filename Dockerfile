# BeatLab — single image for web, worker and bot.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# FFmpeg + ffprobe are required for the audio pipeline.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

RUN chmod +x /app/docker/entrypoint.sh

# Non-root user owns media storage.
RUN useradd --create-home --uid 1000 beatlab \
    && mkdir -p /var/lib/beatlab/media \
    && chown -R beatlab:beatlab /var/lib/beatlab /app

USER beatlab

EXPOSE 8000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["web"]
