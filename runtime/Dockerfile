FROM debian:13-slim

# QEMU and reims-vgpu are built once on the research host, then mounted read-only.
# Runtime exposes no hardware GPU devices; the launcher explicitly selects lavapipe.
# Mesa 25.0.7 lacks the fragment interlock required by raster-order pipelines.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && printf '%s\n' \
      'Types: deb' \
      'URIs: https://deb.debian.org/debian' \
      'Suites: trixie-backports' \
      'Components: main' \
      'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg' \
      > /etc/apt/sources.list.d/ttt-mesa-interlock.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
    python3 bash coreutils util-linux \
    libglib2.0-0t64 libpixman-1-0 liblzfse1 libslirp0 libssh-4 libfdt1 \
    libzstd1 libnettle8t64 libgnutls30t64 libgtk-3-0t64 libsdl2-2.0-0 \
    libusb-1.0-0 libsndio7.0 libcurl4t64 \
    libvulkan1 mesa-vulkan-drivers=26.1.6-1~bpo13+1 vulkan-tools \
    libx11-6 libxss1 libwayland-client0 libxkbcommon0 libdrm2 libgbm1 \
    libegl1 libgl1 libgomp1 libatomic1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY tungtungtungbrowser /app/tungtungtungbrowser
COPY scripts/run_vm.py /app/scripts/run_vm.py
COPY scripts/measure_ready.py /app/scripts/measure_ready.py
COPY scripts/serve_probe.py /app/scripts/serve_probe.py
COPY diagnostics /app/diagnostics
COPY LICENSE THIRD_PARTY_NOTICES.md /usr/share/doc/tungtungtungbrowser/
ENV PYTHONDONTWRITEBYTECODE=1 \
    XDG_CACHE_HOME=/vm/cache \
    MESA_SHADER_CACHE_DIR=/vm/cache/mesa \
    REIMS_VGPU_LOG=warn \
    REIMS_VGPU_CENSUS_DIR=""
ENTRYPOINT ["python3", "scripts/run_vm.py"]
