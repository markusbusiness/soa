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
COPY flight-service/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=codegen /build/generated ./generated
COPY flight-service/src ./src
USER app
EXPOSE 50051
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=5 CMD ["python", "-m", "flight_service.healthcheck"]
CMD ["python", "-m", "flight_service.main"]
