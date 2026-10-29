FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home fleet
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY db/ ./db/
USER 10001:10001
EXPOSE 8080
CMD ["gunicorn", "--chdir", "/app/app", "--bind", "0.0.0.0:8080", "--workers", "2", "--access-logfile", "-", "app:app"]
