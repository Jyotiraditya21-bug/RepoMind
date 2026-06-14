FROM python:3.13-slim

# Install git since clone_repo uses git command line via GitPython
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

# Ensure ABI3 compatibility settings for python 3.13 rust compiles if any fallback happens
ENV PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create directory structure for caching
RUN mkdir -p cache demo_cache

# Hugging Face Spaces runs on port 7860 by default
EXPOSE 7860

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
