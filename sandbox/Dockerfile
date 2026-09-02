FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# Runs as a non-root user: this service exists to execute untrusted,
# AI-generated code, so it should hold as few privileges as the container
# filesystem allows even before the Docker/network isolation described in
# main.py's docstring is considered.
RUN useradd --create-home --uid 1000 sandbox
USER sandbox

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
