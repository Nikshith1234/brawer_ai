# Use official Python image which has all system libs pre-installed
FROM python:3.11-slim

# Install ALL system dependencies Chromium needs (as root, before app user)
RUN apt-get update && apt-get install -y \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libexpat1 \
    libxcb1 \
    libxkbcommon0 \
    libx11-6 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libatspi2.0-0 \
    wget \
    ca-certificates \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers (runs as root here so no permission issues)
RUN playwright install chromium

# Copy all project files
COPY . .

# Create logs directory
RUN mkdir -p logs

# Expose port
EXPOSE 10000

# Start the app
CMD ["gunicorn", "main:app", "--bind", "0.0.0.0:10000", "--timeout", "120", "--workers", "1"]
