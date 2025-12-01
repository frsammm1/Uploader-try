FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install git and other dependencies
RUN apt-get update && \
    apt-get install -y git && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Clone the repository
RUN git clone https://github.com/Howtog41/text-leech-bot.git /app/bot

# Set working directory to bot
WORKDIR /app/bot

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the main.py file
COPY main.py /app/main.py

# Expose port (if needed)
EXPOSE 8080

# Run the application
CMD ["python3", "/app/main.py"]
