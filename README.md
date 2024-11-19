# Room booking

## Table of contents

Did you know that GitHub supports table of
contents [by default](https://github.blog/changelog/2021-04-13-table-of-contents-support-in-markdown-files/) 🤔

## About

This is the API for viewing the booking status of rooms.

### Technologies

- [Python 3.12](https://www.python.org/downloads/) & [Poetry](https://python-poetry.org/docs/)
- [FastAPI](https://fastapi.tiangolo.com/) & [Pydantic](https://docs.pydantic.dev/latest/)
- Formatting and linting: [Ruff](https://docs.astral.sh/ruff/), [pre-commit](https://pre-commit.com/)
- Deployment: [Docker](https://www.docker.com/), [Docker Compose](https://docs.docker.com/compose/),
  [GitHub Actions](https://github.com/features/actions)

## Development

### Set up for development

1. Install [Python 3.12+](https://www.python.org/downloads/), Install [Poetry](https://python-poetry.org/docs/)
2. Install project dependencies with [Poetry](https://python-poetry.org/docs/cli/#options-2).
   ```bash
   poetry install
   ```
3. Copy `settings.example.yaml` to `settings.yaml`
   ```bash
   cp settings.example.yaml settings.yaml
   ```
4. Get credentials for EWS/Exchange.asmx and My University API, set them in `settings.yaml`
5. Start development server:
   ```bash
   poetry run python -m src.api --reload
   ```
   > Follow provided instructions if needed
6. Open in the browser: http://localhost:8000
   > The api will be reloaded when you edit the code

> [!IMPORTANT]
> For endpoints requiring authorization click "Authorize" button in Swagger UI

> [!TIP]
> Edit `settings.yaml` according to your needs, you can view schema in
> [config_schema.py](src/config_schema.py) and in [settings.schema.yaml](settings.schema.yaml)

### Deployment

We use Docker with Docker Compose plugin to run the service on servers.

1. Copy the file with settings: `cp settings.example.yaml settings.yaml`
2. Change settings in the `settings.yaml` file according to your needs
   (check [settings.schema.yaml](settings.schema.yaml) for more info)
3. Install Docker with Docker Compose
4. Build a Docker image: `docker compose build --pull`
5. Run the container: `docker compose up --detach`
6. Check the logs: `docker compose logs -f`

# How to update dependencies

## Project dependencies

1. Run `poetry update` to update all dependencies
2. Run `poetry show --outdated` to check for outdated dependencies
3. Run `poetry add <package>@latest` to add a new dependency if needed

## Pre-commit hooks

1. Run `poetry run pre-commit autoupdate`
