FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ARG REMNAUM_BUILD_VERSION=0.0.0+dev
ENV REMNAUM_BUILD_VERSION=${REMNAUM_BUILD_VERSION}
LABEL org.opencontainers.image.version="${REMNAUM_BUILD_VERSION}"
RUN useradd --uid 1000 --create-home app && chown -R app:app /app
USER app
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
