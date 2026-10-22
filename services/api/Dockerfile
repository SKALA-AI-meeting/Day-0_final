FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends curl libasound2 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY app ./app
# 공개 8000, 내부 8001(compose에서 공개하지 않음). scenarios/는 /scenarios로 읽기 전용 마운트
EXPOSE 8000 8001
CMD ["python", "-m", "app.run"]
