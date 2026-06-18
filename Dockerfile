# Use python base image
FROM python:3.9-slim

# Install wget and curl for Chrome installation
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    curl \
    && wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && sh -c 'echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/default/google-chrome' \
    && apt-get update && apt-get install -y \
    google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Copy requirements
COPY requirements.txt .

# Install requirements
RUN pip install --no-cache-dir -r requirements.txt

# Copy codebase
COPY . .

# Expose port 8000
EXPOSE 8000

# Command to run application
CMD ["uvicorn", "chatgpt_api:app", "--host", "0.0.0.0", "--port", "8000"]
