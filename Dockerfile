FROM python:3.12-slim-bookworm

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/orchestrator/ ./orchestrator/

EXPOSE 8000  # MCP streamable HTTP
COPY src/git_provider/ ./git_provider/
COPY config.yaml.example ./config.yaml.example

# Create non-root user
RUN useradd -m -u 1000 -s /bin/bash orchestrator && \
    chown -R orchestrator:orchestrator /app

USER orchestrator

# Default command
CMD ["python", "-m", "orchestrator.main"]