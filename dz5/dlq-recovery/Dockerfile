FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src
RUN groupadd --system app && useradd --system --gid app app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
USER app
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=5s --start-period=10s --retries=10 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]
CMD ["uvicorn", "recovery.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
