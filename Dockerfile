FROM python:3.13-slim

WORKDIR /app

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Copy pyproject.toml, README.md and source packages required by hatchling
COPY pyproject.toml README.md ./
COPY db ./db
COPY routers ./routers

# Install production dependencies directly from pyproject.toml
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# Copy the rest of the application files
COPY . .

# Run the bot
CMD ["python", "main.py"]
