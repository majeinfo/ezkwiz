# ezkwiz

A live quiz platform in the spirit of Kahoot. Creators sign up, build quizzes
(questions with up to 4 choices each, multiple correct answers allowed), and
publish them to get a randomly generated join code. Players join with just a
nickname and play together in real time.

## Stack

- **Backend:** Django 6 + Django Channels (WebSockets) on Daphne (ASGI)
- **Database:** MariaDB / MySQL
- **Real-time transport:** Redis (Channels layer)
- **Frontend:** Django templates + Bootstrap 5 + vanilla JS (no build step)

## How it works

- **Creators** authenticate with a normal Django account and build quizzes in
  a custom authoring UI (`quizzes` app) — not the Django admin, which is kept
  for superuser oversight only.
- **Publishing** a quiz creates a `GameSession` with a random join code and a
  private host URL (`games` app).
- **Players** open the join URL, pick a nickname (no account needed), and
  play. All game state changes (start, reveal, next question, scoring) are
  driven by a server-authoritative state machine in `games/services.py` and
  broadcast over WebSockets via Channels consumers (`games/consumers.py`).
- Choice order is shuffled per game session and question is scored with a
  base score plus a speed bonus (100 points for a correct answer, plus 10
  points per second remaining before the question's time limit).
- Players can refresh or reconnect mid-game: a client-side token
  (`localStorage`) lets them resume the same session and see the current
  question or results instead of starting over.

## Local development

Requires Python 3.12+ and Docker (for MariaDB + Redis). This runs the app on
the host with `manage.py runserver`, against containerized MariaDB + Redis.

```bash
# 1. Create/activate a virtualenv and install dependencies
python3 -m venv py314
source py314/bin/activate
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# edit .env if you want different DB credentials, then make sure
# docker-compose.dev.yml's MARIADB_* values match

# 3. Start MariaDB + Redis
docker compose -f docker-compose.dev.yml up -d

# 4. Apply migrations and create a creator account
python manage.py migrate
python manage.py createsuperuser   # optional, for /admin/ oversight

# 5. Run the dev server (serves over ASGI/WebSockets via Daphne)
python manage.py runserver
```

Then visit `http://127.0.0.1:8000/accounts/signup/` to create a creator
account and build your first quiz at `/quizzes/`.

## Running tests

```bash
python manage.py test
```

## Running the whole app in Docker

`docker-compose.yml` (the default, distinct from the dev-only file above)
runs MariaDB, Redis, and ezkwiz itself together — no local Python/venv setup
needed. Set at least `SECRET_KEY` in `.env` first (see `.env.example`), then:

```bash
docker compose up -d --build
```

This builds the image from the `Dockerfile` (falling back to pulling
`ghcr.io/majeinfo/ezkwiz:latest`, which a GitHub Actions workflow publishes
on every push to `main`), waits for MariaDB/Redis to be healthy, applies
migrations automatically, and serves the app on `http://127.0.0.1:8000/`.
`BIND_HOST`/`BIND_PORT` in `.env` control which local address/port that's
published on (e.g. `BIND_HOST=127.0.0.1` to keep it off the public network
behind a reverse proxy).

### Behind a reverse proxy (HTTPS)

If a reverse proxy (nginx, Caddy, Traefik...) terminates TLS in front of the
app, set in `.env`:

```
BEHIND_HTTPS_PROXY=True
CSRF_TRUSTED_ORIGINS=https://your-domain.example
```

and make sure the proxy forwards `X-Forwarded-Proto: https` (e.g. nginx:
`proxy_set_header X-Forwarded-Proto https;`). Without this, logging in or
submitting any form fails with "CSRF verification failed: Origin checking
failed" — Django has no way to know the original request was HTTPS.

## Project layout

```
config/     Django project settings, URL root, ASGI wiring
accounts/   Creator signup/login/logout
quizzes/    Quiz/Question/Choice models + authoring UI
games/      GameSession/Player/Answer models, WebSocket consumers,
            game state machine (services.py), join/host/play views
templates/  Shared Bootstrap 5 templates
docker-compose.dev.yml   MariaDB + Redis only, for local development/tests
docker-compose.yml       Full stack (MariaDB + Redis + ezkwiz) in containers
Dockerfile               Image build for ezkwiz itself
```
