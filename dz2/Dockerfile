FROM openapitools/openapi-generator-cli:v7.16.0 AS codegen
WORKDIR /build
COPY openapi ./openapi
RUN docker-entrypoint.sh generate -c openapi/generator-config.yaml

FROM python:3.12-slim AS runtime
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src:/app/generated/src \
    OPENAPI_SPEC_PATH=/app/openapi/marketplace.yaml
RUN groupadd --system app && useradd --system --gid app app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=codegen /build/generated/src ./generated/src
COPY openapi/marketplace.yaml ./openapi/marketplace.yaml
COPY src ./src
USER app
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2)"]
CMD ["uvicorn", "marketplace.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]

FROM runtime AS test
USER root
COPY requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY tests ./tests
COPY pytest.ini ./
USER app
CMD ["pytest", "-v"]
