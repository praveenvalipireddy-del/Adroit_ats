# Production Dockerfile for Aventra AI ATS
FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=5000

# Set work directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port
EXPOSE 5000

# Run with production Gunicorn server: ONE copy of the app with 4 threads. Three copies each loaded the AI / Word /
# PDF / email libraries and ran their own background jobs - more than the 512 MB instance holds (Render restarted
# the service for exceeding its memory limit). Threads let a slow request (AI email, resume tailoring) run while
# the other three keep serving pages.
CMD ["gunicorn", "app:app", "--worker-class=gthread", "--workers=1", "--threads=4", "--bind=0.0.0.0:5000", "--timeout=240"]
