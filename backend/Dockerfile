FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd -m appuser
USER appuser

EXPOSE 8000
# Migration'lar ayrı "migrate" servisinde (docker-compose.yml) bir kez koşar; API açılışında çalışmaz.
# Kuyruk işçisi aynı imajdan: python -m app.worker
# Graceful shutdown: SIGTERM'de yeni bağlantı kabul edilmez, süren istekler en fazla 20 sn içinde biter
# (compose stop_grace_period 30 sn > 20 sn). Keep-alive 65 sn > nginx upstream keepalive_timeout (30 sn):
# boştaki bağlantıyı önce nginx kapatır, "kapanmış bağlantıya istek" kaynaklı 502 oluşmaz.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--timeout-graceful-shutdown", "20", "--timeout-keep-alive", "65"]
