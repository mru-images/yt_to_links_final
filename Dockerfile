# Use official Python slim image
FROM python:3.10-slim

# Set working directory inside container
WORKDIR /app

# Install system dependencies (optional but useful)
RUN apt-get update && apt-get install -y curl ffmpeg && apt-get clean

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

# Run FastAPI with Uvicorn on port 10000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "10000"]
