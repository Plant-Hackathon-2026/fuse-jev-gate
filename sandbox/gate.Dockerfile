# PLANT 데몬을 Linux에서 돌리는 이미지.
# 실행 시 FUSE 장치와 권한이 필요하다:
#   docker run --rm --device /dev/fuse --cap-add SYS_ADMIN fuse-jev-gate ...
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends fuse3 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /bin/uv

WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY . .

CMD ["pytest", "-v"]
