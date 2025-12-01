FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install git and other system dependencies
RUN apt-get update && \
    apt-get install -y git && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install Flask for health check
RUN pip install --no-cache-dir flask

# Copy the main.py file
COPY main.py /app/main.py

# Expose port
EXPOSE 8080

# Run the application
CMD ["python3", "/app/main.py"]
