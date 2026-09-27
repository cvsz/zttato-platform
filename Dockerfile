FROM python:3.14-alpine3.23
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
RUN addgroup -S zttato && adduser -S -G zttato -h /srv zttato && mkdir -p /srv/data /srv/media && chown -R zttato:zttato /srv
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && python -m pip check \
    && rm -rf /usr/local/lib/python3.14/site-packages/pip \
              /usr/local/lib/python3.14/site-packages/pip-*.dist-info \
              /usr/local/lib/python3.14/site-packages/setuptools \
              /usr/local/lib/python3.14/site-packages/setuptools-*.dist-info \
              /usr/local/lib/python3.14/site-packages/_distutils_hack \
              /usr/local/lib/python3.14/ensurepip \
              /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.14
COPY --chown=zttato:zttato app/ ./app/
COPY --chown=zttato:zttato migrations/ ./migrations/
COPY --chown=zttato:zttato alembic.ini ./alembic.ini
COPY --chown=zttato:zttato web/ ./web/
USER zttato
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD python -c "import urllib.request; req = urllib.request.Request('http://127.0.0.1:8000/health/live', headers={'Host': 'zttato.zeaz.dev'}); urllib.request.urlopen(req, timeout=3)"
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000","--proxy-headers","--forwarded-allow-ips","127.0.0.1","--no-access-log"]
