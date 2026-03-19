import argparse
import contextlib
import os
import secrets
import shutil
import sys
import webbrowser
from pathlib import Path

import yaml

base_dir = Path(".").resolve()
os.chdir(base_dir)

settings_template = base_dir / "settings.example.yaml"
settings_file = base_dir / "settings.yaml"
pre_commit_config = base_dir / ".pre-commit-config.yaml"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "token_url",
        type=str,
        help="The URL to receive the service token for Accounts API.",
        default="https://api.innohassle.ru/accounts/v0/tokens/generate-service-token?sub=local-dev&scopes=users&only_for_me=true",
    )
    args = parser.parse_args()

    ensure_settings_file()
    check_and_prompt_api_jwt_token(args.token_url)
    check_and_generate_api_key()


def ensure_settings_file():
    if not settings_template.exists():
        print("❌ No `settings.example.yaml` found. Skipping copying.")
        return
    if settings_file.exists():
        print("✅ `settings.yaml` exists.")
        return
    shutil.copy(settings_template, settings_file)
    print("✅ Copied `settings.example.yaml` to `settings.yaml`")


def check_and_prompt_api_jwt_token(accounts_token_url: str):
    if not settings_file.exists():
        print("❌ No `settings.yaml` found. Skipping JWT token check.")
        return
    try:
        settings = yaml.safe_load(settings_file.read_text()) or {}
    except Exception as e:
        print(f"❌ Error reading `settings.yaml`: {e}")
        return

    accounts = settings.get("accounts", {})
    api_jwt_token = accounts.get("api_jwt_token")

    if not api_jwt_token or api_jwt_token == "...":
        print("⚠️ `accounts.api_jwt_token` is missing in `settings.yaml`.")
        print("  ➡️ Opening the following URL to generate a token:")
        print(f"  {accounts_token_url}")
        try:
            webbrowser.open(accounts_token_url)
        except Exception:
            pass

        print("  🔑 Please paste the generated token below:")
        token_prompt = "  Enter the token here (or press Enter to skip):\n>"
        token = ""
        tty_stream = None
        try:
            if sys.stdin.isatty():
                token = input(token_prompt).strip()
            else:
                tty_stream = open("/dev/tty", encoding="utf-8")
                print(token_prompt, end="", flush=True)
                token = tty_stream.readline().strip()
        except (EOFError, OSError):
            print("  ⚠️ Input stream is not available. Skipping token prompt.")
            print(f"  ➡️ Refer to the URL: {accounts_token_url}")
        finally:
            if tty_stream is not None:
                with contextlib.suppress(Exception):
                    tty_stream.close()

        if token:
            try:
                as_text = settings_file.read_text()
                as_text = as_text.replace("api_jwt_token: null", f"api_jwt_token: {token}")
                as_text = as_text.replace("api_jwt_token: ...", f"api_jwt_token: {token}")
                settings_file.write_text(as_text)
                print("  ✅ `accounts.api_jwt_token` has been updated in `settings.yaml`.")
            except Exception as e:
                print(f"  ❌ Error updating `settings.yaml`: {e}")
        else:
            print("  ⚠️ Token was not provided. Please manually update `settings.yaml` later.")
            print(f"  ➡️ Refer to the URL: {accounts_token_url}")
    else:
        print("✅ `accounts.api_jwt_token` is specified.")


def check_and_generate_api_key():
    if not settings_file.exists():
        print("❌ No `settings.yaml` found. Skipping api token check.")
        return
    try:
        settings = yaml.safe_load(settings_file.read_text()) or {}
    except Exception as e:
        print(f"❌ Error reading `settings.yaml`: {e}")
        return

    api_key = settings.get("api_key")
    if not api_key or api_key == "...":
        print("⚠️ `api_key` is missing in `settings.yaml`. Generating a new one.")
        api_key = secrets.token_hex(32)
        as_text = settings_file.read_text()
        as_text = as_text.replace("api_key: null", f"api_key: {api_key}")
        as_text = as_text.replace("api_key: ...", f"api_key: {api_key}")
        settings_file.write_text(as_text)
        print("  ✅ `api_key` has been updated in `settings.yaml`.")


if __name__ == "__main__":
    main()
