FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd -m botuser && chown -R botuser:botuser /app && mkdir -p /app/data && chown botuser:botuser /app/data

USER botuser

EXPOSE 5000

CMD ["python", "bot.py"]
