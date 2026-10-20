ARG BUILD_FROM
FROM ${BUILD_FROM}
ARG BUILD_VERSION
ENV APP_VERSION=${BUILD_VERSION}

COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt

COPY app /app
WORKDIR /app
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD ["python3", "-m", "enocean_proxy.health"]
CMD ["python3", "-m", "enocean_proxy"]
