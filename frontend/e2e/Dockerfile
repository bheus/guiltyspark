# The browser image tag must equal the @playwright/test version in
# package-lock.json; run.sh reads it from there and passes it in.
ARG PLAYWRIGHT_VERSION
FROM mcr.microsoft.com/playwright:v${PLAYWRIGHT_VERSION}-noble

# The image is linux/arm64 only (run.sh), so one pinned checksum covers it.
ARG UV_VERSION=0.11.7
ARG UV_SHA256=f2ee1cde9aabb4c6e43bd3f341dadaf42189a54e001e521346dc31547310e284
RUN apt-get update \
 && apt-get install -y --no-install-recommends rsync curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && curl -fsSL -o /tmp/uv.tar.gz \
      "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-aarch64-unknown-linux-gnu.tar.gz" \
 && echo "${UV_SHA256}  /tmp/uv.tar.gz" | sha256sum -c - \
 && tar -xzf /tmp/uv.tar.gz -C /usr/local/bin --strip-components=1 \
 && rm /tmp/uv.tar.gz

ENV NPM_CONFIG_UPDATE_NOTIFIER=false \
    UV_PYTHON=3.12 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_INSTALL_DIR=/opt/uv-python
RUN uv python install 3.12
