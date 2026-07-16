FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    default-libmysqlclient-dev \
    pkg-config \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# collectstatic only needs settings.py to import cleanly, not a real
# database or a real secret key -- these placeholders are build args, not
# ENV, so they never end up baked into the final image's environment.
ARG SECRET_KEY=collectstatic-build-placeholder
ARG DB_NAME=build
ARG DB_USER=build
ARG DB_PASSWORD=build
ARG DB_HOST=localhost
ARG DB_PORT=3306
RUN SECRET_KEY=$SECRET_KEY \
    DB_NAME=$DB_NAME \
    DB_USER=$DB_USER \
    DB_PASSWORD=$DB_PASSWORD \
    DB_HOST=$DB_HOST \
    DB_PORT=$DB_PORT \
    python manage.py collectstatic --noinput

EXPOSE 8000

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "config.asgi:application"]
