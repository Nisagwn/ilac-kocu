#!/usr/bin/env sh
# Yerel geliştirme için kendinden imzalı TLS sertifikası üretir: infra/nginx/certs/server.{crt,key}
# Yalnızca localhost içindir; tarayıcı uyarı verir. Üretimde Let's Encrypt vb. gerçek sertifika kullanın.
#
#   sh infra/nginx/gen-cert.sh                 # CN/SAN: localhost, 127.0.0.1
#   sh infra/nginx/gen-cert.sh demo.local      # ek ana makine adı
#
# openssl kurulu değilse betik alpine/openssl Docker imajıyla aynı komutu çalıştırır.
set -eu

DIR="$(cd "$(dirname "$0")" && pwd)/certs"
HOST="${1:-localhost}"
mkdir -p "$DIR"
# Windows Git Bash (MSYS): "/CN=..." konu adı bir dosya yoluna çevrilmesin
export MSYS2_ARG_CONV_EXCL="/CN="

SAN="DNS:localhost,IP:127.0.0.1"
[ "$HOST" != "localhost" ] && SAN="DNS:${HOST},${SAN}"

if command -v openssl >/dev/null 2>&1; then
  openssl req -x509 -nodes -newkey rsa:2048 -sha256 -days 365 \
    -keyout "$DIR/server.key" -out "$DIR/server.crt" \
    -subj "/CN=${HOST}/O=Ilac Kocu (yerel gelistirme)" \
    -addext "subjectAltName=${SAN}"
else
  docker run --rm -v "$DIR:/certs" alpine/openssl:3.5.2 req -x509 -nodes -newkey rsa:2048 \
    -sha256 -days 365 -keyout /certs/server.key -out /certs/server.crt \
    -subj "/CN=${HOST}/O=Ilac Kocu (yerel gelistirme)" -addext "subjectAltName=${SAN}"
fi
chmod 600 "$DIR/server.key" 2>/dev/null || true
echo "Sertifika: $DIR/server.crt (365 gün, SAN: ${SAN})"
echo "TLS'i açmak için: docker compose -f docker-compose.yml -f infra/nginx/compose.tls.yml up -d"
