FROM python:3.11-slim

WORKDIR /app

# Install Docker CLI so the bot can restart itself via the mounted socket
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg tzdata \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && chmod a+r /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
       https://download.docker.com/linux/debian bookworm stable" \
       > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Data directory for SQLite databases
RUN mkdir -p /app/data

# Create non-root user but add to docker group so it can use the socket
RUN groupadd -f docker \
    && useradd -m -G docker botuser \
    && chown -R botuser:botuser /app

USER botuser

CMD ["python", "bot.py"]
