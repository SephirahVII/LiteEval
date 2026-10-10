FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY liteeval ./liteeval
COPY examples ./examples
RUN useradd --uid 1000 --create-home liteeval && mkdir /app/data && chown liteeval:liteeval /app/data
USER liteeval
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "liteeval.cli", "worker"]
