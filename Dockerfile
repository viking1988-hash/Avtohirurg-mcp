FROM python:3.12-slim

WORKDIR /app

COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY . .

ENV PYTHONUNBUFFERED=1

CMD ["python", "server.py"]
