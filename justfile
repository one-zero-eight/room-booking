set quiet := true
set unstable := true

token_url := "https://api.innohassle.ru/accounts/v0/tokens/generate-service-token?sub=room-booking-local-dev&scopes=users&only_for_me=true"

# List all commands
default:
    @just --list --unsorted

# Run development server
dev *args: prepare
    uv run -m src.api --reload {{ args }}

# Set up environment
prepare:
    uv run prek install --overwrite --prepare-hooks -t pre-commit -t commit-msg
    uv run ./scripts/prepare.py '{{ token_url }}'

# Run pre-commit actions on all files
prek:
    uv run prek run --all-files
