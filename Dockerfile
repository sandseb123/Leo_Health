FROM python:3.11-slim

WORKDIR /app

# Copy project files
COPY pyproject.toml .
COPY leo_health/ ./leo_health/

# Install leo-health
RUN pip install --no-cache-dir -e .

# Create data directory
RUN mkdir -p /data

# Expose dashboard port
EXPOSE 5380

# Default command — start dashboard
CMD ["leo-dash"]
