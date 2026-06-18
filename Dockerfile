# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Set working directory in the container
WORKDIR /app

# Install system dependencies required for OpenCV
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt
# Install gunicorn for production serving
RUN pip install --no-cache-dir gunicorn eventlet

# Copy the rest of the application code
COPY . .

# Expose the default port (8800 based on app.py)
EXPOSE 8800

# Command to run the application using gunicorn with eventlet worker (required for SocketIO)
CMD ["gunicorn", "--worker-class", "eventlet", "-w", "1", "-b", "0.0.0.0:8800", "wsgi:app"]
