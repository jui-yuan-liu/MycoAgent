FROM python:3.12-slim

LABEL org.opencontainers.image.title="MycoAgent"

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src src
RUN pip install --no-cache-dir ".[postgres]"

EXPOSE 8080 9001 9002
CMD ["python", "-m", "mycoagent", "manager", "--host", "0.0.0.0"]
