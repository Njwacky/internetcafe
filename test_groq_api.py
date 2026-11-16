import getpass
import os

import requests


def main():
    api_key = os.getenv("GROQ_API_KEY") or getpass.getpass(
        "Enter your Groq API key (input hidden): "
    )
    api_key = api_key.strip()
    if not api_key:
        raise SystemExit("No API key provided.")

    try:
        response = requests.get(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=20,
        )
    except requests.RequestException as exc:
        raise SystemExit(f"Could not reach Groq API: {exc}") from exc

    if response.status_code == 401:
        raise SystemExit("Groq rejected the API key (HTTP 401). Check that it is valid and active.")
    if not response.ok:
        raise SystemExit(
            f"Groq returned HTTP {response.status_code}: {response.text}"
        )

    try:
        models = response.json()["data"]
    except (ValueError, KeyError, TypeError) as exc:
        raise SystemExit("Groq responded, but the model-list response was unexpected.") from exc

    print(f"Groq API key works. Groq returned {len(models)} available models.")
    for model in models[:10]:
        print(f"- {model['id']}")


if __name__ == "__main__":
    main()
