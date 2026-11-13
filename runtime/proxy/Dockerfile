FROM debian:13-slim AS download
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
RUN set -eu; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) checksum=699a1b510aa4396b2176d32d1181920399ebf1e267835da6f3b598d0f7c24968 ;; \
      arm64) checksum=436441f76ee2f7fe21b5b2d426d3cb261a480455a704865789ad533e4273ec26 ;; \
      *) echo "Unsupported proxy image architecture: $arch" >&2; exit 1 ;; \
    esac; \
    curl --fail --location --silent --show-error \
      "https://github.com/SagerNet/sing-box/releases/download/v1.14.4/sing-box-1.14.4-linux-${arch}.tar.gz" \
      -o /tmp/sing-box.tar.gz \
    && echo "$checksum  /tmp/sing-box.tar.gz" | sha256sum --check - \
    && tar -xzf /tmp/sing-box.tar.gz -C /tmp \
    && install -m 0755 "/tmp/sing-box-1.14.4-linux-${arch}/sing-box" /usr/local/bin/sing-box

FROM debian:13-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates python3 iproute2 iptables curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=download /usr/local/bin/sing-box /usr/local/bin/sing-box
COPY runtime/proxy/entrypoint.py /app/entrypoint.py
COPY runtime/proxy/browser_entrypoint.py /app/browser_entrypoint.py
COPY scripts/check_proxy_route.py /app/scripts/check_proxy_route.py
COPY scripts/reload_proxy_session.py /app/scripts/reload_proxy_session.py
COPY LICENSE THIRD_PARTY_NOTICES.md /usr/share/doc/tungtungtungbrowser/
ENV PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["python3", "/app/entrypoint.py"]
