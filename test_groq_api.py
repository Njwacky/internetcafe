import getpass
import os

import requests


def main():
    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()
    api_key = os.getenv("GROQ_API_KEY") or getpass.getpass(
        "Enter your Groq API key (input hidden): "
    )
    api_key = api_key.strip()
    if not api_key:
        raise SystemExit("No API key provided.")
    message = input("Message to send to Groq: ").strip()
    if not message:
        raise SystemExit("No message provided.")

    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": message}],
                "max_completion_tokens": 512,
                **({"reasoning_effort": "low"} if model.startswith("openai/gpt-oss") else {}),
            },
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
        reply = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, TypeError) as exc:
        raise SystemExit("Groq responded, but the chat-completion response was unexpected.") from exc

    if not isinstance(reply, str) or not reply.strip():
        raise SystemExit("Groq returned an empty reply.")
    print(f"\nGroq request succeeded ({model}):\n{reply.strip()}")


if __name__ == "__main__":
    main()