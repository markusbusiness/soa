FROM python:3.12-slim AS codegen
RUN pip install --no-cache-dir grpcio-tools==1.85.1
WORKDIR /build
COPY proto ./proto
RUN mkdir generated && python -m grpc_tools.protoc -I proto \
      --python_out=generated --pyi_out=generated --grpc_python_out=generated \
      proto/flight/v1/flight.proto

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src:/app/generated
RUN groupadd --system app && useradd --system --gid app app
COPY booking-service/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=codegen /build/generated ./generated
COPY booking-service/src ./src
USER app
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=5 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]
CMD ["uvicorn", "booking_service.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
