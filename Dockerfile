FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir httpx websockets fastapi uvicorn pydantic pywebpush pillow solders \
 && useradd -m -u 10001 desk
WORKDIR /app
COPY desk ./desk
COPY copybot ./copybot
COPY scripts ./scripts
RUN mkdir -p data logs && chown -R desk:desk /app
USER desk
CMD ["python", "-m", "desk.main"]
