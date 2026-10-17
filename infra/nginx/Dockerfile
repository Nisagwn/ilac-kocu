# Ters proxy + yük dengeleyici. 1.27.3+ gerekir: upstream `server ... resolve` (Docker DNS ile
# çoğaltılmış konteynerleri yeniden yükleme olmadan izleme) açık kaynak sürümde bu sürümle geldi.
FROM nginx:1.28.0-alpine

# Şablonlarda yalnızca NGINX_ ile başlayan değişkenler yerine konur ($host, $remote_addr... dokunulmaz)
ENV NGINX_ENVSUBST_FILTER=^NGINX_ \
    NGINX_RATE_LIMIT=20r/s

RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/nginx.conf
COPY snippets/ /etc/nginx/snippets/
COPY conf.d/ /etc/nginx/conf.d/
COPY templates/ /etc/nginx/templates/

EXPOSE 80 443 8081
HEALTHCHECK --interval=10s --timeout=3s --retries=3 CMD wget -q -O /dev/null http://127.0.0.1/nginx-health || exit 1
