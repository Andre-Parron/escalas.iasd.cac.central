FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=8000 DB_PATH=/data/escalas.db
VOLUME /data
EXPOSE 8000
CMD ["sh", "-c", "gunicorn -w 1 --threads 4 -b 0.0.0.0:${PORT} app:app"]
