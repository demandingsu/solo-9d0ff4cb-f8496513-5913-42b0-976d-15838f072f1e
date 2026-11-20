FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LAKE_DB=/data/archive.sqlite3

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY lake_archive /app/lake_archive

RUN mkdir -p /data && chown -R nobody:nogroup /data
USER nobody:nogroup
VOLUME ["/data"]
EXPOSE 8000

CMD ["python", "-m", "lake_archive.web"]
