import os
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events.readonly",
]

client_config = {
    "installed": {
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }
}

flow = InstalledAppFlow.from_client_config(
    client_config,
    scopes=SCOPES,
)

credentials = flow.run_local_server(
    port=0,
    access_type="offline",
    prompt="consent",
)

env_path = Path(".env")
env_lines = env_path.read_text().splitlines() if env_path.exists() else []
updated_lines: list[str] = []
found_refresh_token = False
for line in env_lines:
    if line.startswith("GOOGLE_REFRESH_TOKEN="):
        updated_lines.append(f"GOOGLE_REFRESH_TOKEN={credentials.refresh_token}")
        found_refresh_token = True
    else:
        updated_lines.append(line)
if not found_refresh_token:
    updated_lines.append(f"GOOGLE_REFRESH_TOKEN={credentials.refresh_token}")

env_path.write_text("\n".join(updated_lines) + "\n")

print("\nRefresh token generated successfully.")
print("GOOGLE_REFRESH_TOKEN was written to .env and was not printed.")
