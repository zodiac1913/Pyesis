from __future__ import annotations

from dataclasses import dataclass
import json
import os
from subprocess import CalledProcessError, run
import sys
import time
from urllib import error, parse, request
from urllib.parse import urlparse


GITHUB_DOTCOM_AUTH_MODE = "github.com"
GITHUB_ENTERPRISE_AUTH_MODE = "enterprise"
SUPPORTED_GITHUB_AUTH_MODES = {GITHUB_DOTCOM_AUTH_MODE, GITHUB_ENTERPRISE_AUTH_MODE}
GITHUB_DOTCOM_ENDPOINT = "https://github.com"
GITHUB_DOTCOM_API_URL = "https://api.github.com"
GITHUB_MODELS_CHAT_COMPLETIONS_URL = "https://models.github.ai/inference/chat/completions"
PYESIS_GITHUB_TOKEN_SERVICE = "Pyesis GitHub Auth"
DEVICE_CODE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
APPLICATION_JSON = "application/json"
FORM_URLENCODED = "application/x-www-form-urlencoded"


@dataclass(frozen=True)
class GitHubDeviceLogin:
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


@dataclass(frozen=True)
class GitHubUserIdentity:
    login: str
    name: str


@dataclass(frozen=True)
class GitHubAuthStatus:
    mode: str
    endpoint: str
    has_token: bool
    token_source: str
    detail: str


def github_auth_label(mode: str) -> str:
    if normalize_github_auth_mode(mode) == GITHUB_ENTERPRISE_AUTH_MODE:
        return "GitHub Enterprise"
    return "GitHub.com"


def normalize_github_auth_mode(value: str | None) -> str:
    normalized = str(value or GITHUB_DOTCOM_AUTH_MODE).strip().lower()
    if normalized in SUPPORTED_GITHUB_AUTH_MODES:
        return normalized
    return GITHUB_DOTCOM_AUTH_MODE


def normalize_github_auth_endpoint(mode: str, value: str | None) -> str:
    normalized_mode = normalize_github_auth_mode(mode)
    if normalized_mode == GITHUB_DOTCOM_AUTH_MODE:
        return GITHUB_DOTCOM_ENDPOINT

    raw_value = str(value or "").strip()
    if not raw_value:
        return ""
    if "://" not in raw_value:
        raw_value = f"https://{raw_value}"

    parsed = urlparse(raw_value)
    if not parsed.netloc:
        return ""
    return f"{parsed.scheme or 'https'}://{parsed.netloc}".rstrip("/")


def github_auth_api_url(mode: str, endpoint: str) -> str:
    normalized_mode = normalize_github_auth_mode(mode)
    normalized_endpoint = normalize_github_auth_endpoint(normalized_mode, endpoint)
    if normalized_mode == GITHUB_DOTCOM_AUTH_MODE:
        return GITHUB_DOTCOM_API_URL
    if not normalized_endpoint:
        return ""
    return f"{normalized_endpoint}/api/v3"


def github_device_code_url(mode: str, endpoint: str) -> str:
    normalized_mode = normalize_github_auth_mode(mode)
    normalized_endpoint = normalize_github_auth_endpoint(normalized_mode, endpoint)
    if not normalized_endpoint:
        return ""
    return f"{normalized_endpoint}/login/device/code"


def github_oauth_access_token_url(mode: str, endpoint: str) -> str:
    normalized_mode = normalize_github_auth_mode(mode)
    normalized_endpoint = normalize_github_auth_endpoint(normalized_mode, endpoint)
    if not normalized_endpoint:
        return ""
    return f"{normalized_endpoint}/login/oauth/access_token"


def describe_github_auth(mode: str, endpoint: str) -> GitHubAuthStatus:
    normalized_mode = normalize_github_auth_mode(mode)
    normalized_endpoint = normalize_github_auth_endpoint(normalized_mode, endpoint)

    if normalized_mode == GITHUB_ENTERPRISE_AUTH_MODE and not normalized_endpoint:
        return GitHubAuthStatus(
            mode=normalized_mode,
            endpoint="",
            has_token=False,
            token_source="missing-endpoint",
            detail="GitHub Enterprise host is required before auth can be used.",
        )

    token, token_source = load_github_auth_token(normalized_mode, normalized_endpoint)
    endpoint_label = normalized_endpoint or GITHUB_DOTCOM_ENDPOINT
    if token:
        source_label = "environment" if token_source == "environment" else "macOS Keychain"
        return GitHubAuthStatus(
            mode=normalized_mode,
            endpoint=endpoint_label,
            has_token=True,
            token_source=token_source,
            detail=f"{github_auth_label(normalized_mode)} token available from {source_label} for {endpoint_label}.",
        )

    if sys.platform != "darwin":
        return GitHubAuthStatus(
            mode=normalized_mode,
            endpoint=endpoint_label,
            has_token=False,
            token_source="unsupported",
            detail="No GitHub token found. Secure storage is only wired for macOS right now.",
        )

    return GitHubAuthStatus(
        mode=normalized_mode,
        endpoint=endpoint_label,
        has_token=False,
        token_source="missing",
        detail=f"No GitHub token stored for {endpoint_label}.",
    )


def start_github_device_login(mode: str, endpoint: str, client_id: str, scope: str = "") -> GitHubDeviceLogin:
    if not client_id.strip():
        raise RuntimeError("GitHub OAuth client ID is required.")

    url = github_device_code_url(mode, endpoint)
    if not url:
        raise RuntimeError("GitHub endpoint is required before starting sign-in.")

    payload = {"client_id": client_id.strip()}
    if scope.strip():
        payload["scope"] = scope.strip()
    data = parse.urlencode(payload).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={"Accept": APPLICATION_JSON, "Content-Type": FORM_URLENCODED},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except error.URLError as exc:
        raise RuntimeError(str(exc)) from exc

    return GitHubDeviceLogin(
        device_code=str(result.get("device_code", "")).strip(),
        user_code=str(result.get("user_code", "")).strip(),
        verification_uri=str(result.get("verification_uri", "")).strip(),
        expires_in=int(result.get("expires_in", 900) or 900),
        interval=max(1, int(result.get("interval", 5) or 5)),
    )


def poll_github_device_login_token(
    mode: str,
    endpoint: str,
    client_id: str,
    device_code: str,
    expires_in: int,
    interval: int,
) -> str:
    url = github_oauth_access_token_url(mode, endpoint)
    if not url:
        raise RuntimeError("GitHub endpoint is required before polling sign-in.")

    started_at = time.monotonic()
    poll_interval = max(1, interval)
    while time.monotonic() - started_at < expires_in:
        payload = parse.urlencode(
            {
                "client_id": client_id.strip(),
                "device_code": device_code.strip(),
                "grant_type": DEVICE_CODE_GRANT_TYPE,
            }
        ).encode("utf-8")
        req = request.Request(
            url,
            data=payload,
            headers={"Accept": APPLICATION_JSON, "Content-Type": FORM_URLENCODED},
            method="POST",
        )
        result = _request_oauth_json(req)

        token = str(result.get("access_token", "")).strip()
        if token:
            return token

        poll_interval = _next_device_poll_interval(result, poll_interval)
        if not poll_interval:
            continue
        time.sleep(poll_interval)

    raise RuntimeError("Timed out waiting for GitHub sign-in.")


def fetch_github_user_identity(mode: str, endpoint: str, token: str) -> GitHubUserIdentity:
    api_url = github_auth_api_url(mode, endpoint)
    if not api_url:
        raise RuntimeError("GitHub endpoint is required before validating the token.")
    req = request.Request(
        f"{api_url}/user",
        headers={
            "Accept": APPLICATION_JSON,
            "Authorization": f"Bearer {token}",
        },
        method="GET",
    )
    try:
        with request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except error.URLError as exc:
        raise RuntimeError(str(exc)) from exc

    return GitHubUserIdentity(
        login=str(result.get("login", "")).strip(),
        name=str(result.get("name", "")).strip(),
    )


def _request_oauth_json(req: request.Request) -> dict[str, object]:
    try:
        with request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except Exception as parse_exc:
            raise RuntimeError(str(exc)) from parse_exc
    except error.URLError as exc:
        raise RuntimeError(str(exc)) from exc


def _next_device_poll_interval(result: dict[str, object], poll_interval: int) -> int:
    error_code = str(result.get("error", "")).strip().lower()
    if error_code == "authorization_pending":
        return poll_interval
    if error_code == "slow_down":
        return poll_interval + 5
    if error_code == "expired_token":
        raise RuntimeError("The GitHub device code expired before sign-in completed.")
    if error_code == "access_denied":
        raise RuntimeError("GitHub sign-in was cancelled.")
    description = str(result.get("error_description", "")).strip()
    raise RuntimeError(description or error_code or "GitHub device sign-in failed.")


def load_github_auth_token(mode: str, endpoint: str) -> tuple[str, str]:
    env_token = (
        os.getenv("PYESIS_GITHUB_AUTH_TOKEN", "").strip()
        or os.getenv("PYESIS_GITHUB_GPT_API_KEY", "").strip()
        or os.getenv("PYESIS_GITHUB_COPILOT_API_KEY", "").strip()
    )
    if env_token:
        return env_token, "environment"

    normalized_mode = normalize_github_auth_mode(mode)
    normalized_endpoint = normalize_github_auth_endpoint(normalized_mode, endpoint)
    if not normalized_endpoint or sys.platform != "darwin":
        return "", ""

    try:
        result = run(
            [
                "security",
                "find-generic-password",
                "-s",
                PYESIS_GITHUB_TOKEN_SERVICE,
                "-a",
                normalized_endpoint,
                "-w",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except CalledProcessError:
        return "", ""
    return result.stdout.strip(), "keychain"


def store_github_auth_token(mode: str, endpoint: str, token: str) -> tuple[bool, str]:
    normalized_mode = normalize_github_auth_mode(mode)
    normalized_endpoint = normalize_github_auth_endpoint(normalized_mode, endpoint)
    if not normalized_endpoint:
        return False, "GitHub endpoint is required before saving a token."
    if sys.platform != "darwin":
        return False, "Secure token storage is only implemented on macOS right now."

    try:
        run(
            [
                "security",
                "add-generic-password",
                "-U",
                "-s",
                PYESIS_GITHUB_TOKEN_SERVICE,
                "-a",
                normalized_endpoint,
                "-w",
                token,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except CalledProcessError as exc:
        message = exc.stderr.strip() or exc.stdout.strip() or "Unknown macOS Keychain error"
        return False, message
    return True, ""


def clear_github_auth_token(mode: str, endpoint: str) -> tuple[bool, str]:
    normalized_mode = normalize_github_auth_mode(mode)
    normalized_endpoint = normalize_github_auth_endpoint(normalized_mode, endpoint)
    if not normalized_endpoint:
        return True, ""
    if sys.platform != "darwin":
        return False, "Secure token storage is only implemented on macOS right now."

    try:
        run(
            [
                "security",
                "delete-generic-password",
                "-s",
                PYESIS_GITHUB_TOKEN_SERVICE,
                "-a",
                normalized_endpoint,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except CalledProcessError as exc:
        stderr = exc.stderr.strip().lower()
        if "could not be found" in stderr or "item not found" in stderr:
            return True, ""
        message = exc.stderr.strip() or exc.stdout.strip() or "Unknown macOS Keychain error"
        return False, message
    return True, ""
