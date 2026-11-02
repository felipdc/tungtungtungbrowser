FROM debian:13-slim AS download
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
RUN curl --fail --location --silent --show-error \
      https://github.com/SagerNet/sing-box/releases/download/v1.14.4/sing-box-1.14.4-linux-amd64.tar.gz \
      -o /tmp/sing-box.tar.gz \
    && echo '699a1b510aa4396b2176d32d1181920399ebf1e267835da6f3b598d0f7c24968  /tmp/sing-box.tar.gz' | sha256sum --check - \
    && tar -xzf /tmp/sing-box.tar.gz -C /tmp

FROM debian:13-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates python3 iproute2 iptables curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=download /tmp/sing-box-1.14.4-linux-amd64/sing-box /usr/local/bin/sing-box
COPY runtime/proxy/entrypoint.py /app/entrypoint.py
ENV PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["python3", "/app/entrypoint.py"]
