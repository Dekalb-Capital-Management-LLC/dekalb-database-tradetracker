FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# db.py looks for the schema at /etc/schemas/trade_tracker_schema.sql. Locally
# that path comes from a docker-compose volume mount of the repo-root
# schemas/ dir; here we bake in this directory's own copy since Docker can't
# COPY from outside the build context (Root Directory = trade-tracker/api).
COPY schemas/trade_tracker_schema.sql /etc/schemas/trade_tracker_schema.sql

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
