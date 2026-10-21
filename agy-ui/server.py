#!/data/data/com.termux/files/usr/bin/python3
"""
Antigravity Mobile UI Backend Daemon (agy-web)
Target Environment: Android ARM64 | Termux Python 3.14.6
"""

from __future__ import annotations

import asyncio
import base64
import datetime
import io
import json
import logging
import mimetypes
import os
import re
import select
import shutil
import secrets
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path
from typing import Any, Optional

import websockets
from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.http11 import Request, Response

# ---------------------------------------------------------------------------
# Configuration & Constants
# ---------------------------------------------------------------------------

HOST = os.environ.get("AGY_WEB_HOST", "127.0.0.1")
PORT = int(os.environ.get("AGY_WEB_PORT", "8081"))

HOME_DIR = Path(os.environ.get("HOME", "/data/data/com.termux/files/home"))
AGY_BIN = Path(os.environ.get("AGY_BIN", "/data/data/com.termux/files/usr/bin/agy"))
GEMINI_DIR = HOME_DIR / ".gemini" / "antigravity-cli"
SUMMARIES_DB = GEMINI_DIR / "conversation_summaries.db"
CONVERSATIONS_DIR = GEMINI_DIR / "conversations"
BRAIN_DIR = GEMINI_DIR / "brain"
ANNOTATIONS_DIR = GEMINI_DIR / "annotations"
CACHE_DIR = GEMINI_DIR / "cache"
CONV_METADATA_JSON = CACHE_DIR / "conversation_metadata.json"
APP_SETTINGS_FILE = GEMINI_DIR / "app_settings.json"

TMP_DIR = Path(os.environ.get("TMPDIR", "/data/data/com.termux/files/usr/tmp"))
AUTH_TOKEN_FILE = TMP_DIR / ".auth_token"
AUTH_TOKEN = os.environ.get("AGY_AUTH_TOKEN")
if not AUTH_TOKEN and AUTH_TOKEN_FILE.exists():
    try:
        AUTH_TOKEN = AUTH_TOKEN_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        pass
if not AUTH_TOKEN:
    AUTH_TOKEN = secrets.token_urlsafe(32)
    try:
        AUTH_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        AUTH_TOKEN_FILE.write_text(AUTH_TOKEN, encoding="utf-8")
        os.chmod(str(AUTH_TOKEN_FILE), 0o600)
    except Exception:
        pass


def is_valid_local_origin(origin: Optional[str]) -> bool:
    """
    Validates that the Origin header (if provided) is strictly from localhost/127.0.0.1.
    Prevents Cross-Site WebSocket Hijacking (CSWSH) and unauthorized cross-origin requests.
    """
    if not origin:
        return True  # Non-browser clients or same-origin direct requests may omit Origin
    origin_clean = origin.strip().lower()
    return bool(re.match(r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$", origin_clean))


def is_request_authorized(request: Request) -> bool:
    """
    Verifies that the incoming request contains the valid local session auth token.
    Supports ?auth=<token> query parameter, X-Agy-Auth header, or Authorization: Bearer <token>.
    """
    if not AUTH_TOKEN:
        return True

    # 1. Check query parameter ?auth=...
    raw_path = request.path
    if "?" in raw_path:
        qs = raw_path.split("?", 1)[1]
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        if params.get("auth") == AUTH_TOKEN:
            return True

    # 2. Check X-Agy-Auth header
    if request.headers.get("X-Agy-Auth") == AUTH_TOKEN:
        return True

    # 3. Check Authorization header
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        if auth_header[7:].strip() == AUTH_TOKEN:
            return True

    return False


def load_app_settings() -> dict[str, Any]:
    if APP_SETTINGS_FILE.exists():
        try:
            return json.loads(APP_SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_app_settings(settings: dict[str, Any]) -> None:
    try:
        APP_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        APP_SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed saving app_settings.json: %s", exc)


def _read_varint(stream: io.BytesIO) -> Optional[int]:
    val = 0
    shift = 0
    while True:
        b = stream.read(1)
        if not b:
            return None
        byte = b[0]
        val |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            break
        shift += 7
    return val


def _encode_varint(val: int) -> bytes:
    res = bytearray()
    while True:
        b = val & 0x7F
        val >>= 7
        if val:
            res.append(b | 0x80)
        else:
            res.append(b)
            break
    return bytes(res)


def _decode_proto_fields(data: bytes) -> list[tuple[int, int, Any]]:
    fields = []
    stream = io.BytesIO(data)
    while True:
        tag = _read_varint(stream)
        if tag is None:
            break
        field_num = tag >> 3
        wire_type = tag & 0x07
        if wire_type == 0:
            val = _read_varint(stream)
            fields.append((field_num, wire_type, val))
        elif wire_type == 2:
            length = _read_varint(stream)
            content = stream.read(length or 0)
            fields.append((field_num, wire_type, content))
        elif wire_type == 1:
            fields.append((field_num, wire_type, stream.read(8)))
        elif wire_type == 5:
            fields.append((field_num, wire_type, stream.read(4)))
        else:
            break
    return fields


def _encode_proto_fields(fields: list[tuple[int, int, Any]]) -> bytes:
    out = bytearray()
    for field_num, wire_type, val in fields:
        tag = (field_num << 3) | wire_type
        out.extend(_encode_varint(tag))
        if wire_type == 0:
            out.extend(_encode_varint(val))
        elif wire_type == 2:
            out.extend(_encode_varint(len(val)))
            out.extend(val)
        elif wire_type in (1, 5):
            out.extend(val)
    return bytes(out)


def _patch_submessage_workspace(sub_bytes: bytes, new_ws_uri: str) -> bytes:
    sub_fields = _decode_proto_fields(sub_bytes)
    new_sub = []
    new_ws_encoded = new_ws_uri.encode("utf-8")
    for sfn, swt, sval in sub_fields:
        if sfn in (1, 2) and swt == 2:
            new_sub.append((sfn, swt, new_ws_encoded))
        else:
            new_sub.append((sfn, swt, sval))
    return _encode_proto_fields(new_sub)


def patch_trajectory_metadata_blob(blob_bytes: bytes, new_ws_uri: str) -> bytes:
    fields = _decode_proto_fields(blob_bytes)
    new_fields = []
    new_ws_encoded = new_ws_uri.encode("utf-8")
    for fn, wt, val in fields:
        if fn == 1 and wt == 2:
            try:
                new_sub = _patch_submessage_workspace(val, new_ws_uri)
                new_fields.append((fn, wt, new_sub))
            except Exception:
                new_fields.append((fn, wt, val))
        elif fn == 7 and wt == 2:
            new_fields.append((fn, wt, new_ws_encoded))
        else:
            new_fields.append((fn, wt, val))
    return _encode_proto_fields(new_fields)


def patch_raw_summary_workspace(data: bytes, new_ws_uri: str) -> bytes:
    fields = _decode_proto_fields(data)
    new_fields = []
    for fn, wt, val in fields:
        if fn in (9, 14) and wt == 2:
            try:
                new_sub = _patch_submessage_workspace(val, new_ws_uri)
                new_fields.append((fn, wt, new_sub))
            except Exception:
                new_fields.append((fn, wt, val))
        else:
            new_fields.append((fn, wt, val))
    return _encode_proto_fields(new_fields)

APP_DIR = Path(__file__).resolve().parent
INDEX_HTML_PATH = APP_DIR / "index.html"
UPLOADS_DIR = APP_DIR / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_MODELS: list[dict[str, Any]] = []

VALID_MODES = ["default", "accept-edits", "plan"]
VALID_EFFORTS = ["low", "medium", "high"]

_cached_agy_version: Optional[str] = None
_cached_agy_version_at: float = 0.0

def get_agy_version() -> str:
    global _cached_agy_version, _cached_agy_version_at
    now = time.time()
    if _cached_agy_version and (now - _cached_agy_version_at) < 20.0:
        return _cached_agy_version

    # 1. Ask the binary directly: agy --version
    try:
        agy_cmd = os.environ.get("AGY_BIN", str(AGY_BIN))
        res = subprocess.run([agy_cmd, "--version"], capture_output=True, text=True, timeout=3)
        out = (res.stdout or res.stderr).strip()
        m = re.search(r"\b([0-9]+\.[0-9]+(?:\.[0-9]+)?)\b", out)
        if m:
            _cached_agy_version = m.group(1)
            _cached_agy_version_at = now
            return _cached_agy_version
    except Exception as exc:
        logger.warning("Error running agy --version: %s", exc)

    # 2. Check environment variable injected by Android container
    env_ver = os.environ.get("AGY_RUNTIME_VERSION")
    if env_ver:
        _cached_agy_version = env_ver.strip().lstrip("v")
        _cached_agy_version_at = now
        return _cached_agy_version

    # 3. Check cli.log if agy has written its startup header
    try:
        cli_log = GEMINI_DIR / "cli.log"
        if cli_log.exists():
            log_head = cli_log.read_text(errors="ignore")[:4096]
            m = re.search(r"Language server version:\s*([0-9]+\.[0-9]+(?:\.[0-9]+)?)", log_head)
            if m:
                _cached_agy_version = m.group(1)
                _cached_agy_version_at = now
                return _cached_agy_version
    except Exception:
        pass

    return _cached_agy_version or os.environ.get("AGY_RUNTIME_VERSION", "1.4.3")

APP_VERSION = os.environ.get("AGY_APP_VERSION", "1.3.3")
APP_BUILD = os.environ.get("AGY_APP_VERSION_CODE", "16")
AGY_VERSION = get_agy_version()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("agy-web")


# ---------------------------------------------------------------------------
# v1.1.0: Credential, Prompts, Skills & MCP Management Subsystems
# ---------------------------------------------------------------------------

USR_DIR = Path(os.environ.get("PREFIX", "/data/data/com.termux/files/usr"))
SSH_DIR = HOME_DIR / ".ssh"
GEMINI_CONFIG_DIR = HOME_DIR / ".gemini" / "config"
GEMINI_RULES_DIR = GEMINI_CONFIG_DIR / "rules"
DEFAULT_APP_PROMPT_FILE = GEMINI_CONFIG_DIR / "GEMINI.md"
USER_INSTRUCTIONS_FILE = GEMINI_RULES_DIR / "user_instructions.md"
API_KEYS_FILE = GEMINI_CONFIG_DIR / "api_keys.json"
API_KEYS_MANIFEST_FILE = GEMINI_RULES_DIR / "api_keys_manifest.md"
MCP_CONFIG_FILE = GEMINI_CONFIG_DIR / "mcp_config.json"
GLOBAL_SKILLS_DIR = GEMINI_CONFIG_DIR / "skills"
BUILTIN_SKILLS_DIR = HOME_DIR / ".gemini" / "antigravity-cli" / "builtin" / "skills"
PROTECTED_SYSTEM_SKILLS: frozenset[str] = frozenset({"ask-question"})


def atomic_write_secure(target_path: str, content: str, mode: int = 0o600) -> int:
    encoded = content.encode("utf-8")
    parent = os.path.dirname(target_path)
    if parent:
        os.makedirs(parent, mode=0o700, exist_ok=True)
    tmp_path = f"{target_path}.tmp.{os.getpid()}"
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(encoded)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, target_path)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        raise
    return len(encoded)


# 1. Custom Instructions & User Prompts
def get_user_prompt_data() -> dict[str, Any]:
    content = ""
    updated_at = 0
    if USER_INSTRUCTIONS_FILE.exists():
        try:
            content = USER_INSTRUCTIONS_FILE.read_text(encoding="utf-8")
            updated_at = int(USER_INSTRUCTIONS_FILE.stat().st_mtime)
        except Exception as exc:
            logger.warning("Could not read user_instructions.md: %s", exc)

    return {
        "user_prompt": content,
        "content": content,
        "updated_at": updated_at,
        "max_bytes": 24000,
    }


def sync_gemini_md_prompt() -> None:
    """
    Synchronizes default_app_prompt.md and user_instructions.md into ~/.gemini/config/GEMINI.md.
    Ensures that libagy.so loads both the mobile directives and the user's custom instructions
    into RULE[user_global] on every turn.
    """
    base_prompt = ""
    app_prompt_asset = APP_DIR / "default_app_prompt.md"
    if app_prompt_asset.exists():
        try:
            base_prompt = app_prompt_asset.read_text(encoding="utf-8").strip()
        except Exception as exc:
            logger.warning("Could not read default_app_prompt.md: %s", exc)

    if not base_prompt and DEFAULT_APP_PROMPT_FILE.exists():
        try:
            raw = DEFAULT_APP_PROMPT_FILE.read_text(encoding="utf-8").strip()
            if "\n\n# User Custom Instructions" in raw:
                base_prompt = raw.split("\n\n# User Custom Instructions")[0].strip()
            else:
                base_prompt = raw
        except Exception:
            pass

    user_instructions = ""
    if USER_INSTRUCTIONS_FILE.exists():
        try:
            user_instructions = USER_INSTRUCTIONS_FILE.read_text(encoding="utf-8").strip()
        except Exception as exc:
            logger.warning("Could not read user_instructions.md: %s", exc)

    combined = base_prompt
    if user_instructions:
        combined += f"\n\n# User Custom Instructions\n\n{user_instructions}\n"

    try:
        atomic_write_secure(str(DEFAULT_APP_PROMPT_FILE), combined, mode=0o644)
        logger.info("Synchronized GEMINI.md with user instructions (%d bytes)", len(combined.encode("utf-8")))
    except Exception as exc:
        logger.warning("Failed to sync GEMINI.md: %s", exc)


def save_user_prompt_data(content: str) -> dict[str, Any]:
    encoded = content.encode("utf-8")
    if len(encoded) > 24000:
        raise ValueError("Prompt exceeds maximum allowed size of 24,000 bytes")
    bytes_written = atomic_write_secure(str(USER_INSTRUCTIONS_FILE), content, mode=0o600)
    sync_gemini_md_prompt()
    return {"status": "ok", "bytes": bytes_written}


# 2. OpenSSH Toolchain & Key Management
RESERVED_SSH_NAMES = {
    "config", "known_hosts", "known_hosts.old",
    "authorized_keys", "authorized_keys2", "environment", "rc"
}
KEY_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

DEFAULT_KNOWN_HOSTS = (
    "# Standard GitHub & GitLab Host Keys (Pre-seeded for MITM protection)\n"
    "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLnxAzETJ+kytsnupzs0KECc\n"
    "github.com ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBEmKSENjQEezOmxkZMy7opKgwFB9nkt5YRrYMjNuG5N87uR23pHrCEhVlW/9pcKGqvdqQD37OOcxSk+4HtddKeU=\n"
    "github.com ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCj7ndNxQowgcQnjshcLrqPEiiphnt+VTTvDP6mHBL9j1aNUkY4Ue1gvwnGLVlOhGeYrnZaMgRK6+PKCUXaDb6wqqsz/mt4C27fqBQU2vqh4WhhxxnrRqwUS199IjVCc692crkFAIbN5MmOJayUEYW6dJitterzH1FF951LT8GI626floIbEk6Y+io65NVzMiahEr72MZ+OYyavVVpXN55nj5dTC65NT6IFMgE44zfCunCob046g300jdKGig7/MrX05h+29KNeFHB64THV6/22+OxqAEysmVuzZMgyGw28BIbwOmvyEQdzTEKLRgIC6uSohHGZObLnElJyMxXxRHtk=\n"
    "gitlab.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAfuCHKVTjquxvt6CM6tdG4SLp1Btn/nOeHHE5UOzRdf\n"
    "gitlab.com ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBFSMqzJeV9rUzU4kWitGjeR4PWSa29SPqJ1fUtEQjZecqErYzWCisKed3UngAJ7G3Prd21OUI+9LEUhgBQgkE4=\n"
    "gitlab.com ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCsj2bNKTBSpIYDEGb9KzsGh3ULVNop7ZREmlLtQt8Am2ihw6AZMul+JY3PHyhmPv0BJPiteVeRR6LvxEWPzSjiBQMWVkC5DUbe0Sf/IAFc8OOMrHJedk20YDY95baa+UxP9GWHYktqfZjcKyaWimSCnE9GlReUxN1bf2II6iA6VNqznZaoKn373VK40fi/mzFjZtZN3wN84YOE2YCGkkgrVKmBQY57A6egIp+gwpDgoIfpbIdeINhA4eMZXdZmU3a100HJaTDBprzQdJJ6WNiUomScLurZs48NRH/9jeGvokUKdtWhyYXuCY6U5O+Bigfp8w6hMZ9HS8xexNu7twPD\n"
)


def sync_ssh_config() -> None:
    SSH_DIR.mkdir(mode=0o700, exist_ok=True)
    os.chmod(str(SSH_DIR), 0o700)
    known_hosts_file = SSH_DIR / "known_hosts"
    if not known_hosts_file.exists() or known_hosts_file.stat().st_size == 0:
        atomic_write_secure(str(known_hosts_file), DEFAULT_KNOWN_HOSTS, mode=0o600)
    config_file = SSH_DIR / "config"
    lines = [
        "# Auto-generated by Antigravity Studio",
        "Host *",
        "    BatchMode yes",
        "    StrictHostKeyChecking accept-new",
        f"    UserKnownHostsFile {SSH_DIR}/known_hosts",
        "    ServerAliveInterval 30",
        "    ServerAliveCountMax 3",
        "    IdentitiesOnly yes",
    ]
    for pub in sorted(SSH_DIR.glob("*.pub")):
        priv = pub.with_suffix("")
        if priv.is_file() and priv.name not in RESERVED_SSH_NAMES:
            lines.append(f"    IdentityFile {priv.resolve()}")
    atomic_write_secure(str(config_file), "\n".join(lines) + "\n", mode=0o600)


async def list_ssh_keys() -> list[dict[str, Any]]:
    SSH_DIR.mkdir(mode=0o700, exist_ok=True)
    keys = []
    for pub in sorted(SSH_DIR.glob("*.pub")):
        name = pub.stem
        if name in RESERVED_SSH_NAMES:
            continue
        priv = pub.with_suffix("")
        has_private = priv.is_file()
        fingerprint = ""
        key_type = "UNKNOWN"
        try:
            pub_content = pub.read_text(encoding="utf-8").strip()
            parts = pub_content.split()
            if parts:
                key_type = parts[0].replace("ssh-", "").upper()
            proc = await asyncio.create_subprocess_exec(
                "ssh-keygen", "-l", "-f", str(pub),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3.0)
            if proc.returncode == 0:
                fp_parts = stdout.decode("utf-8").strip().split()
                if len(fp_parts) >= 2:
                    fingerprint = fp_parts[1]
                if len(fp_parts) >= 4:
                    key_type = fp_parts[-1].strip("()")
        except Exception:
            pass

        keys.append({
            "name": name,
            "key_type": key_type,
            "type": key_type,
            "fingerprint": fingerprint,
            "has_private": has_private,
            "created_at": int(pub.stat().st_mtime) if pub.exists() else 0,
            "created": time.strftime("%Y-%m-%d", time.localtime(pub.stat().st_mtime)) if pub.exists() else "",
        })
    return keys


async def generate_ssh_key(name: str, key_type: str, overwrite: bool = False) -> dict[str, Any]:
    if not KEY_NAME_RE.match(name) or name.lower() in RESERVED_SSH_NAMES:
        raise ValueError(f"Invalid or reserved SSH key name: '{name}'")
    SSH_DIR.mkdir(mode=0o700, exist_ok=True)
    os.chmod(str(SSH_DIR), 0o700)
    priv_path = SSH_DIR / name
    pub_path = SSH_DIR / f"{name}.pub"

    if (priv_path.exists() or pub_path.exists()) and not overwrite:
        raise FileExistsError(f"SSH key '{name}' already exists")

    kt = key_type.lower()
    if kt == "ed25519":
        type_args = ["-t", "ed25519"]
    elif kt in ("rsa-4096", "rsa"):
        type_args = ["-t", "rsa", "-b", "4096"]
    elif kt == "ecdsa":
        type_args = ["-t", "ecdsa", "-b", "384"]
    else:
        raise ValueError(f"Unsupported key_type: {key_type}")

    if priv_path.exists():
        priv_path.unlink()
    if pub_path.exists():
        pub_path.unlink()

    cmd = ["ssh-keygen", *type_args, "-N", "", "-C", f"{name}@antigravity", "-f", str(priv_path)]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=15.0)
    if proc.returncode != 0:
        raise RuntimeError(f"ssh-keygen failed: {stderr.decode('utf-8', 'replace')}")

    os.chmod(str(priv_path), 0o600)
    os.chmod(str(pub_path), 0o644)
    sync_ssh_config()
    key_list = await list_ssh_keys()
    for k in key_list:
        if k["name"] == name:
            return k
    return {"name": name, "key_type": key_type, "fingerprint": "", "has_private": True, "created_at": int(time.time())}


def export_ssh_key(name: str) -> str:
    if not KEY_NAME_RE.match(name) or name.lower() in RESERVED_SSH_NAMES:
        raise ValueError("Invalid key name")
    pub_path = SSH_DIR / f"{name}.pub"
    if not pub_path.exists():
        raise FileNotFoundError(f"Key {name}.pub does not exist")

    download_dir = Path("/storage/emulated/0/Download")
    download_dir.mkdir(parents=True, exist_ok=True)
    dest = download_dir / f"{name}.pub"
    try:
        if dest.exists():
            dest.unlink()
        dest.write_text(pub_path.read_text(encoding="utf-8"), encoding="utf-8")
        return str(dest)
    except PermissionError:
        timestamp = int(time.time())
        fallback = download_dir / f"{name}_{timestamp}.pub"
        fallback.write_text(pub_path.read_text(encoding="utf-8"), encoding="utf-8")
        return str(fallback)


def delete_ssh_key(name: str) -> bool:
    if not KEY_NAME_RE.match(name) or name.lower() in RESERVED_SSH_NAMES:
        raise ValueError("Invalid key name")
    priv_path = SSH_DIR / name
    pub_path = SSH_DIR / f"{name}.pub"
    deleted = False
    if priv_path.exists():
        priv_path.unlink()
        deleted = True
    if pub_path.exists():
        pub_path.unlink()
        deleted = True
    if deleted:
        sync_ssh_config()
    return deleted


def get_ssh_pub_key(name: str) -> str:
    if not KEY_NAME_RE.match(name) or name.lower() in RESERVED_SSH_NAMES:
        raise ValueError("Invalid key name")
    pub_path = SSH_DIR / f"{name}.pub"
    if not pub_path.exists():
        raise FileNotFoundError(f"Key {name}.pub does not exist")
    return pub_path.read_text(encoding="utf-8").strip()


# 3. API Keys & Process Environment
PROTECTED_ENV_NAMES = {
    "PATH", "LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "LD_DEBUG",
    "HOME", "USER", "LOGNAME", "SHELL", "TERM", "TMPDIR", "TMP", "TEMP",
    "PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONINSPECT",
    "GODEBUG", "GOMAXPROCS", "GOPATH", "GOROOT",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE",
    "GIT_SSH", "GIT_SSH_COMMAND", "GIT_EXEC_PATH", "GIT_TEMPLATE_DIR",
    "BASH_ENV", "ENV", "IFS", "PROMPT_COMMAND", "LANG", "LC_ALL",
    "ANDROID_DATA", "ANDROID_ROOT", "ANDROID_STORAGE", "EXTERNAL_STORAGE"
}
API_KEY_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")


def validate_api_key_name(name: str) -> None:
    if not API_KEY_NAME_RE.match(name):
        raise ValueError("Key name must start with A-Z and contain only A-Z, 0-9, and _")
    if name in PROTECTED_ENV_NAMES or name.startswith(("LD_", "DYLD_", "ANDROID_", "AGY_")):
        raise ValueError(f"'{name}' is a protected system environment variable and cannot be overridden")


def mask_api_key(value: str) -> str:
    n = len(value)
    if n <= 8:
        return "••••••••"
    if n <= 14:
        return f"{value[:2]}••••••••{value[-2:]}"
    return f"{value[:4]}••••••••{value[-4:]}"


def redact_secrets_from_text(text: str, active_keys: dict[str, str]) -> str:
    if not text or not active_keys:
        return text
    for key_name, raw_val in active_keys.items():
        if raw_val and len(raw_val) >= 6 and raw_val in text:
            text = text.replace(raw_val, f"[REDACTED:{key_name}]")
    return text


def load_api_keys_store() -> dict[str, Any]:
    if not API_KEYS_FILE.exists():
        default_store = {"version": 1, "updated_at": int(time.time()), "keys": {}}
        atomic_write_secure(str(API_KEYS_FILE), json.dumps(default_store, indent=2) + "\n", mode=0o600)
        return default_store
    os.chmod(str(API_KEYS_FILE), 0o600)
    try:
        return json.loads(API_KEYS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "updated_at": int(time.time()), "keys": {}}


def get_active_api_keys() -> dict[str, str]:
    store = load_api_keys_store()
    return {k: v.get("value", "") for k, v in store.get("keys", {}).items() if v.get("value")}


def sync_api_keys_manifest() -> None:
    store = load_api_keys_store()
    keys = store.get("keys", {})
    if not keys:
        if API_KEYS_MANIFEST_FILE.exists():
            API_KEYS_MANIFEST_FILE.unlink()
        return
    lines = [
        "# Environment API Keys Available",
        "",
        "The following API keys are pre-configured in your process environment:",
    ]
    for k, info in sorted(keys.items()):
        desc = info.get("description", "").strip()
        if desc:
            lines.append(f"- `{k}`: {desc}")
        else:
            lines.append(f"- `{k}`")
    lines.extend([
        "",
        "You can access these directly via `$KEY_NAME` in shell scripts or `os.environ[\"KEY_NAME\"]` in Python scripts.",
        "CRITICAL: Do NOT ask the user to provide these keys, and NEVER echo, print, or run `curl -v` in a way that outputs raw key values to stdout/stderr.",
    ])
    atomic_write_secure(str(API_KEYS_MANIFEST_FILE), "\n".join(lines) + "\n", mode=0o600)


def save_api_key(name: str, value: str, description: str = "") -> dict[str, Any]:
    validate_api_key_name(name)
    store = load_api_keys_store()
    keys = store.setdefault("keys", {})
    keys[name] = {
        "value": value,
        "description": description.strip(),
        "created_at": int(time.time()),
    }
    store["updated_at"] = int(time.time())
    atomic_write_secure(str(API_KEYS_FILE), json.dumps(store, indent=2) + "\n", mode=0o600)
    sync_api_keys_manifest()
    return {"name": name, "description": description, "masked": mask_api_key(value), "created_at": keys[name]["created_at"]}


def delete_api_key(name: str) -> bool:
    store = load_api_keys_store()
    keys = store.get("keys", {})
    if name in keys:
        del keys[name]
        store["updated_at"] = int(time.time())
        atomic_write_secure(str(API_KEYS_FILE), json.dumps(store, indent=2) + "\n", mode=0o600)
        sync_api_keys_manifest()
        return True
    return False


def list_api_keys_masked() -> list[dict[str, Any]]:
    store = load_api_keys_store()
    result = []
    for k, v in sorted(store.get("keys", {}).items()):
        val = v.get("value", "")
        result.append({
            "name": k,
            "description": v.get("description", ""),
            "masked": mask_api_key(val),
            "created_at": v.get("created_at", 0),
        })
    return result


def get_api_key_raw(name: str) -> str:
    store = load_api_keys_store()
    keys = store.get("keys", {})
    if name in keys:
        return keys[name].get("value", "")
    raise KeyError(f"Key '{name}' not found")


# 4. Skills Discovery & Authoring
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def parse_skill_frontmatter(content: str) -> tuple[dict[str, str], str]:
    normalized = content.replace("\r\n", "\n")
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", normalized, re.DOTALL)
    if not match:
        raise ValueError("SKILL.md must begin with fenced YAML frontmatter (---)")
    raw_yaml, body = match.group(1), match.group(2).strip()

    fm: dict[str, str] = {}
    try:
        import yaml
        loaded = yaml.safe_load(raw_yaml)
        if isinstance(loaded, dict):
            fm = {str(k): str(v).strip() for k, v in loaded.items() if v is not None}
    except ImportError:
        current_key = None
        block_mode = None
        block_lines = []
        for line in raw_yaml.split("\n"):
            if current_key and (line.startswith("  ") or not line.strip()):
                block_lines.append(line.strip())
                continue
            elif current_key:
                joiner = " " if block_mode in (">-", ">") else "\n"
                fm[current_key] = joiner.join(l for l in block_lines if l).strip()
                current_key, block_mode, block_lines = None, None, []
            m = re.match(r"^([a-zA-Z0-9_-]+)\s*:\s*(.*)$", line)
            if m:
                k, v = m.group(1), m.group(2).strip()
                if v in (">-", ">", "|", "|-"):
                    current_key, block_mode, block_lines = k, v, []
                else:
                    fm[k] = v.strip("\"'")
        if current_key:
            joiner = " " if block_mode in (">-", ">") else "\n"
            fm[current_key] = joiner.join(l for l in block_lines if l).strip()

    name = fm.get("name", "")
    desc = fm.get("description", "")
    if not isinstance(name, str) or not SKILL_NAME_RE.match(name):
        raise ValueError("Frontmatter 'name' is required and must be lowercase kebab-case (a-z0-9-)")
    if not isinstance(desc, str) or len(desc) < 20:
        raise ValueError("Frontmatter 'description' is required and must be at least 20 characters")
    if not body:
        raise ValueError("SKILL.md must contain markdown instructions after frontmatter")
    return {"name": name, "description": desc}, body


def resolve_safe_skill_dir(target_path: str, allowed_roots: list[str]) -> str:
    real_target = os.path.realpath(target_path)
    if os.path.basename(real_target) == "SKILL.md":
        real_target = os.path.dirname(real_target)
    for root in allowed_roots:
        real_root = os.path.realpath(root)
        if os.path.dirname(real_target) == real_root and real_target != real_root:
            skill_slug = os.path.basename(real_target)
            if SKILL_NAME_RE.match(skill_slug):
                return real_target
    raise PermissionError(f"Path '{target_path}' is outside allowed mutable skill directories")


def discover_skills(workspace_dir: Optional[str] = None, include_builtin: bool = False) -> list[dict[str, Any]]:
    skills = []
    seen_names = set()

    # 1. Global Skills: ~/.gemini/config/skills/
    if GLOBAL_SKILLS_DIR.exists():
        for d in sorted(GLOBAL_SKILLS_DIR.iterdir()):
            if d.is_dir() and (d / "SKILL.md").exists():
                try:
                    meta, _ = parse_skill_frontmatter((d / "SKILL.md").read_text(encoding="utf-8"))
                    skills.append({
                        "name": meta["name"],
                        "folder_name": d.name,
                        "description": meta["description"],
                        "scope": "global",
                        "path": str(d / "SKILL.md"),
                        "dir_path": str(d),
                        "is_builtin": False,
                    })
                    seen_names.add(meta["name"])
                except Exception as exc:
                    logger.debug("Skipping invalid global skill %s: %s", d.name, exc)

    # 2. Workspace Skills: <workspace>/.agents/skills/
    if workspace_dir and Path(workspace_dir).is_dir():
        ws_skills = Path(workspace_dir) / ".agents" / "skills"
        if ws_skills.exists():
            for d in sorted(ws_skills.iterdir()):
                if d.is_dir() and (d / "SKILL.md").exists():
                    try:
                        meta, _ = parse_skill_frontmatter((d / "SKILL.md").read_text(encoding="utf-8"))
                        skills.append({
                            "name": meta["name"],
                            "folder_name": d.name,
                            "description": meta["description"],
                            "scope": "workspace",
                            "path": str(d / "SKILL.md"),
                            "dir_path": str(d),
                            "is_builtin": False,
                        })
                        seen_names.add(meta["name"])
                    except Exception as exc:
                        logger.debug("Skipping invalid workspace skill %s: %s", d.name, exc)

    # 3. Builtin Skills: ~/.gemini/antigravity-cli/builtin/skills/
    if include_builtin and BUILTIN_SKILLS_DIR.exists():
        for d in sorted(BUILTIN_SKILLS_DIR.iterdir()):
            if d.is_dir() and (d / "SKILL.md").exists():
                try:
                    meta, _ = parse_skill_frontmatter((d / "SKILL.md").read_text(encoding="utf-8"))
                    if meta["name"] not in seen_names:
                        skills.append({
                            "name": meta["name"],
                            "folder_name": d.name,
                            "description": meta["description"],
                            "scope": "builtin",
                            "path": str(d / "SKILL.md"),
                            "dir_path": str(d),
                            "is_builtin": True,
                        })
                except Exception as exc:
                    logger.debug("Skipping invalid builtin skill %s: %s", d.name, exc)

    return [
        s for s in skills
        if s.get("name") not in PROTECTED_SYSTEM_SKILLS
        and s.get("folder_name") not in PROTECTED_SYSTEM_SKILLS
    ]


def get_skill_content(target_path: str, workspace_dir: Optional[str] = None) -> str:
    allowed_roots = [str(GLOBAL_SKILLS_DIR), str(BUILTIN_SKILLS_DIR)]
    if workspace_dir and Path(workspace_dir).is_dir():
        allowed_roots.append(str(Path(workspace_dir) / ".agents" / "skills"))
    real_path = os.path.realpath(target_path)
    if Path(real_path).parent.name in PROTECTED_SYSTEM_SKILLS:
        raise PermissionError(f"Protected system skills cannot be edited")
    is_allowed = False
    for r in allowed_roots:
        if real_path.startswith(os.path.realpath(r)):
            is_allowed = True
            break
    if not is_allowed or not os.path.isfile(real_path) or not real_path.endswith("SKILL.md"):
        raise PermissionError(f"Unauthorized path access: {target_path}")
    return Path(real_path).read_text(encoding="utf-8")


def save_skill(name: str, scope: str, content: str, workspace_dir: Optional[str] = None) -> dict[str, Any]:
    meta, _ = parse_skill_frontmatter(content)
    slug = meta["name"]
    if slug in PROTECTED_SYSTEM_SKILLS or name in PROTECTED_SYSTEM_SKILLS:
        raise PermissionError(f"Cannot overwrite protected system skill '{slug}'")
    if scope.lower() == "workspace":
        if not workspace_dir or not Path(workspace_dir).is_dir():
            raise ValueError("No active workspace selected for workspace skill")
        root = Path(workspace_dir) / ".agents" / "skills"
    else:
        root = GLOBAL_SKILLS_DIR

    allowed_roots = [str(GLOBAL_SKILLS_DIR)]
    if workspace_dir and Path(workspace_dir).is_dir():
        allowed_roots.append(str(Path(workspace_dir) / ".agents" / "skills"))

    root.mkdir(parents=True, exist_ok=True)
    target_dir = root / slug
    safe_dir = resolve_safe_skill_dir(str(target_dir), allowed_roots)
    if Path(safe_dir).name in PROTECTED_SYSTEM_SKILLS:
        raise PermissionError(f"Cannot overwrite protected system skill '{Path(safe_dir).name}'")
    os.makedirs(safe_dir, exist_ok=True)
    target_skill_file = Path(safe_dir) / "SKILL.md"
    atomic_write_secure(str(target_skill_file), content, mode=0o644)
    return {
        "name": slug,
        "description": meta["description"],
        "scope": "Workspace" if scope.lower() == "workspace" else "Global",
        "path": str(target_skill_file),
        "dir_path": safe_dir,
    }


def delete_skill(target_path: str, workspace_dir: Optional[str] = None) -> bool:
    allowed_roots = [str(GLOBAL_SKILLS_DIR)]
    if workspace_dir and Path(workspace_dir).is_dir():
        allowed_roots.append(str(Path(workspace_dir) / ".agents" / "skills"))
    safe_dir = resolve_safe_skill_dir(target_path, allowed_roots)
    if Path(safe_dir).name in PROTECTED_SYSTEM_SKILLS:
        raise PermissionError(f"Cannot delete protected system skill '{Path(safe_dir).name}'")
    if os.path.exists(safe_dir) and os.path.isdir(safe_dir):
        shutil.rmtree(safe_dir)
        return True
    return False


# 5. Model Context Protocol (MCP) Management
def load_masked_mcp_servers() -> list[dict[str, Any]]:
    if not MCP_CONFIG_FILE.exists():
        atomic_write_secure(str(MCP_CONFIG_FILE), '{"mcpServers": {}}\n', mode=0o600)
        return []
    os.chmod(str(MCP_CONFIG_FILE), 0o600)
    try:
        data = json.loads(MCP_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    servers = data.get("mcpServers", {})
    server_list = []
    for name, srv in sorted(servers.items()):
        copy_srv = dict(srv)
        copy_srv["name"] = name
        trans = "http" if ("serverUrl" in copy_srv or "url" in copy_srv or copy_srv.get("type") == "http") else "stdio"
        copy_srv["transport"] = trans
        copy_srv["target"] = copy_srv.get("serverUrl") or copy_srv.get("url") or copy_srv.get("command") or ""
        copy_srv["enabled"] = not copy_srv.get("disabled", False)
        if "headers" in copy_srv and isinstance(copy_srv["headers"], dict):
            copy_srv["headers"] = {
                k: mask_api_key(v) for k, v in copy_srv["headers"].items()
            }
        if "env" in copy_srv and isinstance(copy_srv["env"], dict):
            copy_srv["env"] = {
                k: mask_api_key(v) if any(x in k for x in ("KEY", "TOKEN", "SECRET", "PASSWORD")) else v
                for k, v in copy_srv["env"].items()
            }
        server_list.append(copy_srv)
    return server_list


async def probe_single_mcp_server(name: str) -> dict[str, Any]:
    if not MCP_CONFIG_FILE.exists():
        return {"status": "error", "name": name, "message": "Configuration file not found"}
    try:
        data = json.loads(MCP_CONFIG_FILE.read_text(encoding="utf-8"))
        servers = data.get("mcpServers", {})
    except Exception as exc:
        return {"status": "error", "name": name, "message": f"Cannot read config: {exc}"}

    srv = servers.get(name)
    if not srv:
        return {"status": "error", "name": name, "message": f"Server '{name}' not found in configuration"}

    if srv.get("disabled", False):
        return {"status": "disabled", "name": name, "message": "Server is disabled in configuration"}

    trans = "http" if ("serverUrl" in srv or "url" in srv or srv.get("type") == "http") else "stdio"
    if trans == "http":
        url = srv.get("serverUrl") or srv.get("url") or ""
        if not url:
            return {"status": "error", "name": name, "message": "No endpoint URL configured"}
        if not (url.startswith("http://") or url.startswith("https://")):
            url = "http://" + url
        raw_headers = dict(srv.get("headers") or {})
        loop = asyncio.get_running_loop()

        def _do_http() -> dict[str, Any]:
            import ssl
            t0 = time.time()
            headers = {"User-Agent": f"AntigravityStudio/{APP_VERSION}", **raw_headers}
            req = urllib.request.Request(url, headers=headers)
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            try:
                with urllib.request.urlopen(req, timeout=5.0, context=ctx) as resp:
                    elapsed = int((time.time() - t0) * 1000)
                    return {"status": "connected", "name": name, "message": f"Reachable (HTTP {resp.status}) · {elapsed}ms"}
            except urllib.error.HTTPError as e:
                elapsed = int((time.time() - t0) * 1000)
                # 401/403 (Auth required) and 405 (Method Not Allowed, e.g. server expects POST for JSON-RPC)
                # indicate an active MCP server running and processing requests.
                if e.code in (401, 403, 405):
                    return {"status": "connected", "name": name, "message": f"Active (HTTP {e.code}) · {elapsed}ms"}
                # Any 5xx code (500, 502, 503, 504, 520..530, etc.) is a server/gateway error and MUST be considered offline
                elif e.code >= 500:
                    reason = getattr(e, "reason", "Server Error") or "Server Error"
                    return {"status": "error", "name": name, "message": f"Server Error (HTTP {e.code}: {reason}) · {elapsed}ms"}
                # 404 is an invalid/missing endpoint URL
                elif e.code == 404:
                    return {"status": "error", "name": name, "message": f"Endpoint Not Found (HTTP 404) · {elapsed}ms"}
                else:
                    reason = getattr(e, "reason", "HTTP Error") or "HTTP Error"
                    return {"status": "error", "name": name, "message": f"HTTP {e.code} ({reason}) · {elapsed}ms"}
            except Exception as e:
                msg = str(e)
                if hasattr(e, "reason"):
                    msg = str(e.reason)
                return {"status": "error", "name": name, "message": f"Connection failed: {msg}"}

        return await loop.run_in_executor(None, _do_http)
    else:
        command = srv.get("command") or ""
        if not command:
            return {"status": "error", "name": name, "message": "No command executable configured"}
        exe = shutil.which(command) or (os.path.exists(command) and command)
        if not exe and Path(USR_DIR).exists():
            cand = Path(USR_DIR) / "bin" / command
            if cand.exists():
                exe = str(cand)
        if not exe:
            return {"status": "error", "name": name, "message": f"Executable '{command}' not found in PATH"}

        args = srv.get("args") or []
        if args:
            first_arg = str(args[0])
            if first_arg.endswith((".py", ".sh", ".js")) or "/" in first_arg:
                p = Path(first_arg).expanduser()
                if not p.is_file():
                    return {"status": "error", "name": name, "message": f"Script not found: {Path(first_arg).name}"}
                return {"status": "connected", "name": name, "message": f"Verified: {command} {Path(first_arg).name}"}
        return {"status": "connected", "name": name, "message": f"Verified: {command}"}


async def probe_mcp_health() -> dict[str, dict[str, str]]:
    if not MCP_CONFIG_FILE.exists():
        return {}
    try:
        data = json.loads(MCP_CONFIG_FILE.read_text(encoding="utf-8"))
        servers = data.get("mcpServers", {})
    except Exception:
        return {}

    results = {}
    for name in servers:
        try:
            results[name] = await probe_single_mcp_server(name)
        except Exception as exc:
            results[name] = {"status": "error", "name": name, "message": str(exc)}
    return results


async def add_mcp_server(
    name: str,
    transport: str,
    url_or_command: str,
    args: Optional[list[str]] = None,
    headers: Optional[dict[str, str]] = None,
    env: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    if not re.match(r"^[a-zA-Z0-9_-]+$", name):
        raise ValueError("Server name must be alphanumeric with hyphens or underscores")

    trans = transport.lower()
    cmd = [str(AGY_BIN), "mcp", "add", "--type", trans]

    if trans == "http":
        if headers:
            for hk, hv in headers.items():
                if hk and hv:
                    cmd.extend(["-H", f"{hk}: {hv}"])
        cmd.extend([name, url_or_command])
    else:  # stdio
        merged_env = {
            "PATH": f"{USR_DIR}/bin:/system/bin",
            "LD_LIBRARY_PATH": f"{USR_DIR}/lib",
            "LD_PRELOAD": f"{USR_DIR}/lib/libagy_shim.so",
            "SSL_CERT_FILE": f"{USR_DIR}/etc/tls/cert.pem",
        }
        if env:
            merged_env.update(env)
        for ek, ev in merged_env.items():
            if ek and ev:
                cmd.extend(["-e", f"{ek}={ev}"])
        cmd.extend([name, url_or_command])
        if args:
            cmd.extend(args)

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15.0)
    if proc.returncode != 0:
        raise RuntimeError(f"agy mcp add failed: {stderr.decode('utf-8', 'replace')}")

    if MCP_CONFIG_FILE.exists():
        os.chmod(str(MCP_CONFIG_FILE), 0o600)
    return {"status": "ok", "name": name, "transport": trans}


async def toggle_mcp_server(name: str, enabled: bool) -> dict[str, Any]:
    sub = "enable" if enabled else "disable"
    cmd = [str(AGY_BIN), "mcp", sub, name]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)
    if proc.returncode != 0:
        raise RuntimeError(f"agy mcp {sub} failed: {stderr.decode('utf-8', 'replace')}")
    if MCP_CONFIG_FILE.exists():
        os.chmod(str(MCP_CONFIG_FILE), 0o600)
    return {"status": "ok", "name": name, "enabled": enabled}


async def delete_mcp_server(name: str) -> dict[str, Any]:
    try:
        cmd = [str(AGY_BIN), "mcp", "remove", name]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await asyncio.wait_for(proc.communicate(), timeout=8.0)
    except Exception as exc:
        logger.warning("agy mcp remove warning for %s: %s", name, exc)

    # Ensure removal directly from MCP_CONFIG_FILE
    if MCP_CONFIG_FILE.exists():
        try:
            data = json.loads(MCP_CONFIG_FILE.read_text(encoding="utf-8"))
            if "mcpServers" in data and name in data["mcpServers"]:
                del data["mcpServers"][name]
                atomic_write_secure(str(MCP_CONFIG_FILE), json.dumps(data, indent=2) + "\n", mode=0o600)
        except Exception as exc:
            logger.warning("Failed pruning %s from %s: %s", name, MCP_CONFIG_FILE, exc)

    return {"status": "ok", "name": name}


async def check_github_updates() -> dict[str, Any]:
    installed_version = os.environ.get("AGY_APP_VERSION", "1.3.1")
    installed_code = int(os.environ.get("AGY_APP_VERSION_CODE", "14"))
    url = "https://api.github.com/repos/MisterMoeGreen/Antigravity-Studio-App/releases/latest"

    cmd = ["curl", "-sSL", "-H", f"User-Agent: AntigravityStudio/{APP_VERSION}", url]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=8.0)
    if proc.returncode != 0 or not stdout:
        return {"status": "error", "message": "Failed to connect to GitHub releases"}

    try:
        data = json.loads(stdout.decode("utf-8"))
    except Exception as exc:
        return {"status": "error", "message": f"Malformed JSON from releases API: {exc}"}

    tag_name = data.get("tag_name", "").lstrip("v")
    body = data.get("body", "")
    download_url = None
    remote_code = None

    for asset in data.get("assets", []):
        aname = asset.get("name", "")
        if aname == "release-metadata.json":
            meta_url = asset.get("browser_download_url")
            try:
                mproc = await asyncio.create_subprocess_exec(
                    "curl", "-sSL", meta_url,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                mstdout, _ = await asyncio.wait_for(mproc.communicate(), timeout=5.0)
                if mproc.returncode == 0 and mstdout:
                    mjson = json.loads(mstdout.decode("utf-8"))
                    remote_code = mjson.get("version_code")
            except Exception:
                pass
        elif aname.endswith(".apk") and not download_url:
            download_url = asset.get("browser_download_url")

    has_update = False
    if remote_code and remote_code > installed_code:
        has_update = True
    elif tag_name and tag_name != installed_version.lstrip("v"):
        try:
            r_parts = [int(p) for p in tag_name.split("-")[0].split(".")]
            l_parts = [int(p) for p in installed_version.lstrip("v").split("-")[0].split(".")]
            max_len = max(len(r_parts), len(l_parts))
            r_parts += [0] * (max_len - len(r_parts))
            l_parts += [0] * (max_len - len(l_parts))
            if r_parts > l_parts:
                has_update = True
        except Exception:
            if tag_name != installed_version.lstrip("v"):
                has_update = True

    up_to_date = not has_update
    clean_latest = ("v" + tag_name) if not tag_name.startswith("v") else tag_name
    clean_current = ("v" + installed_version.lstrip("v"))

    return {
        "status": "ok",
        "has_update": has_update,
        "up_to_date": up_to_date,
        "current_version": clean_current,
        "current_code": installed_code,
        "latest_version": clean_latest,
        "latest_code": remote_code,
        "download_url": download_url,
        "changelog": body,
    }


# ---------------------------------------------------------------------------
# Package Manager Engine: Alpine Linux Container (W^X PRoot Subsystem)
# ---------------------------------------------------------------------------

PROTECTED_PACKAGES: frozenset[str] = frozenset({
    "alpine-baselayout",
    "alpine-baselayout-data",
    "alpine-keys",
    "alpine-release",
    "apk-tools",
    "busybox",
    "busybox-binsh",
    "ca-certificates-bundle",
    "libc-utils",
    "libcrypto3",
    "libssl3",
    "musl",
    "musl-utils",
    "scanelf",
    "ssl_client",
    "zlib",
})

APK_PKG_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._+-]{0,127}$")


def validate_pkg_name(name: str) -> str:
    if not isinstance(name, str) or not APK_PKG_NAME_RE.match(name):
        raise ValueError(f"Invalid Alpine package name: {name!r}")
    return name


def get_rootfs_path() -> Path:
    candidates = [
        os.environ.get("AGY_ROOTFS_DIR"),
        os.path.join(os.environ.get("AGY_FILES_DIR", ""), "rootfs") if os.environ.get("AGY_FILES_DIR") else None,
        str(Path(os.environ.get("PREFIX", "/data/data/ai.agy.app/files/usr")).parent / "rootfs"),
        "/data/data/ai.agy.app/files/rootfs",
        str(Path(__file__).resolve().parent.parent / "rootfs"),
        str(Path(__file__).resolve().parents[3] / "rootfs") if len(Path(__file__).resolve().parents) >= 4 else None,
        "/data/data/com.termux/files/rootfs",
    ]
    for c in candidates:
        if c and Path(c).is_dir() and (Path(c) / "etc" / "os-release").exists():
            return Path(c)
    if candidates[0]:
        return Path(candidates[0])
    return Path("/data/data/ai.agy.app/files/rootfs")


def get_apk_bin() -> str:
    candidates = [
        shutil.which("apk"),
        shutil.which("pkg"),
        os.path.join(os.environ.get("PREFIX", "/data/data/ai.agy.app/files/usr"), "bin", "apk"),
        "/data/data/ai.agy.app/files/usr/bin/apk",
        "/data/data/com.termux/files/usr/bin/apk",
    ]
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return "apk"


def get_installed_packages() -> dict[str, Any]:
    """
    Directly parses /etc/apk/world and /lib/apk/db/installed in < 3ms.
    Returns:
        {
            "packages": [
                {
                    "name": str,
                    "version": str,
                    "size_mb": float,
                    "description": str,
                    "url": str,
                }, ...
            ],
            "total_size_mb": float
        }
    """
    rootfs = get_rootfs_path()
    world_path = rootfs / "etc" / "apk" / "world"
    installed_path = rootfs / "lib" / "apk" / "db" / "installed"

    if not world_path.is_file() or not installed_path.is_file():
        return {"packages": [], "total_size_mb": 0.0}

    try:
        world_raw = world_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.warning("Could not read apk world: %s", exc)
        return {"packages": [], "total_size_mb": 0.0}

    world_packages: list[str] = []
    for line in world_raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        clean_name = re.split(r"[@=<>~]", line, maxsplit=1)[0].strip()
        if clean_name and clean_name not in PROTECTED_PACKAGES and clean_name not in world_packages:
            world_packages.append(clean_name)

    try:
        installed_raw = installed_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.warning("Could not read apk installed db: %s", exc)
        return {"packages": [], "total_size_mb": 0.0}

    installed_index: dict[str, dict[str, Any]] = {}
    current_entry: dict[str, Any] = {}

    for line in installed_raw.splitlines():
        if not line.strip():
            if "name" in current_entry:
                installed_index[current_entry["name"]] = current_entry
            current_entry = {}
            continue

        if len(line) >= 2 and line[1] == ":":
            tag = line[0]
            val = line[2:].strip()
            if tag == "P":
                current_entry["name"] = val
            elif tag == "V":
                current_entry["version"] = val
            elif tag == "I":
                try:
                    current_entry["size_bytes"] = int(val)
                except ValueError:
                    current_entry["size_bytes"] = 0
            elif tag == "T":
                current_entry["desc"] = val
            elif tag == "U":
                current_entry["url"] = val
            elif tag == "D":
                raw_deps = val.split()
                deps: list[str] = []
                for d in raw_deps:
                    if d.startswith(("so:", "cmd:", "pc:", "!")):
                        continue
                    d_clean = re.split(r"[@=<>~]", d, maxsplit=1)[0].strip()
                    if d_clean and d_clean not in deps:
                        deps.append(d_clean)
                current_entry["deps"] = deps

    if "name" in current_entry:
        installed_index[current_entry["name"]] = current_entry

    total_bytes = sum(
        info.get("size_bytes", 0)
        for name, info in installed_index.items()
        if name not in PROTECTED_PACKAGES
    )
    total_size_mb = round(total_bytes / (1024 * 1024), 1)

    packages: list[dict[str, Any]] = []
    for pkg_name in world_packages:
        info = installed_index.get(pkg_name, {})
        version = info.get("version", "unknown")
        desc = info.get("desc", "")
        url = info.get("url", "")
        size_bytes = info.get("size_bytes", 0)

        # Meta-package rollup (e.g. build-base size <= 16KB)
        if size_bytes <= 16384 and info.get("deps"):
            rolled_up = sum(
                installed_index.get(dep, {}).get("size_bytes", 0)
                for dep in info["deps"]
                if dep not in PROTECTED_PACKAGES
            )
            if rolled_up > size_bytes:
                size_bytes = rolled_up

        size_mb = round(size_bytes / (1024 * 1024), 1)

        packages.append({
            "name": pkg_name,
            "version": version,
            "size_mb": size_mb,
            "description": desc,
            "url": url,
        })

    return {
        "packages": packages,
        "total_size_mb": total_size_mb,
    }


APK_MUTATION_LOCK = asyncio.Lock()


async def run_apk_command(*args: str, timeout: float = 180.0) -> tuple[int, str, str]:
    apk_bin = get_apk_bin()
    cmd = [apk_bin, "--wait", "30", *args]
    logger.info("Executing APK command: %s", " ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        return proc.returncode, stdout, stderr
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        raise TimeoutError(f"APK command timed out after {timeout} seconds")


async def delete_package(name: str) -> tuple[bool, str]:
    validate_pkg_name(name)
    if name in PROTECTED_PACKAGES:
        raise PermissionError(f"Cannot delete protected system package: {name}")

    async with APK_MUTATION_LOCK:
        code, stdout, stderr = await run_apk_command("del", "--purge", "--", name)
        output = stdout + "\n" + stderr
        if code != 0:
            return False, f"Failed to delete {name}: {output.strip()}"
        if "not removed due to" in output:
            return False, f"Cannot remove {name}: required by other installed packages"
        return True, f"Successfully removed {name}"


async def upgrade_package(name: str) -> tuple[bool, str]:
    validate_pkg_name(name)
    async with APK_MUTATION_LOCK:
        code, stdout, stderr = await run_apk_command("add", "--upgrade", "--", name)
        output = stdout + "\n" + stderr
        if code != 0:
            return False, f"Failed to upgrade {name}: {output.strip()}"
        return True, f"Successfully upgraded {name}"


async def upgrade_all_packages() -> tuple[bool, str]:
    async with APK_MUTATION_LOCK:
        code, stdout, stderr = await run_apk_command("upgrade", "--update-cache")
        output = stdout + "\n" + stderr
        if code != 0:
            return False, f"Failed to upgrade packages: {output.strip()}"
        return True, "Successfully upgraded all packages"


# ---------------------------------------------------------------------------
# Storage Engine: Read-Only SQLite & JSONL Transcript Inspector
# ---------------------------------------------------------------------------

class AntigravityStorageEngine:
    """
    Provides non-blocking, zero-lock access to Antigravity's SQLite databases
    and JSONL step transcripts.
    """

    def _resolve_session_title(self, row: sqlite3.Row) -> str:
        cid = row["conversation_id"]

        # 1. Native CLI annotation title (written by agy CLI when a title is assigned)
        pbtxt_path = ANNOTATIONS_DIR / f"{cid}.pbtxt"
        if pbtxt_path.exists():
            try:
                content = pbtxt_path.read_text(encoding="utf-8", errors="replace")
                m = re.search(r'title:\s*"(.*?)"', content)
                if m and m.group(1).strip():
                    return m.group(1).strip()
            except Exception:
                pass

        # 2. SQLite conversation_summaries.db title column
        title = (row["title"] or "").strip()
        if title:
            return title

        # 3. SQLite preview column (standard CLI stores initial prompt or topic in preview)
        preview = (row["preview"] or "").strip()
        if preview:
            first_line = preview.split("\n")[0].strip()
            first_line = re.sub(r"<[^>]+>", "", first_line).strip()
            if first_line:
                return first_line[:65] if len(first_line) > 65 else first_line

        # 4. Fallback: inspect transcript.jsonl for first user prompt
        t_jsonl = BRAIN_DIR / cid / ".system_generated" / "logs" / "transcript.jsonl"
        if t_jsonl.exists():
            try:
                with t_jsonl.open("r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        ev = json.loads(line)
                        if ev.get("type") in ("USER_INPUT", "USER") or ev.get("role") == "user":
                            raw_txt = self._clean_user_prompt(self._extract_text(ev)).strip()
                            if raw_txt:
                                first_l = raw_txt.split("\n")[0].strip()
                                if first_l:
                                    return first_l[:65] if len(first_l) > 65 else first_l
            except Exception:
                pass

        # 5. Last resort identifier
        return f"Session {cid[:8]}"

    def list_conversations(self, search_query: str = "", limit: int = 100) -> list[dict[str, Any]]:
        if not SUMMARIES_DB.exists():
            return []

        uri = f"file:{SUMMARIES_DB}?mode=ro"
        results: list[dict[str, Any]] = []

        try:
            conn = sqlite3.connect(uri, uri=True, timeout=2.0)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            if search_query.strip():
                q = f"%{search_query.strip()}%"
                cursor.execute(
                    """
                    SELECT conversation_id, title, preview, step_count,
                           last_modified_time, workspace_uris, status,
                           project_id, agent_name, group_id
                    FROM conversation_summaries
                    WHERE title LIKE ? OR preview LIKE ? OR conversation_id LIKE ?
                    ORDER BY datetime(last_modified_time) DESC
                    LIMIT ?
                    """,
                    (q, q, q, limit),
                )
            else:
                cursor.execute(
                    """
                    SELECT conversation_id, title, preview, step_count,
                           last_modified_time, workspace_uris, status,
                           project_id, agent_name, group_id
                    FROM conversation_summaries
                    ORDER BY datetime(last_modified_time) DESC
                    LIMIT ?
                    """,
                    (limit,),
                )

            app_settings = load_app_settings()
            session_workspaces = app_settings.get("session_workspaces", {})

            for row in cursor.fetchall():
                cid = row["conversation_id"]
                effective_title = self._resolve_session_title(row)

                workspaces: list[str] = []
                if cid in session_workspaces and Path(session_workspaces[cid]).is_dir():
                    workspaces = [session_workspaces[cid]]
                else:
                    raw_ws = row["workspace_uris"]
                    if raw_ws:
                        try:
                            parsed = json.loads(raw_ws)
                            if isinstance(parsed, list):
                                workspaces = [str(w).replace("file://", "") for w in parsed]
                        except Exception:
                            workspaces = [str(raw_ws)]

                results.append(
                    {
                        "conversation_id": cid,
                        "title": effective_title,
                        "preview": row["preview"] or "",
                        "step_count": row["step_count"] or 0,
                        "last_modified_time": row["last_modified_time"] or "",
                        "workspaces": workspaces,
                        "status": row["status"] or "CASCADE_RUN_STATUS_IDLE",
                        "project_id": row["project_id"] or "",
                        "agent_name": row["agent_name"] or "antigravity",
                    }
                )
            conn.close()

        except Exception as exc:
            logger.warning("SQLite read warning on conversation_summaries.db: %s", exc)

        return results

    def get_conversation_meta(self, conversation_id: str) -> dict[str, Any]:
        sessions = self.list_conversations(search_query=conversation_id, limit=5)
        for s in sessions:
            if s["conversation_id"] == conversation_id:
                return s
        pbtxt_path = ANNOTATIONS_DIR / f"{conversation_id}.pbtxt"
        annot_title = ""
        if pbtxt_path.exists():
            try:
                m = re.search(r'title:\s*"(.*?)"', pbtxt_path.read_text(encoding="utf-8"))
                if m:
                    annot_title = m.group(1).strip()
            except Exception:
                pass
        ws: list[str] = []
        app_settings = load_app_settings()
        if sw := app_settings.get("session_workspaces", {}).get(conversation_id):
            if Path(sw).is_dir():
                ws = [sw]
        return {
            "conversation_id": conversation_id,
            "title": annot_title or f"Session {conversation_id[:8]}",
            "preview": "",
            "step_count": 0,
            "last_modified_time": "",
            "workspaces": ws,
            "status": "CASCADE_RUN_STATUS_IDLE",
        }

    def rename_conversation(self, conversation_id: str, new_title: str) -> bool:
        cid = conversation_id.strip()
        clean_title = new_title.strip()
        if not cid or not clean_title:
            return False

        # 1. Update annotations/<cid>.pbtxt (native CLI title definition read by agy CLI)
        try:
            ANNOTATIONS_DIR.mkdir(parents=True, exist_ok=True)
            pbtxt_path = ANNOTATIONS_DIR / f"{cid}.pbtxt"
            escaped_title = clean_title.replace('"', '\\"')
            pbtxt_path.write_text(f'title:"{escaped_title}"\n', encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed updating annotations pbtxt for %s: %s", cid, exc)

        # 2. Update conversation_summaries.db (SQLite summary table)
        if SUMMARIES_DB.exists():
            try:
                conn = sqlite3.connect(SUMMARIES_DB, timeout=5.0)
                with conn:
                    conn.execute(
                        "UPDATE conversation_summaries SET title = ? WHERE conversation_id = ?",
                        (clean_title, cid),
                    )
                    try:
                        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    except Exception:
                        pass
                conn.close()
            except Exception as exc:
                logger.warning("Failed updating title in conversation_summaries.db: %s", exc)

        # 3. Update cache/conversation_metadata.json if present
        if CONV_METADATA_JSON.exists():
            try:
                meta_dict = json.loads(CONV_METADATA_JSON.read_text(encoding="utf-8"))
                if isinstance(meta_dict, dict):
                    convs = meta_dict.get("conversations", meta_dict)
                    if cid in convs and isinstance(convs[cid], dict) and "summary" in convs[cid]:
                        convs[cid]["summary"]["Title"] = clean_title
                        CONV_METADATA_JSON.write_text(
                            json.dumps(meta_dict, indent=2, ensure_ascii=False), encoding="utf-8"
                        )
            except Exception as exc:
                logger.warning("Failed updating conversation_metadata.json for %s: %s", cid, exc)

        logger.info("Renamed conversation %s to '%s'", cid, clean_title)
        return True

    def update_conversation_workspace(self, conversation_id: str, workspace_path: str) -> bool:
        cid = conversation_id.strip()
        clean_path = str(Path(workspace_path).resolve())
        if not cid or not clean_path:
            return False

        ws_uri = f"file://{clean_path}"

        # 1. Update conversations/<cid>.db (trajectory_metadata_blob)
        conv_db = CONVERSATIONS_DIR / f"{cid}.db"
        if conv_db.exists():
            try:
                conn = sqlite3.connect(conv_db, timeout=5.0)
                with conn:
                    cur = conn.cursor()
                    row = cur.execute("SELECT id, data FROM trajectory_metadata_blob WHERE id = 'main'").fetchone()
                    if not row:
                        row = cur.execute("SELECT id, data FROM trajectory_metadata_blob LIMIT 1").fetchone()
                    if row and row[1]:
                        patched_blob = patch_trajectory_metadata_blob(row[1], ws_uri)
                        cur.execute("UPDATE trajectory_metadata_blob SET data = ? WHERE id = ?", (patched_blob, row[0]))
                conn.close()
                logger.info("Updated trajectory_metadata_blob for %s to %s", cid, clean_path)
            except Exception as exc:
                logger.warning("Failed updating trajectory_metadata_blob in %s: %s", conv_db, exc)

        # 2. Update conversation_summaries.db (workspace_uris and raw_summary)
        if SUMMARIES_DB.exists():
            try:
                conn = sqlite3.connect(SUMMARIES_DB, timeout=5.0)
                with conn:
                    cur = conn.cursor()
                    cur.execute(
                        "UPDATE conversation_summaries SET workspace_uris = ? WHERE conversation_id = ?",
                        (json.dumps([ws_uri]), cid),
                    )
                    row = cur.execute("SELECT raw_summary FROM conversation_summaries WHERE conversation_id = ?", (cid,)).fetchone()
                    if row and row[0]:
                        try:
                            patched_raw = patch_raw_summary_workspace(row[0], ws_uri)
                            cur.execute("UPDATE conversation_summaries SET raw_summary = ? WHERE conversation_id = ?", (patched_raw, cid))
                        except Exception:
                            pass
                conn.close()
                logger.info("Updated conversation_summaries.db workspace for %s to '%s'", cid, clean_path)
            except Exception as exc:
                logger.warning("Failed updating workspace in conversation_summaries.db: %s", exc)

        # 3. Update cache/conversation_metadata.json if present
        if CONV_METADATA_JSON.exists():
            try:
                meta_dict = json.loads(CONV_METADATA_JSON.read_text(encoding="utf-8"))
                if isinstance(meta_dict, dict):
                    convs = meta_dict.get("conversations", meta_dict)
                    if cid in convs and isinstance(convs[cid], dict):
                        if "summary" in convs[cid] and isinstance(convs[cid]["summary"], dict):
                            convs[cid]["summary"]["WorkspaceURIs"] = [ws_uri]
                        CONV_METADATA_JSON.write_text(
                            json.dumps(meta_dict, indent=2, ensure_ascii=False), encoding="utf-8"
                        )
            except Exception as exc:
                logger.warning("Failed updating conversation_metadata.json for %s: %s", cid, exc)

        # 4. Update cache/last_conversations.json
        last_conv_file = CACHE_DIR / "last_conversations.json"
        if last_conv_file.exists():
            try:
                last_convs = json.loads(last_conv_file.read_text(encoding="utf-8"))
                if isinstance(last_convs, dict):
                    last_convs[clean_path] = cid
                    last_conv_file.write_text(json.dumps(last_convs, indent=2), encoding="utf-8")
            except Exception:
                pass

        # 5. Persist mapping in app_settings.json
        try:
            settings = load_app_settings()
            sw = settings.get("session_workspaces", {})
            sw[cid] = clean_path
            settings["session_workspaces"] = sw
            save_app_settings(settings)
        except Exception:
            pass

        return True

    def _prune_conversation_from_proto_stores(self, cid: str) -> None:
        """Prunes conversation record from protobuf summaries stores (jetbox_summaries_proto.pb, agyhub_summaries_proto.pb).
        This is essential because agy CLI background reconciliation reads these proto files on startup
        and restores any deleted sessions back into conversation_summaries.db.
        """
        for filename in ("jetbox_summaries_proto.pb", "agyhub_summaries_proto.pb"):
            pb_path = GEMINI_DIR / filename
            if not pb_path.exists():
                continue
            try:
                data = pb_path.read_bytes()
                cid_bytes = cid.encode("ascii")
                if cid_bytes not in data:
                    continue

                def _rv(stream: io.BytesIO) -> Optional[int]:
                    res, shift = 0, 0
                    while True:
                        b = stream.read(1)
                        if not b:
                            return None
                        byte = b[0]
                        res |= (byte & 0x7F) << shift
                        if not (byte & 0x80):
                            return res
                        shift += 7

                def _encode_varint(val: int) -> bytes:
                    res = bytearray()
                    while val > 0x7F:
                        res.append((val & 0x7F) | 0x80)
                        val >>= 7
                    res.append(val & 0x7F)
                    return bytes(res)

                s = io.BytesIO(data)
                new_data = bytearray()
                pruned = False
                while s.tell() < len(data):
                    tag = _rv(s)
                    if tag is None:
                        break
                    wire = tag & 0x7
                    if wire == 2:
                        length = _rv(s)
                        if length is None:
                            break
                        rec_content = s.read(length)
                        # Inspect first subfield for conversation ID
                        sub = io.BytesIO(rec_content)
                        sub_tag = _rv(sub)
                        if sub_tag is not None and (sub_tag >> 3) == 1 and (sub_tag & 0x7) == 2:
                            sub_len = _rv(sub)
                            sub_cid = sub.read(sub_len).decode("ascii", "replace") if sub_len else ""
                            if sub_cid == cid or cid_bytes in rec_content:
                                pruned = True
                                continue
                        elif cid_bytes in rec_content:
                            pruned = True
                            continue

                        new_data.extend(_encode_varint(tag))
                        new_data.extend(_encode_varint(length))
                        new_data.extend(rec_content)
                    elif wire == 0:
                        val = _rv(s)
                        if val is None:
                            break
                        new_data.extend(_encode_varint(tag))
                        new_data.extend(_encode_varint(val))
                    else:
                        break

                if pruned:
                    pb_path.write_bytes(bytes(new_data))
                    logger.info("Pruned session %s from %s", cid, filename)
            except Exception as exc:
                logger.warning("Failed pruning %s from %s: %s", cid, filename, exc)

    def delete_conversation(self, conversation_id: str) -> bool:
        cid = conversation_id.strip()
        if not cid:
            return False
        try:
            # 1. Remove from SQLite conversation_summaries.db and truncate WAL journal
            if SUMMARIES_DB.exists():
                try:
                    conn = sqlite3.connect(SUMMARIES_DB, timeout=5.0)
                    with conn:
                        conn.execute(
                            "DELETE FROM conversation_summaries WHERE conversation_id = ?",
                            (cid,),
                        )
                        try:
                            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                        except Exception:
                            pass
                    conn.close()
                except Exception as exc:
                    logger.warning("Failed deleting %s from conversation_summaries.db: %s", cid, exc)

            # 2. Prune from protobuf summaries store so agy reconciliation won't resurrect it
            self._prune_conversation_from_proto_stores(cid)

            # 3. Remove conversation sqlite db
            conv_db = CONVERSATIONS_DIR / f"{cid}.db"
            for ext in ("", "-wal", "-shm"):
                p = Path(f"{conv_db}{ext}")
                if p.exists():
                    try:
                        p.unlink()
                    except Exception:
                        pass

            # 4. Remove brain directory (logs, transcripts, scratch)
            brain_path = BRAIN_DIR / cid
            if brain_path.exists() and brain_path.is_dir():
                shutil.rmtree(brain_path, ignore_errors=True)

            # 5. Remove annotations pbtxt
            pbtxt_path = ANNOTATIONS_DIR / f"{cid}.pbtxt"
            if pbtxt_path.exists():
                try:
                    pbtxt_path.unlink()
                except Exception:
                    pass

            # 6. Remove presence lock if exists
            lock_path = GEMINI_DIR / "presence" / f"{cid}.lock"
            if lock_path.exists():
                try:
                    lock_path.unlink()
                except Exception:
                    pass

            # 7. Remove from metadata cache if exists
            if CONV_METADATA_JSON.exists():
                try:
                    meta_dict = json.loads(CONV_METADATA_JSON.read_text(encoding="utf-8"))
                    if isinstance(meta_dict, dict):
                        convs = meta_dict.get("conversations", meta_dict)
                        if cid in convs:
                            del convs[cid]
                            CONV_METADATA_JSON.write_text(
                                json.dumps(meta_dict, indent=2, ensure_ascii=False), encoding="utf-8"
                            )
                except Exception:
                    pass

            # 8. Remove from cache/last_conversations.json
            last_conv_file = CACHE_DIR / "last_conversations.json"
            if last_conv_file.exists():
                try:
                    last_convs = json.loads(last_conv_file.read_text(encoding="utf-8"))
                    if isinstance(last_convs, dict):
                        filtered = {k: v for k, v in last_convs.items() if v != cid}
                        if len(filtered) != len(last_convs):
                            last_conv_file.write_text(json.dumps(filtered, indent=2), encoding="utf-8")
                except Exception:
                    pass

            # 9. Remove from app_settings.json (session_workspaces)
            try:
                settings = load_app_settings()
                sw = settings.get("session_workspaces", {})
                if cid in sw:
                    del sw[cid]
                    settings["session_workspaces"] = sw
                    save_app_settings(settings)
            except Exception:
                pass

            # 10. Prune from history.jsonl
            history_file = GEMINI_DIR / "history.jsonl"
            if history_file.exists():
                try:
                    lines = history_file.read_text(encoding="utf-8", errors="replace").splitlines()
                    new_lines = [l for l in lines if f'"{cid}"' not in l]
                    if len(new_lines) != len(lines):
                        history_file.write_text("\n".join(new_lines) + ("\n" if new_lines else ""), encoding="utf-8")
                except Exception:
                    pass

            logger.info("Deleted conversation %s permanently from all stores", cid)
            return True
        except Exception as exc:
            logger.exception("Failed to delete conversation %s: %s", cid, exc)
            return False

    def branch_conversation(
        self, orig_cid: str, branch_name: Optional[str] = None
    ) -> dict[str, Any]:
        cid = orig_cid.strip()
        new_cid = str(uuid.uuid4())

        if not SUMMARIES_DB.exists():
            raise FileNotFoundError("conversation_summaries.db not found")

        conn = sqlite3.connect(SUMMARIES_DB, timeout=5.0)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM conversation_summaries WHERE conversation_id = ?", (cid,)
        )
        orig_row = cursor.fetchone()
        row_dict = dict(orig_row) if orig_row else {}

        if not branch_name:
            base_title = self.get_conversation_meta(cid).get("title") or row_dict.get("title") or "Conversation"
            branch_name = f"[Branch] {base_title}"

        # 1. Clone conversation SQLite database using online backup
        src_db = CONVERSATIONS_DIR / f"{cid}.db"
        dst_db = CONVERSATIONS_DIR / f"{new_cid}.db"
        if src_db.exists():
            try:
                src_conn = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True, timeout=5.0)
                dst_conn = sqlite3.connect(dst_db, timeout=5.0)
                src_conn.backup(dst_conn)
                try:
                    dst_conn.execute(
                        "UPDATE trajectory_meta SET trajectory_id = ?, cascade_id = ?",
                        (new_cid, new_cid),
                    )
                    dst_conn.commit()
                except Exception:
                    pass
                dst_conn.close()
                src_conn.close()
            except Exception as exc:
                logger.warning("Could not backup conversation db for branch: %s", exc)

        # 2. Clone brain directory (logs, transcripts, scratch)
        src_brain = BRAIN_DIR / cid
        dst_brain = BRAIN_DIR / new_cid
        if src_brain.exists() and src_brain.is_dir():
            try:
                shutil.copytree(src_brain, dst_brain, symlinks=True, ignore_dangling_symlinks=True)
            except Exception as exc:
                logger.warning("Could not copy brain directory for branch: %s", exc)

        # 3. Insert record into conversation_summaries
        with conn:
            cursor.execute(
                """
                INSERT INTO conversation_summaries (
                    conversation_id, title, preview, step_count, last_modified_time,
                    workspace_uris, status, source, project_id, agent_name,
                    parent_conversation_id, nesting_depth, last_user_input_time,
                    last_user_input_step_index, app_data_dir, group_id
                ) VALUES (?, ?, ?, ?, datetime('now'), ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?, ?, ?)
                """,
                (
                    new_cid,
                    branch_name,
                    row_dict.get("preview", ""),
                    row_dict.get("step_count", 0),
                    row_dict.get("workspace_uris", str(HOME_DIR)),
                    row_dict.get("status", "CASCADE_RUN_STATUS_IDLE"),
                    row_dict.get("source", ""),
                    row_dict.get("project_id", ""),
                    row_dict.get("agent_name", "antigravity"),
                    cid,
                    (row_dict.get("nesting_depth") or 0) + 1,
                    row_dict.get("last_user_input_step_index", -1),
                    row_dict.get("app_data_dir", ""),
                    row_dict.get("group_id", ""),
                ),
            )
        conn.close()
        logger.info("Branched conversation %s -> %s ('%s')", cid, new_cid, branch_name)
        return {"conversation_id": new_cid, "title": branch_name}

    def rewind_conversation(
        self, conversation_id: str, step_index: Optional[int], prompt_text: str = ""
    ) -> str:
        cid = conversation_id.strip()
        target_step = step_index
        target_prompt = prompt_text

        brain_path = BRAIN_DIR / cid
        t_jsonl = brain_path / ".system_generated" / "logs" / "transcript.jsonl"
        t_full = brain_path / ".system_generated" / "logs" / "transcript_full.jsonl"

        # If step_index wasn't explicitly provided, find it from transcript by prompt_text
        clean_search = self._clean_user_prompt(prompt_text).strip()
        if target_step is None and clean_search:
            if t_jsonl.exists():
                try:
                    raw_lines = t_jsonl.read_text(encoding="utf-8", errors="replace").splitlines()
                    # 1. Reverse search for exact match
                    for line in reversed(raw_lines):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            ev = json.loads(line)
                            if ev.get("type") == "USER_INPUT":
                                c_text = self._clean_user_prompt(self._extract_text(ev)).strip()
                                if c_text == clean_search:
                                    target_step = ev.get("step_index")
                                    target_prompt = c_text
                                    break
                        except Exception:
                            pass
                    # 2. Reverse search for substring match if exact not found
                    if target_step is None:
                        for line in reversed(raw_lines):
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                ev = json.loads(line)
                                if ev.get("type") == "USER_INPUT":
                                    c_text = self._clean_user_prompt(self._extract_text(ev)).strip()
                                    if clean_search in c_text or c_text in clean_search:
                                        target_step = ev.get("step_index")
                                        target_prompt = c_text
                                        break
                            except Exception:
                                pass
                except Exception as exc:
                    logger.warning("Error searching transcript for rewind: %s", exc)

            # Fallback to SQLite DB if not found in transcript.jsonl
            if target_step is None:
                conv_db = CONVERSATIONS_DIR / f"{cid}.db"
                if conv_db.exists():
                    try:
                        conn = sqlite3.connect(conv_db, timeout=5.0)
                        c = conn.cursor()
                        c.execute("SELECT idx, payload FROM steps ORDER BY idx DESC")
                        for s_idx, payload in c.fetchall():
                            if isinstance(payload, str) and payload.strip().startswith("{"):
                                try:
                                    obj = json.loads(payload)
                                    if obj.get("type") == "USER_INPUT" or "USER" in str(obj.get("role", "")).upper():
                                        c_text = self._clean_user_prompt(self._extract_text(obj)).strip()
                                        if c_text == clean_search or clean_search in c_text or c_text in clean_search:
                                            target_step = s_idx
                                            target_prompt = c_text
                                            break
                                except Exception:
                                    pass
                        conn.close()
                    except Exception as exc:
                        logger.warning("Error searching SQLite db for rewind: %s", exc)

            # If clean_search was provided but not found anywhere (e.g. prompt was aborted
            # before agy engine persisted it), the persistent transcript is already clean.
            # Preceding prompts MUST NOT be truncated! Return the requested prompt to editor.
            if target_step is None:
                target_prompt = clean_search

        # Only if NEITHER step_index nor prompt_text was provided (bare rewind),
        # rewind to the last recorded USER_INPUT in transcript
        elif target_step is None and not clean_search and t_jsonl.exists():
            try:
                raw_lines = t_jsonl.read_text(encoding="utf-8", errors="replace").splitlines()
                for line in reversed(raw_lines):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                        if ev.get("type") == "USER_INPUT":
                            target_step = ev.get("step_index")
                            target_prompt = self._clean_user_prompt(self._extract_text(ev)).strip()
                            break
                    except Exception:
                        pass
            except Exception as exc:
                logger.warning("Error reading transcript for bare rewind: %s", exc)

        if target_step is not None:
            # 1. Truncate JSONL files
            for path in (t_jsonl, t_full):
                if path.exists():
                    kept_lines = []
                    with path.open("r", encoding="utf-8", errors="replace") as f:
                        for line in f:
                            if not line.strip():
                                continue
                            try:
                                ev = json.loads(line)
                                s_idx = ev.get("step_index")
                                if s_idx is not None and s_idx >= target_step:
                                    if not target_prompt and ev.get("type") == "USER_INPUT":
                                        target_prompt = self._clean_user_prompt(self._extract_text(ev)).strip()
                                    continue
                                kept_lines.append(line)
                            except Exception:
                                kept_lines.append(line)
                    path.write_text("".join(kept_lines), encoding="utf-8")

            # 2. Truncate SQLite conversation database
            conv_db = CONVERSATIONS_DIR / f"{cid}.db"
            remaining_steps = 0
            if conv_db.exists():
                try:
                    conn = sqlite3.connect(conv_db, timeout=5.0)
                    with conn:
                        for tbl in ("steps", "gen_metadata", "executor_metadata", "parent_references", "battle_mode_infos"):
                            try:
                                conn.execute(f"DELETE FROM {tbl} WHERE idx >= ?", (target_step,))
                            except Exception:
                                pass
                    c = conn.cursor()
                    try:
                        c.execute("SELECT COUNT(*) FROM steps")
                        remaining_steps = c.fetchone()[0]
                    except Exception:
                        pass
                    conn.close()
                except Exception as exc:
                    logger.warning("Could not truncate conversation db during rewind: %s", exc)

            # 3. Update conversation_summaries.db
            if SUMMARIES_DB.exists():
                try:
                    conn = sqlite3.connect(SUMMARIES_DB, timeout=5.0)
                    with conn:
                        conn.execute(
                            "UPDATE conversation_summaries SET step_count = ?, last_modified_time = datetime('now') WHERE conversation_id = ?",
                            (remaining_steps, cid),
                        )
                    conn.close()
                except Exception as exc:
                    logger.warning("Could not update summaries during rewind: %s", exc)

        clean_ret = self._clean_user_prompt(target_prompt)
        return clean_ret if clean_ret else target_prompt

    def load_transcript(self, conversation_id: str) -> list[dict[str, Any]]:
        """
        Loads the full conversation history from transcript.jsonl, with automatic
        fallback to conversations/<conversation_id>.db if transcript.jsonl is missing.
        """
        transcript_path = (
            BRAIN_DIR
            / conversation_id
            / ".system_generated"
            / "logs"
            / "transcript.jsonl"
        )
        messages: list[dict[str, Any]] = []

        if transcript_path.exists():
            try:
                messages = self._parse_transcript_jsonl(transcript_path)
                if messages:
                    return messages
            except Exception as exc:
                logger.warning("Failed parsing transcript.jsonl for %s: %s", conversation_id, exc)

        # Fallback: inspect ~/.gemini/antigravity-cli/conversations/<id>.db
        conv_db_path = CONVERSATIONS_DIR / f"{conversation_id}.db"
        if conv_db_path.exists():
            try:
                messages = self._parse_conversation_db(conv_db_path)
            except Exception as exc:
                logger.warning("Fallback DB parse failed for %s: %s", conversation_id, exc)

        return messages

    def _parse_transcript_jsonl(self, path: Path) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        current_assistant: Optional[dict[str, Any]] = None

        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line_num, raw_line in enumerate(f, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                etype = (
                    event.get("type")
                    or event.get("step_type")
                    or event.get("role")
                    or ""
                ).upper()

                timestamp = (
                    event.get("timestamp")
                    or event.get("created_at")
                    or event.get("time")
                    or ""
                )

                # 1. User Input Step
                if etype in ("USER_INPUT", "USER", "HUMAN") or event.get("role") == "user":
                    if current_assistant:
                        messages.append(current_assistant)
                        current_assistant = None

                    text = self._extract_text(event)
                    if text:
                        text = self._clean_user_prompt(text)
                    if text:
                        messages.append(
                            {
                                "id": event.get("id") or f"u-{line_num}",
                                "role": "user",
                                "content": text,
                                "timestamp": timestamp,
                                "step_index": event.get("step_index"),
                            }
                        )
                    continue

                # Ensure an active assistant turn accumulator exists
                if current_assistant is None:
                    current_assistant = {
                        "id": event.get("id") or f"a-{line_num}",
                        "role": "assistant",
                        "content": "",
                        "thoughts": "",
                        "tools": [],
                        "timestamp": timestamp,
                        "usage": event.get("usage") or {},
                        "step_index": event.get("step_index"),
                    }

                # 2. Thinking / Reasoning Step
                if (
                    etype in ("THINKING", "THOUGHT", "REASONING")
                    or "thinking" in event
                    or "thought" in event
                ):
                    thought_text = (
                        event.get("thinking")
                        or event.get("thought")
                        or self._extract_text(event)
                    )
                    if isinstance(thought_text, str) and thought_text.strip():
                        if current_assistant["thoughts"]:
                            current_assistant["thoughts"] += "\n\n"
                        current_assistant["thoughts"] += thought_text.strip()
                    if etype in ("THINKING", "THOUGHT", "REASONING"):
                        continue

                # 3. Tool Calls Step
                if (
                    etype in ("TOOL_CALL", "TOOL_CALLS", "TOOL_USE", "TOOL_RESULT")
                    or "tool_calls" in event
                    or "tool_use" in event
                ):
                    raw_tools = (
                        event.get("tool_calls")
                        or event.get("tool_use")
                        or ([event] if etype in ("TOOL_CALL", "TOOL_USE") else [])
                    )
                    if isinstance(raw_tools, dict):
                        raw_tools = [raw_tools]
                    if isinstance(raw_tools, list):
                        for t in raw_tools:
                            if not isinstance(t, dict):
                                continue
                            t_name = (
                                t.get("name")
                                or t.get("tool_name")
                                or (t.get("function") or {}).get("name")
                                or "tool"
                            )
                            t_args = (
                                t.get("args")
                                or t.get("arguments")
                                or t.get("input")
                                or (t.get("function") or {}).get("arguments")
                                or {}
                            )
                            t_output = (
                                t.get("output")
                                or t.get("result")
                                or event.get("output")
                                or ""
                            )
                            current_assistant["tools"].append(
                                {
                                    "tool_id": t.get("id") or f"tool-{line_num}",
                                    "name": t_name,
                                    "args": t_args,
                                    "status": "completed",
                                    "output": str(t_output)[:4000] if t_output else "",
                                }
                            )
                    if etype in ("TOOL_CALL", "TOOL_CALLS", "TOOL_USE", "TOOL_RESULT"):
                        continue

                # 4. Planner / Assistant Response Step
                if etype in ("PLANNER_RESPONSE", "ASSISTANT", "MODEL", "TEXT", "RESPONSE", ""):
                    text = self._extract_text(event)
                    if text:
                        if current_assistant["content"]:
                            current_assistant["content"] += "\n\n"
                        current_assistant["content"] += text
                    if "usage" in event and isinstance(event["usage"], dict):
                        current_assistant["usage"] = event["usage"]
                    elif "input_tokens" in event or "output_tokens" in event:
                        in_tok = event.get("input_tokens", 0) + event.get("cache_read_tokens", 0)
                        out_tok = event.get("output_tokens", 0)
                        think_tok = event.get("thinking_tokens", 0)
                        current_assistant["usage"] = {
                            "input_tokens": in_tok,
                            "output_tokens": out_tok,
                            "thinking_tokens": think_tok,
                        }

        if current_assistant and (
            current_assistant["content"]
            or current_assistant["thoughts"]
            or current_assistant["tools"]
        ):
            messages.append(current_assistant)

        return messages

    def _parse_conversation_db(self, db_path: Path) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='steps'")
        if not cursor.fetchone():
            conn.close()
            return messages

        cursor.execute("SELECT * FROM steps ORDER BY rowid ASC LIMIT 500")
        rows = cursor.fetchall()
        for idx, row in enumerate(rows):
            row_dict = dict(row)
            # Inspect any JSON blob or text columns inside the steps table
            raw_payload = (
                row_dict.get("step_data")
                or row_dict.get("data")
                or row_dict.get("payload")
                or row_dict.get("content")
                or ""
            )
            if isinstance(raw_payload, bytes):
                raw_payload = raw_payload.decode("utf-8", errors="replace")

            if isinstance(raw_payload, str) and raw_payload.strip().startswith("{"):
                try:
                    obj = json.loads(raw_payload)
                    text = self._extract_text(obj)
                    role_hint = str(
                        obj.get("type") or obj.get("role") or row_dict.get("step_type") or ""
                    ).upper()
                    role = "user" if "USER" in role_hint else "assistant"
                    if text:
                        if role == "user":
                            text = self._clean_user_prompt(text)
                        if text:
                            messages.append(
                                {
                                    "id": f"db-{idx}",
                                    "role": role,
                                    "content": text,
                                    "thoughts": obj.get("thinking") or "",
                                    "tools": [],
                                    "timestamp": str(row_dict.get("created_at") or ""),
                                    "step_index": row_dict.get("idx"),
                                }
                            )
                except Exception:
                    pass
        conn.close()
        return messages

    @staticmethod
    def _clean_user_prompt(text: str) -> str:
        if not isinstance(text, str):
            return ""
        # 1. Strip context summary blocks if present (from compacted transcripts)
        text = re.sub(r"<CONTEXT_SUMMARY>.*?</CONTEXT_SUMMARY>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()

        # 2. Extract content within <USER_REQUEST> ... </USER_REQUEST> if present
        m = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", text, re.DOTALL | re.IGNORECASE)
        if m:
            text = m.group(1).strip()

        # 3. Strip any stray user request or system / metadata / settings tags
        text = re.sub(r"</?USER_REQUEST>", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"<ADDITIONAL_METADATA>.*?</ADDITIONAL_METADATA>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
        text = re.sub(r"<USER_SETTINGS_CHANGE>.*?</USER_SETTINGS_CHANGE>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
        text = re.sub(r"<SYSTEM_MESSAGE>.*?</SYSTEM_MESSAGE>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
        return text.strip()

    @staticmethod
    def _extract_text(obj: dict[str, Any]) -> str:
        # Direct string fields
        for key in ("text", "response", "content", "user_input", "planner_response", "message"):
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                return val
            if isinstance(val, dict):
                nested = AntigravityStorageEngine._extract_text(val)
                if nested:
                    return nested
            if isinstance(val, list):
                parts: list[str] = []
                for item in val:
                    if isinstance(item, str):
                        parts.append(item)
                    elif isinstance(item, dict):
                        if item.get("type") in ("text", "output_text", None) and isinstance(
                            item.get("text"), str
                        ):
                            parts.append(item["text"])
                        elif isinstance(item.get("content"), str):
                            parts.append(item["content"])
                if parts:
                    return "\n".join(parts)
        return ""


def get_git_branch(workspace_dir: str) -> Optional[str]:
    try:
        ws = Path(workspace_dir)
        git_dir = ws / ".git"
        if git_dir.is_file():
            content = git_dir.read_text(encoding="utf-8").strip()
            if content.startswith("gitdir:"):
                target = content.split(":", 1)[1].strip()
                git_dir = (ws / target).resolve()
        if git_dir.is_dir():
            head_file = git_dir / "HEAD"
            if head_file.exists():
                head_content = head_file.read_text(encoding="utf-8").strip()
                if head_content.startswith("ref: refs/heads/"):
                    return head_content.replace("ref: refs/heads/", "").strip()
                elif len(head_content) >= 7:
                    return head_content[:7]
    except Exception:
        pass
    return None


def get_default_workspace() -> str:
    settings = load_app_settings()
    saved_ws = settings.get("default_workspace")
    if saved_ws and Path(saved_ws).is_dir():
        return str(Path(saved_ws).resolve())
    env_ws = os.environ.get("AGY_WORKSPACE")
    if env_ws and Path(env_ws).is_dir():
        return str(Path(env_ws).resolve())
    docs = Path("/storage/emulated/0/Documents")
    if docs.is_dir():
        return str(docs)
    home_ws = HOME_DIR / "workspace"
    if home_ws.is_dir():
        return str(home_ws)
    return str(HOME_DIR)


# ---------------------------------------------------------------------------
# Antigravity Subprocess & Stream-JSON Orchestrator
# ---------------------------------------------------------------------------

@dataclass
class SessionConfig:
    conversation_id: Optional[str] = None
    model: str = "gemini-3.8-flash-high"
    mode: str = "default"
    effort: str = "high"
    workspace: str = field(default_factory=get_default_workspace)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_thinking_tokens: int = 0
    turn_count: int = 0


class AgyProcessOrchestrator:
    """
    Manages the lifecycle of the `agy` CLI subprocess, supporting both:
      1. Persistent bidirectional `--input-format stream-json --output-format stream-json`
      2. Turn-scoped streaming `--output-format stream-json -p <prompt>`
    with automatic fallback, real-time NDJSON event demultiplexing, and SIGINT abort.
    """

    def __init__(self, storage: AntigravityStorageEngine, broadcast_cb):
        self.storage = storage
        self.broadcast = broadcast_cb
        self.config = SessionConfig()
        self.active_proc: Optional[asyncio.subprocess.Process] = None
        self.is_running: bool = False
        self._interrupted: bool = False
        self._current_turn_id: Optional[str] = None
        self._turn_lock = asyncio.Lock()
        self._streamed_turn_ids: set[str] = set()

        # In-app Google Authentication lifecycle state
        self.is_authenticating: bool = False
        self.auth_proc: Optional[asyncio.subprocess.Process] = None
        self.auth_master: Optional[int] = None
        self.auth_turn_id: Optional[str] = None
        self.auth_waiter_task: Optional[asyncio.Task] = None
        self.on_auth_success_cb: Optional[Any] = None
        self._pending_auth_prompt: Optional[str] = None
        self._pending_auth_attachments: list[dict[str, Any]] = []
        self._auth_required_detected: bool = False

    def build_cli_args(
        self,
        prompt: Optional[str] = None,
        use_persistent_stdio: bool = False,
    ) -> list[str]:
        args = [
            str(AGY_BIN),
            "--dangerously-skip-permissions",
            "--output-format",
            "stream-json",
            "--model",
            self.config.model,
        ]
        # Only pass --effort for models that explicitly support reasoning effort (Gemini)
        if "gemini" in self.config.model.lower() and self.config.effort in ("low", "medium", "high"):
            args.extend(["--effort", self.config.effort])

        # Only pass --mode when a specific non-default mode is selected (accept-edits or plan).
        # Note: --mode auto triggers 'warning: --mode auto is not available for this account'
        # in standard agy accounts. Omitting --mode runs in the native default mode with full tool permissions.
        if self.config.mode and self.config.mode not in ("default", "standard", "auto"):
            args.extend(["--mode", self.config.mode])

        if use_persistent_stdio:
            args.extend(["--input-format", "stream-json"])

        if self.config.conversation_id:
            args.extend(["--conversation", self.config.conversation_id])

        if prompt is not None and not use_persistent_stdio:
            if prompt.strip().startswith("/"):
                args.append("--disable-slash-commands")
            args.extend(["-p", prompt])

        return args

    async def execute_turn(
        self,
        prompt: str,
        attachments: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        if self._turn_lock.locked():
            await self.broadcast(
                {
                    "type": "system_card",
                    "title": "Busy",
                    "markdown": "Antigravity is currently processing a turn. Tap **Stop** to interrupt it first.",
                    "level": "warning",
                }
            )
            return

        async with self._turn_lock:
            turn_id = f"turn-{uuid.uuid4().hex[:8]}"
            self._current_turn_id = turn_id
            self._interrupted = False
            self._auth_required_detected = False
            self.is_running = True

            await self.broadcast(
                {
                    "type": "status",
                    "state": "running",
                    "detail": f"Running ({self.config.model})",
                }
            )
            await self.broadcast(
                {
                    "type": "turn_start",
                    "turn_id": turn_id,
                    "conversation_id": self.config.conversation_id,
                    "model": self.config.model,
                }
            )

            usage_summary = {
                "input_tokens": 0,
                "output_tokens": 0,
                "thinking_tokens": 0,
            }
            emitted_any_content = False

            try:
                if not AGY_BIN.exists():
                    raise FileNotFoundError(
                        f"Antigravity CLI binary not found at {AGY_BIN}. Ensure `agy` is installed in Termux."
                    )

                token_file = GEMINI_DIR / "antigravity-oauth-token"
                if not token_file.exists():
                    logger.info("No token file found. Proactively initiating Google login...")
                    await self.trigger_in_app_login(turn_id, original_prompt=prompt, original_attachments=attachments)
                    return

                # Spawn `agy` with `--output-format stream-json --dangerously-skip-permissions`
                cmd = self.build_cli_args(prompt=prompt, use_persistent_stdio=False)
                logger.info("Spawning agy: %s", " ".join(cmd[:8]) + " ...")

                proc_env = dict(os.environ)
                proc_env["AGY_AUTO_UPDATE"] = "1"
                proc_env["GIT_SSH_COMMAND"] = f"{USR_DIR}/bin/ssh -F {SSH_DIR}/config"
                proc_env["AGY_WEB_PORT"] = str(PORT)
                active_api_keys = get_active_api_keys()
                for ak_k, ak_v in active_api_keys.items():
                    if ak_k not in PROTECTED_ENV_NAMES:
                        proc_env[ak_k] = ak_v

                self.active_proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=self.config.workspace if Path(self.config.workspace).is_dir() else str(HOME_DIR),
                    env=proc_env,
                    limit=16 * 1024 * 1024,  # 16 MiB line buffer for large tool outputs
                )

                # Read stdout and stderr concurrently
                stdout_task = asyncio.create_task(
                    self._consume_stdout_stream(turn_id, usage_summary)
                )
                stderr_task = asyncio.create_task(self._consume_stderr_stream(turn_id))

                emitted_any_content = await stdout_task
                stderr_text = await stderr_task
                return_code = await self.active_proc.wait()

                was_interrupted = self._interrupted or return_code in (
                    -signal.SIGINT,
                    -signal.SIGTERM,
                    130,  # 128 + SIGINT
                    143,  # 128 + SIGTERM
                )

                # Check if auth required was detected during streaming or in stderr
                if not self._auth_required_detected and not was_interrupted:
                    combined_err = (stderr_text or "").lower()
                    if (
                        "authentication required" in combined_err
                        or "accounts.google.com/o/oauth2/auth" in combined_err
                        or "you are not logged into antigravity" in combined_err
                        or "error getting token source" in combined_err
                        or "authentication failed or timed out" in combined_err
                        or "please log in" in combined_err
                        or "please visit the url to log in" in combined_err
                    ):
                        self._auth_required_detected = True

                if self._auth_required_detected and not was_interrupted:
                    logger.info("Session expired or authentication required. Triggering in-app login...")
                    token_file = GEMINI_DIR / "antigravity-oauth-token"
                    if token_file.exists():
                        try:
                            token_file.unlink()
                        except Exception:
                            pass
                    await self.trigger_in_app_login(turn_id, original_prompt=prompt, original_attachments=attachments)
                    return

                if was_interrupted:
                    status_str = "INTERRUPTED"
                elif return_code != 0 and not emitted_any_content and stderr_text.strip():
                    cleaned_err = "\n".join(
                        line for line in stderr_text.strip().splitlines()
                        if not line.lower().startswith("warning:")
                    ).strip()
                    if cleaned_err:
                        await self.broadcast(
                            {
                                "type": "agent_token",
                                "turn_id": turn_id,
                                "delta": f"\n> **CLI Status ({return_code}):**\n```\n{cleaned_err[:2000]}\n```\n",
                            }
                        )
                        status_str = "ERROR"
                    else:
                        status_str = "SUCCESS"
                else:
                    status_str = "SUCCESS"

                # If conversation_id was newly created, discover it from SQLite summaries
                if not self.config.conversation_id:
                    latest = self.storage.list_conversations(limit=1)
                    if latest:
                        self.config.conversation_id = latest[0]["conversation_id"]

                # Accumulate session telemetry
                self.config.total_input_tokens += usage_summary["input_tokens"]
                self.config.total_output_tokens += usage_summary["output_tokens"]
                self.config.total_thinking_tokens += usage_summary["thinking_tokens"]
                self.config.turn_count += 1

                await self.broadcast(
                    {
                        "type": "turn_complete",
                        "turn_id": turn_id,
                        "conversation_id": self.config.conversation_id,
                        "usage": usage_summary,
                        "totals": {
                            "input_tokens": self.config.total_input_tokens,
                            "output_tokens": self.config.total_output_tokens,
                            "thinking_tokens": self.config.total_thinking_tokens,
                            "turns": self.config.turn_count,
                        },
                        "status": status_str,
                    }
                )

                # Push updated session drawer list
                await self.broadcast(
                    {
                        "type": "session_list",
                        "sessions": self.storage.list_conversations(limit=80),
                    }
                )

            except asyncio.CancelledError:
                await self.broadcast(
                    {
                        "type": "turn_complete",
                        "turn_id": turn_id,
                        "conversation_id": self.config.conversation_id,
                        "usage": usage_summary,
                        "status": "INTERRUPTED",
                    }
                )
            except Exception as exc:
                logger.exception("Error during agy turn execution")
                await self.broadcast(
                    {
                        "type": "agent_token",
                        "turn_id": turn_id,
                        "delta": f"\n\n**Execution Error:** `{exc}`",
                    }
                )
                await self.broadcast(
                    {
                        "type": "turn_complete",
                        "turn_id": turn_id,
                        "conversation_id": self.config.conversation_id,
                        "usage": usage_summary,
                        "status": "ERROR",
                    }
                )
            finally:
                self._streamed_turn_ids.discard(turn_id)
                self.active_proc = None
                self.is_running = False
                self._current_turn_id = None
                if not self.is_authenticating:
                    await self.broadcast(
                        {"type": "status", "state": "idle", "detail": "Ready"}
                    )

    def _cleanup_auth(self) -> None:
        self.is_authenticating = False
        if self.auth_master is not None:
            try:
                os.close(self.auth_master)
            except Exception:
                pass
            self.auth_master = None
        if self.auth_proc:
            try:
                if self.auth_proc.returncode is None:
                    self.auth_proc.terminate()
            except Exception:
                pass
            self.auth_proc = None
        if self.auth_waiter_task and not self.auth_waiter_task.done():
            self.auth_waiter_task.cancel()
        self.auth_waiter_task = None

    def _sync_oauth_token(self) -> None:
        token_src = HOME_DIR / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
        if token_src.exists():
            backup_candidates = [
                Path("/storage/emulated/0/Documents/AgyStudio/antigravity-oauth-token"),
                Path("/storage/emulated/0/Documents/antigravity-oauth-token"),
            ]
            for bp in backup_candidates:
                try:
                    bp.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(token_src, bp)
                    logger.info("Synced oauth token to %s", bp)
                except Exception as exc:
                    logger.debug("Could not sync oauth token to %s: %s", bp, exc)

    async def _on_auth_success(self) -> None:
        turn_id = self.auth_turn_id or "auth"
        pending_prompt = self._pending_auth_prompt
        pending_attachments = self._pending_auth_attachments
        self._pending_auth_prompt = None
        self._pending_auth_attachments = []

        self._sync_oauth_token()
        self._cleanup_auth()
        await self.broadcast(
            {
                "type": "agent_token",
                "turn_id": turn_id,
                "delta": "\n\n**Google Sign-In Successful!** Authentication credentials saved. You are ready to use Antigravity Studio.\n",
            }
        )
        await self.broadcast(
            {
                "type": "turn_complete",
                "turn_id": turn_id,
                "metrics": {},
            }
        )
        await self.broadcast({"type": "auth_success"})
        await self.broadcast({"type": "status", "state": "idle", "detail": "Ready"})
        if self.on_auth_success_cb:
            try:
                cb_res = self.on_auth_success_cb()
                if asyncio.iscoroutine(cb_res):
                    await cb_res
            except Exception as exc:
                logger.warning("Error in on_auth_success_cb: %s", exc)

        if pending_prompt:
            logger.info("Resuming pending prompt after successful authentication: %s", pending_prompt[:60])
            await asyncio.sleep(0.5)
            asyncio.create_task(
                self.execute_turn(
                    pending_prompt,
                    attachments=pending_attachments,
                )
            )

    async def submit_auth_code(self, code: str) -> bool:
        clean_code = code.strip().strip("'\"`")
        if "code=" in clean_code:
            m = re.search(r"[?&]code=([^&\s]+)", clean_code)
            if m:
                import urllib.parse
                clean_code = urllib.parse.unquote(m.group(1))

        if not clean_code:
            return False

        if not self.is_authenticating or not self.auth_proc or self.auth_master is None:
            await self.broadcast(
                {
                    "type": "agent_token",
                    "turn_id": self._current_turn_id or "auth",
                    "delta": "\n**Notice:** No active authentication session waiting for a code. Run `/login` to sign in.\n",
                }
            )
            return False

        turn_id = self.auth_turn_id or self._current_turn_id or "auth"
        await self.broadcast(
            {
                "type": "agent_token",
                "turn_id": turn_id,
                "delta": "\n> ⏳ **Submitting authorization code to Antigravity CLI...**\n",
            }
        )

        token_file = GEMINI_DIR / "antigravity-oauth-token"
        initial_mtime = token_file.stat().st_mtime if token_file.exists() else 0

        try:
            # Write code + CRLF (\r\n) to master PTY (essential for terminal raw mode)
            os.write(self.auth_master, (clean_code + "\r\n").encode("utf-8"))

            success = False
            output_tail = ""
            start_poll = time.time()
            while time.time() - start_poll < 20.0:
                await asyncio.sleep(0.3)

                # 1. Check if token file was created or updated with valid JSON content
                if token_file.exists():
                    try:
                        st = token_file.stat()
                        if st.st_size > 50 and (st.st_mtime > initial_mtime or initial_mtime == 0):
                            try:
                                token_data = json.loads(token_file.read_text())
                                if "token" in token_data or "access_token" in token_data:
                                    success = True
                                    logger.info("Found newly saved oauth token file (%d bytes)", st.st_size)
                                    break
                            except Exception:
                                pass
                    except Exception:
                        pass

                # 2. Read output from PTY master
                if self.auth_master is not None:
                    try:
                        r, _, _ = select.select([self.auth_master], [], [], 0.05)
                        if r:
                            chunk = os.read(self.auth_master, 4096).decode("latin1", "ignore")
                            output_tail += chunk
                            lowered = output_tail.lower()
                            if any(w in lowered for w in ["logged in", "welcome", "authenticated"]):
                                success = True
                                logger.info("Detected success keyword in CLI output: %s", chunk[:100])
                                break
                            if any(w in lowered for w in ["invalid_grant", "invalid authorization code", "error:", "bad request"]):
                                logger.warning("Detected error keyword in CLI output: %s", output_tail[-200:])
                                break
                    except Exception:
                        pass

                # 3. Check if proc exited
                if self.auth_proc and self.auth_proc.returncode is not None:
                    if self.auth_proc.returncode == 0:
                        success = True
                        break
                    else:
                        logger.warning("Auth process exited with rc=%s", self.auth_proc.returncode)
                        if token_file.exists() and token_file.stat().st_size > 50:
                            success = True
                        break

            if success:
                await self._on_auth_success()
                return True
            else:
                logger.error("Authentication failed: token not saved. PTY output: %s", output_tail[-300:])
                err_hint = ""
                lowered_tail = output_tail.lower()
                if "invalid_grant" in lowered_tail or "invalid" in lowered_tail:
                    err_hint = " Google rejected the code (it may have expired or already been used)."
                elif "bad request" in lowered_tail:
                    err_hint = " The code format was invalid."
                await self.broadcast(
                    {
                        "type": "agent_token",
                        "turn_id": turn_id,
                        "delta": f"\n\n**Sign-In Failed.**{err_hint} Please close any extra browser tabs and type `/login` in chat to obtain a fresh sign-in link.\n",
                    }
                )
                await self.broadcast(
                    {
                        "type": "turn_complete",
                        "turn_id": turn_id,
                        "metrics": {},
                    }
                )
                await self.broadcast({"type": "auth_failed"})
                self._pending_auth_prompt = None
                self._pending_auth_attachments = []
                self._cleanup_auth()
                return False
        except Exception as exc:
            logger.exception("Error writing auth code to PTY: %s", exc)
            self._pending_auth_prompt = None
            self._pending_auth_attachments = []
            await self.broadcast(
                {
                    "type": "agent_token",
                    "turn_id": turn_id,
                    "delta": f"\n\n**Authentication Error:** `{exc}`\n",
                }
            )
            await self.broadcast(
                {
                    "type": "turn_complete",
                    "turn_id": turn_id,
                    "metrics": {},
                }
            )
            self._cleanup_auth()
            return False

    async def trigger_in_app_login(
        self,
        turn_id: str,
        original_prompt: Optional[str] = None,
        original_attachments: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        """
        Interactively spawns `agy` in a pseudo-terminal (PTY) to generate the Google OAuth consent URL,
        broadcasts it to the WebView / Chrome Custom Tabs, and awaits user authorization code submission.
        """
        import fcntl
        import pty
        import select
        import struct
        import termios

        # Clean up any existing auth session
        self._cleanup_auth()

        self._pending_auth_prompt = original_prompt
        self._pending_auth_attachments = original_attachments or []

        # Remove stale token file if starting a fresh sign-in session
        token_file = GEMINI_DIR / "antigravity-oauth-token"
        if token_file.exists():
            try:
                token_file.unlink()
            except Exception:
                pass

        logger.info("Initiating in-app Google OAuth consent flow...")
        await self.broadcast(
            {
                "type": "agent_token",
                "turn_id": turn_id,
                "delta": "\n> **Google Sign-In Required:** Initializing authentication session...\n",
            }
        )

        try:
            master, slave = pty.openpty()
            # Crucial: set terminal window size to 100 rows x 1000 cols to prevent agy from paging or wrapping URLs
            try:
                winsize = struct.pack("HHHH", 100, 1000, 0, 0)
                fcntl.ioctl(slave, termios.TIOCSWINSZ, winsize)
                fcntl.ioctl(master, termios.TIOCSWINSZ, winsize)
            except Exception:
                pass

            env = dict(os.environ)
            env["TERM"] = "xterm-256color"

            proc = await asyncio.create_subprocess_exec(
                os.environ.get("AGY_BIN", str(AGY_BIN)),
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=env,
                close_fds=True,
            )
            os.close(slave)

            # Wait for menu to appear before selecting option 1
            output = ""
            start_wait = time.time()
            while time.time() - start_wait < 6.0:
                if proc.returncode is not None:
                    break
                await asyncio.sleep(0.15)
                r, _, _ = select.select([master], [], [], 0.1)
                if r:
                    try:
                        c = os.read(master, 1024).decode("latin1", "ignore")
                        output += c
                        if "Select login method" in output or "Google OAuth" in output:
                            break
                    except Exception:
                        break

            if proc.returncode is not None:
                raise RuntimeError(
                    f"Antigravity CLI process terminated prematurely (exit code {proc.returncode}). Output: {output.strip() or 'None'}"
                )

            await asyncio.sleep(0.25)
            # Send Enter (\r\n) to select "1. Google OAuth"
            try:
                os.write(master, b"\r\n")
            except Exception as write_err:
                raise RuntimeError(f"Failed communicating with CLI login pseudo-terminal: {write_err}")

            auth_url = None
            start_time = time.time()
            while time.time() - start_time < 10.0:
                if proc.returncode is not None:
                    break
                await asyncio.sleep(0.2)
                r, _, _ = select.select([master], [], [], 0.1)
                if r:
                    try:
                        chunk = os.read(master, 2048).decode("latin1", "ignore")
                        output += chunk
                        m = re.search(
                            r'(https://accounts\.google\.com/o/oauth2/auth\?[a-zA-Z0-9_\-\.%&=+]+)',
                            output
                        )
                        if m:
                            auth_url = m.group(1)
                            if "authorization code" in output.lower():
                                break
                    except Exception:
                        break

            if auth_url:
                self.is_authenticating = True
                self.auth_proc = proc
                self.auth_master = master
                self.auth_turn_id = turn_id

                logger.info("Captured OAuth URL: %s...", auth_url[:60])
                await self.broadcast({"type": "open_external_url", "url": auth_url})
                await self.broadcast(
                    {
                        "type": "auth_required",
                        "url": auth_url,
                        "turn_id": turn_id,
                    }
                )
                await self.broadcast(
                    {
                        "type": "agent_token",
                        "turn_id": turn_id,
                        "delta": (
                            f"\n[**Click Here to Sign In with Google**]({auth_url})\n\n"
                            "**Steps to complete sign-in:**\n"
                            "1. Tap the link above to sign in with Google in your browser.\n"
                            "2. Copy the authorization code Google gives you.\n"
                            "3. **Paste the code directly into the chat prompt below and send it.**\n"
                        ),
                    }
                )

                async def _monitor_proc():
                    try:
                        rc = await proc.wait()
                        if rc == 0:
                            await self._on_auth_success()
                        elif self.is_authenticating:
                            logger.info("Auth process exited with rc=%s", rc)
                    except asyncio.CancelledError:
                        pass
                    except Exception as exc:
                        logger.warning("Auth monitor error: %s", exc)

                self.auth_waiter_task = asyncio.create_task(_monitor_proc())
            else:
                logger.error("Failed to capture OAuth URL from agy.")
                try:
                    if proc.returncode is None:
                        proc.terminate()
                except Exception:
                    pass
                try: os.close(master)
                except Exception: pass
                await self.broadcast(
                    {
                        "type": "agent_token",
                        "turn_id": turn_id,
                        "delta": "\nCould not automatically generate Google OAuth session. Please retry.\n",
                    }
                )
                await self.broadcast({"type": "auth_failed"})
                await self.broadcast(
                    {
                        "type": "turn_complete",
                        "turn_id": turn_id,
                        "status": "ERROR",
                    }
                )
                self._cleanup_auth()
        except Exception as exc:
            err_msg = str(exc).strip() or repr(exc)
            logger.exception("Error during in-app login initialization: %s", err_msg)
            await self.broadcast(
                {
                    "type": "agent_token",
                    "turn_id": turn_id,
                    "delta": f"\n**Sign-in initialization error:** `{err_msg}`\n",
                }
            )
            await self.broadcast({"type": "auth_failed"})
            await self.broadcast(
                {
                    "type": "turn_complete",
                    "turn_id": turn_id,
                    "status": "ERROR",
                }
            )
            self._cleanup_auth()

    async def _consume_stdout_stream(
        self, turn_id: str, usage_summary: dict[str, int]
    ) -> bool:
        assert self.active_proc and self.active_proc.stdout
        emitted = False

        while True:
            raw_line = await self.active_proc.stdout.readline()
            if not raw_line:
                break

            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            # Parse NDJSON line or handle raw text fallback gracefully
            if not line.startswith("{"):
                lower = line.lower()
                if (
                    "authentication required" in lower
                    or "accounts.google.com/o/oauth2/auth" in lower
                    or "you are not logged into antigravity" in lower
                    or "error getting token source" in lower
                    or "please visit the url to log in" in lower
                ):
                    logger.warning("Auth requirement detected in CLI stdout: %s", line)
                    self._auth_required_detected = True
                    if self.active_proc and self.active_proc.returncode is None:
                        try:
                            self.active_proc.terminate()
                        except Exception:
                            pass
                    return False

                emitted = True
                await self.broadcast(
                    {"type": "agent_token", "turn_id": turn_id, "delta": line + "\n"}
                )
                continue

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                emitted = True
                await self.broadcast(
                    {"type": "agent_token", "turn_id": turn_id, "delta": line + "\n"}
                )
                continue

            if await self._demux_json_event(turn_id, event, usage_summary):
                emitted = True

        return emitted

    async def _consume_stderr_stream(self, turn_id: str) -> str:
        assert self.active_proc and self.active_proc.stderr
        chunks: list[str] = []
        while True:
            raw_line = await self.active_proc.stderr.readline()
            if not raw_line:
                break
            decoded = raw_line.decode("utf-8", errors="replace")
            chunks.append(decoded)

            # Real-time detection of auth expiration / sign-in requirement
            lower = decoded.lower()
            if (
                "authentication required" in lower
                or "accounts.google.com/o/oauth2/auth" in lower
                or "you are not logged into antigravity" in lower
                or "error getting token source" in lower
                or "please visit the url to log in" in lower
                or "please log in" in lower
            ):
                logger.warning("Auth requirement detected in CLI stderr stream: %s", decoded.strip())
                self._auth_required_detected = True
                # Terminate the blocked CLI process immediately to prevent 60s timeout stall
                if self.active_proc and self.active_proc.returncode is None:
                    try:
                        self.active_proc.terminate()
                    except Exception:
                        pass
                break
        return "".join(chunks)

    async def _demux_json_event(
        self,
        turn_id: str,
        event: dict[str, Any],
        usage_summary: dict[str, int],
    ) -> bool:
        """
        Normalizes polymorphic NDJSON events from `agy --output-format stream-json`
        and `--print` outputs into frontend WebSocket frames.
        """
        emitted = False

        # 1. Capture conversation_id if present in any frame
        conv_id = event.get("conversation_id") or event.get("session_id")
        if isinstance(conv_id, str) and conv_id:
            self.config.conversation_id = conv_id

        # 2. Extract token usage telemetry if present
        usage = event.get("usage") or (event.get("message") or {}).get("usage")
        if isinstance(usage, dict):
            for k in ("input_tokens", "output_tokens", "thinking_tokens"):
                if isinstance(usage.get(k), int):
                    usage_summary[k] = max(usage_summary[k], usage[k])

        etype = str(event.get("event") or event.get("type") or "").lower()

        # 3. Handle Antigravity Native "step_update" streaming events
        if etype == "step_update" and isinstance(event.get("step_update"), dict):
            su = event["step_update"]
            cid = su.get("conversation_id")
            if cid:
                self.config.conversation_id = cid

            # Usage telemetry if in step_update
            su_usage = su.get("usage")
            if isinstance(su_usage, dict):
                for k in ("input_tokens", "output_tokens", "thinking_tokens"):
                    if isinstance(su_usage.get(k), int):
                        usage_summary[k] = max(usage_summary[k], su_usage[k])

            # Text delta (streaming response tokens)
            text_delta = su.get("text_delta") or su.get("delta")
            if isinstance(text_delta, str) and text_delta:
                emitted = True
                self._streamed_turn_ids.add(turn_id)
                await self.broadcast(
                    {"type": "agent_token", "turn_id": turn_id, "delta": text_delta}
                )

            # Thinking delta (streaming reasoning tokens)
            thought_delta = (
                su.get("thinking_delta")
                or su.get("thought_delta")
                or su.get("thinking")
                or su.get("thought")
            )
            if isinstance(thought_delta, str) and thought_delta:
                emitted = True
                await self.broadcast(
                    {"type": "agent_thought", "turn_id": turn_id, "delta": thought_delta}
                )

            # Tool call or execution
            if (
                su.get("step_type") in ("tool", "tool_call", "tool_use")
                or "tool_call" in su
                or "tool_info" in su
                or "tool_name" in su
            ):
                tool_info = su.get("tool_info") or su.get("tool_call") or su
                t_id = str(tool_info.get("id") or tool_info.get("tool_id") or su.get("step_index") or f"tc-{uuid.uuid4().hex[:6]}")
                t_name = str(su.get("tool_name") or tool_info.get("name") or tool_info.get("tool_name") or "tool")
                t_args = tool_info.get("parameters") or tool_info.get("args") or tool_info.get("arguments") or tool_info.get("input") or {}
                t_status = "completed" if su.get("state") == "DONE" else "running"
                t_out = tool_info.get("output") or tool_info.get("result") or su.get("output") or ""
                emitted = True
                await self.broadcast(
                    {
                        "type": "tool_call",
                        "turn_id": turn_id,
                        "tool_id": t_id,
                        "name": t_name,
                        "args": t_args,
                        "status": t_status,
                        "output": str(t_out)[:4000] if t_out else "",
                    }
                )

            return emitted

        # 4. Handle Antigravity Native "result" event
        if etype == "result" and isinstance(event.get("result"), dict):
            res = event["result"]
            cid = res.get("conversation_id")
            if cid:
                self.config.conversation_id = cid

            err_msg = str(res.get("error") or "")
            if res.get("status") == "ERROR" and (
                "auth" in err_msg.lower()
                or "log in" in err_msg.lower()
                or "token" in err_msg.lower()
            ):
                logger.warning("Auth failure detected in result event: %s", err_msg)
                self._auth_required_detected = True
                if self.active_proc and self.active_proc.returncode is None:
                    try:
                        self.active_proc.terminate()
                    except Exception:
                        pass
                return False

            res_usage = res.get("usage")
            if isinstance(res_usage, dict):
                for k in ("input_tokens", "output_tokens", "thinking_tokens"):
                    if isinstance(res_usage.get(k), int):
                        usage_summary[k] = max(usage_summary[k], res_usage[k])

            # If tokens were not incrementally streamed, emit final response
            final_resp = res.get("response")
            if turn_id not in self._streamed_turn_ids and isinstance(final_resp, str) and final_resp:
                emitted = True
                await self.broadcast(
                    {"type": "agent_token", "turn_id": turn_id, "delta": final_resp}
                )
            return emitted

        # 4b. Handle explicit "error" event
        if etype == "error":
            err_str = str(event.get("error") or event.get("message") or "")
            if "auth" in err_str.lower() or "log in" in err_str.lower() or "token" in err_str.lower():
                logger.warning("Auth failure detected in error event: %s", err_str)
                self._auth_required_detected = True
                if self.active_proc and self.active_proc.returncode is None:
                    try:
                        self.active_proc.terminate()
                    except Exception:
                        pass
                return False

        # 5. Handle Antigravity Native "init" event
        if etype == "init":
            cid = event.get("conversation_id")
            if cid:
                self.config.conversation_id = cid
            return emitted

        # 6. Handle Anthropic/Gemini style content_block_delta events
        if etype == "content_block_delta":
            delta = event.get("delta") or {}
            dtype = str(delta.get("type") or "").lower()
            if "thinking" in dtype or "thought" in dtype:
                text = delta.get("thinking") or delta.get("text") or ""
                if text:
                    emitted = True
                    await self.broadcast(
                        {"type": "agent_thought", "turn_id": turn_id, "delta": text}
                    )
                return emitted
            text = delta.get("text") or ""
            if text:
                emitted = True
                await self.broadcast(
                    {"type": "agent_token", "turn_id": turn_id, "delta": text}
                )
            return emitted

        # 4. Handle direct thinking / reasoning events
        if etype in ("thinking", "thought", "reasoning") or (
            isinstance(event.get("thinking"), str) and not event.get("response")
        ):
            thought_delta = (
                event.get("thinking")
                or event.get("thought")
                or event.get("delta")
                or event.get("text")
                or ""
            )
            if isinstance(thought_delta, str) and thought_delta:
                emitted = True
                await self.broadcast(
                    {"type": "agent_thought", "turn_id": turn_id, "delta": thought_delta}
                )
            return emitted

        # 5. Handle tool call / tool execution events
        if etype in ("tool_call", "tool_use", "tool_result", "tool_execution") or "tool_call" in event:
            tool_obj = event.get("tool_call") or event.get("tool_use") or event
            t_id = str(tool_obj.get("id") or tool_obj.get("tool_id") or f"tc-{uuid.uuid4().hex[:6]}")
            t_name = str(
                tool_obj.get("name")
                or tool_obj.get("tool_name")
                or (tool_obj.get("function") or {}).get("name")
                or "tool"
            )
            t_args = (
                tool_obj.get("args")
                or tool_obj.get("arguments")
                or tool_obj.get("input")
                or {}
            )
            t_out = tool_obj.get("output") or tool_obj.get("result") or event.get("output") or ""
            t_status = "completed" if (etype == "tool_result" or t_out) else "running"

            emitted = True
            await self.broadcast(
                {
                    "type": "tool_call",
                    "turn_id": turn_id,
                    "tool_id": t_id,
                    "name": t_name,
                    "args": t_args,
                    "status": t_status,
                    "output": str(t_out)[:4000] if t_out else "",
                }
            )
            return emitted

        # 6. Handle structured assistant message envelope (`{"type": "assistant", "message": ...}`)
        if etype == "assistant" and isinstance(event.get("message"), dict):
            msg = event["message"]
            content_list = msg.get("content")
            if isinstance(content_list, list):
                for block in content_list:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype in ("thinking", "thought") and block.get("thinking"):
                        emitted = True
                        await self.broadcast(
                            {
                                "type": "agent_thought",
                                "turn_id": turn_id,
                                "delta": block["thinking"],
                            }
                        )
                    elif btype == "tool_use":
                        emitted = True
                        await self.broadcast(
                            {
                                "type": "tool_call",
                                "turn_id": turn_id,
                                "tool_id": block.get("id") or f"tc-{uuid.uuid4().hex[:6]}",
                                "name": block.get("name") or "tool",
                                "args": block.get("input") or {},
                                "status": "running",
                                "output": "",
                            }
                        )
                    elif btype == "text" and block.get("text"):
                        emitted = True
                        await self.broadcast(
                            {
                                "type": "agent_token",
                                "turn_id": turn_id,
                                "delta": block["text"],
                            }
                        )
                return emitted

        # 7. Handle single-shot `--print` / final result JSON (`{"conversation_id": ..., "status": "SUCCESS", "response": ...}`)
        if isinstance(event.get("response"), str) and event["response"]:
            # Only emit final `response` if tokens weren't already incrementally streamed
            if turn_id not in self._streamed_turn_ids:
                emitted = True
                await self.broadcast(
                    {
                        "type": "agent_token",
                        "turn_id": turn_id,
                        "delta": event["response"],
                    }
                )
            return emitted

        # 8. Handle generic text / delta frames
        for key in ("delta", "text", "content"):
            val = event.get(key)
            if isinstance(val, str) and val:
                emitted = True
                await self.broadcast(
                    {"type": "agent_token", "turn_id": turn_id, "delta": val}
                )
                return emitted

        return emitted

    async def abort_current_turn(self) -> bool:
        self._interrupted = True
        if self.active_proc and self.active_proc.returncode is None:
            logger.info("Sending SIGINT to active agy process (PID %s)", self.active_proc.pid)
            try:
                self.active_proc.send_signal(signal.SIGINT)
                await asyncio.sleep(0.3)
                if self.active_proc and self.active_proc.returncode is None:
                    self.active_proc.terminate()
                return True
            except ProcessLookupError:
                return False
        return False


# ---------------------------------------------------------------------------
# System Utilities & Slash Command Executor
# ---------------------------------------------------------------------------

def get_android_hardware_telemetry() -> dict[str, str]:
    mem_total = "N/A"
    mem_avail = "N/A"
    try:
        meminfo = Path("/proc/meminfo").read_text(encoding="utf-8")
        mt = re.search(r"MemTotal:\s+(\d+)\s+kB", meminfo)
        ma = re.search(r"MemAvailable:\s+(\d+)\s+kB", meminfo)
        if mt:
            mem_total = f"{int(mt.group(1)) / 1048576:.1f} GiB"
        if ma:
            mem_avail = f"{int(ma.group(1)) / 1048576:.1f} GiB"
    except Exception:
        pass

    load_str = "N/A"
    try:
        load_str = " / ".join(Path("/proc/loadavg").read_text().split()[:3])
    except Exception:
        pass

    telemetry_json = Path(os.environ.get("TMPDIR", "/tmp")) / "android_telemetry.json"
    battery_info = {}
    device_from_telemetry = None
    if telemetry_json.exists():
        try:
            with open(telemetry_json, "r", encoding="utf-8") as f:
                tdata = json.load(f)
                battery_info = {
                    "battery": f"{tdata.get('battery_pct')}%" if tdata.get('battery_pct') is not None else None,
                    "charging": tdata.get("charging"),
                    "thermal_temp": f"{tdata.get('temp_c')} °C" if tdata.get('temp_c') is not None else None,
                    "thermal_status": "NORMAL" if (tdata.get("temp_c") or 0) < 42.0 else "THROTTLED",
                }
                mfr = tdata.get("device_manufacturer")
                model = tdata.get("device_model")
                rel = tdata.get("android_release")
                sdk = tdata.get("sdk_int")
                if model or mfr:
                    dev_name = f"{mfr} {model}".strip() if mfr and not (model or "").startswith(mfr) else (model or mfr or "")
                    extra = []
                    if rel:
                        extra.append(f"Android {rel}")
                    if sdk:
                        extra.append(f"API {sdk}")
                    if extra:
                        dev_name += f" ({' / '.join(extra)})"
                    device_from_telemetry = dev_name
        except Exception:
            pass

    # Resolve dynamic device model & manufacturer via getprop if not in telemetry
    device_name = device_from_telemetry
    if not device_name:
        getprop_cmd = shutil.which("getprop") or "/system/bin/getprop"
        if os.path.exists(getprop_cmd):
            def _read_prop(prop_name: str) -> str:
                try:
                    res = subprocess.run([getprop_cmd, prop_name], capture_output=True, text=True, timeout=1.0)
                    return res.stdout.strip()
                except Exception:
                    return ""
            mfr = _read_prop("ro.product.manufacturer")
            model = _read_prop("ro.product.model")
            rel = _read_prop("ro.build.version.release")
            sdk = _read_prop("ro.build.version.sdk")
            if model or mfr:
                dev_name = f"{mfr} {model}".strip() if mfr and not (model or "").startswith(mfr) else (model or mfr or "")
                extra = []
                if rel:
                    extra.append(f"Android {rel}")
                if sdk:
                    extra.append(f"API {sdk}")
                if extra:
                    dev_name += f" ({' / '.join(extra)})"
                device_name = dev_name

    if not device_name:
        import platform
        device_name = f"Android ({platform.machine() or 'ARM64'})"

    # Resolve dynamic CPU arch & cores
    import platform
    cpu_arch = platform.machine() or "arm64"
    cores = os.cpu_count()
    core_str = f"{cores} cores" if cores else "multi-core"
    arch_str = f"{cpu_arch} ({core_str})"

    res = {
        "device": device_name,
        "arch": arch_str,
        "python": sys.version.split()[0],
        "mem_total": mem_total,
        "mem_avail": mem_avail,
        "loadavg": load_str,
    }
    res.update({k: str(v) for k, v in battery_info.items() if v is not None})
    return res


async def set_android_clipboard(text: str) -> bool:
    clip_bin = shutil.which("termux-clipboard-set")
    if not clip_bin:
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            clip_bin,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.communicate(input=text.encode("utf-8")), timeout=2.0)
        return proc.returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Main WebSocket & HTTP Multiplexed Application Server
# ---------------------------------------------------------------------------

class AgyWebServer:
    def __init__(self) -> None:
        self.storage = AntigravityStorageEngine()
        self.clients: set[ServerConnection] = set()
        self.orchestrator = AgyProcessOrchestrator(self.storage, self.broadcast)
        self.orchestrator.on_auth_success_cb = self.refresh_models
        self.default_workspace = get_default_workspace()
        self.orchestrator.config.workspace = self.default_workspace
        self.discovered_models: list[dict[str, Any]] = []
        self._cached_quotas: Optional[list[dict[str, str]]] = None
        self._quotas_cached_at: float = 0.0
        self._pending_questions: dict[str, dict[str, Any]] = {}
        self.ipc_sock_path: Path = GEMINI_DIR / "ask_question.sock"
        self.ipc_server: Optional[asyncio.AbstractServer] = None

    async def start_ipc_server(self) -> None:
        GEMINI_DIR.mkdir(parents=True, exist_ok=True)
        if self.ipc_sock_path.exists():
            try:
                self.ipc_sock_path.unlink()
            except Exception as exc:
                logger.warning("Could not unlink stale socket %s: %s", self.ipc_sock_path, exc)
        try:
            self.ipc_server = await asyncio.start_unix_server(
                self.handle_ipc_client,
                str(self.ipc_sock_path),
            )
            logger.info("Antigravity question IPC server listening on %s", self.ipc_sock_path)
        except Exception as exc:
            logger.error("Failed to start question IPC server on %s: %s", self.ipc_sock_path, exc)

    async def stop_ipc_server(self) -> None:
        if self.ipc_server:
            self.ipc_server.close()
            try:
                await self.ipc_server.wait_closed()
            except Exception:
                pass
            self.ipc_server = None
        if self.ipc_sock_path.exists():
            try:
                self.ipc_sock_path.unlink()
            except Exception:
                pass

    async def handle_ipc_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await reader.readline()
            if not line:
                return
            payload = json.loads(line.decode("utf-8"))
            action = payload.get("action", "ask")
            if action == "ask":
                res = await self.process_question_ask(payload)
                writer.write(json.dumps(res).encode("utf-8") + b"\n")
                await writer.drain()
            elif action == "respond":
                res = await self.resolve_question_response(payload)
                writer.write(json.dumps(res).encode("utf-8") + b"\n")
                await writer.drain()
            else:
                err_res = {"status": "error", "error": f"Unknown action: {action}"}
                writer.write(json.dumps(err_res).encode("utf-8") + b"\n")
                await writer.drain()
        except Exception as exc:
            logger.error("Error in question IPC handler: %s", exc, exc_info=True)
            try:
                err_res = {
                    "status": "error",
                    "skipped": True,
                    "formatted_output": f"A1: User Skipped (IPC error: {exc})",
                }
                writer.write(json.dumps(err_res).encode("utf-8") + b"\n")
                await writer.drain()
            except Exception:
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def process_question_ask(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_questions = payload.get("questions")
        if not raw_questions and (payload.get("question") or payload.get("title")):
            raw_questions = [{
                "question": payload.get("question") or payload.get("title"),
                "options": payload.get("options") or payload.get("choices") or ["(Recommended) Proceed", "Skip"],
                "is_multi_select": bool(payload.get("is_multi_select") or payload.get("isMultiSelect", False)),
            }]

        if not raw_questions or not isinstance(raw_questions, list):
            return {
                "error": "Missing or invalid 'questions' array",
                "status": "error",
                "skipped": True,
                "formatted_output": "A1: User Skipped (invalid questions)",
            }

        normalized_questions = []
        for q in raw_questions:
            if not isinstance(q, dict):
                continue
            q_text = str(q.get("question") or q.get("title") or "").strip()
            if not q_text:
                continue
            raw_opts = q.get("options") or q.get("choices") or ["(Recommended) Proceed", "Skip"]
            opts = [str(o).strip() for o in raw_opts if str(o).strip()]
            if not opts:
                opts = ["(Recommended) Proceed", "Skip"]
            multi = bool(q.get("is_multi_select") or q.get("isMultiSelect", False))
            normalized_questions.append({
                "question": q_text,
                "options": opts,
                "is_multi_select": multi,
            })

        if not normalized_questions:
            return {
                "error": "No valid questions found",
                "status": "error",
                "skipped": True,
                "formatted_output": "A1: User Skipped (no valid questions)",
            }

        tool_action = str(payload.get("tool_action") or payload.get("toolAction") or "Waiting for user decision").strip()
        tool_summary = str(payload.get("tool_summary") or payload.get("toolSummary") or "Decision required").strip()
        try:
            timeout_val = float(payload.get("timeout_seconds") or payload.get("timeoutSeconds") or 300)
        except (ValueError, TypeError):
            timeout_val = 300.0

        question_id = f"q-{uuid.uuid4().hex[:8]}"
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        self._pending_questions[question_id] = {
            "future": future,
            "session_id": self.orchestrator.config.conversation_id,
            "created_at": int(time.time()),
            "questions": normalized_questions,
            "tool_action": tool_action,
            "tool_summary": tool_summary,
        }

        # Broadcast WebSocket frame to all connected mobile clients
        await self.broadcast({
            "type": "ask_question",
            "question_id": question_id,
            "created_at": int(time.time()),
            "tool_action": tool_action,
            "tool_summary": tool_summary,
            "questions": normalized_questions,
        })

        try:
            result = await asyncio.wait_for(future, timeout=timeout_val)
            return result
        except asyncio.TimeoutError:
            timeout_res = {
                "status": "timeout",
                "question_id": question_id,
                "skipped": True,
                "answers": [],
                "formatted_output": f"A1: User Timed Out (no response received within {int(timeout_val)}s)",
            }
            await self.broadcast({
                "type": "dismiss_question",
                "question_id": question_id,
                "reason": "timeout",
            })
            return timeout_res
        except asyncio.CancelledError:
            await self.broadcast({
                "type": "dismiss_question",
                "question_id": question_id,
                "reason": "client_disconnected",
            })
            raise
        finally:
            self._pending_questions.pop(question_id, None)

    async def resolve_question_response(self, payload: dict[str, Any]) -> dict[str, Any]:
        question_id = str(payload.get("question_id") or "").strip()
        if not question_id or question_id not in self._pending_questions:
            return {"error": "Question not found or already resolved", "status": "not_found"}

        entry = self._pending_questions[question_id]
        future: Optional[asyncio.Future] = entry.get("future")
        if not future or future.done():
            return {"error": "Question already completed", "status": "gone"}

        skipped = bool(payload.get("skipped", False))
        if skipped:
            res = {
                "status": "skipped",
                "question_id": question_id,
                "skipped": True,
                "answers": [],
                "formatted_output": "A1: User Skipped",
            }
        else:
            responses = payload.get("responses")
            if responses and isinstance(responses, list):
                lines = []
                all_answers = []
                for idx, item in enumerate(responses):
                    if not isinstance(item, dict):
                        continue
                    q_idx = item.get("question_index", idx)
                    selected = [str(s).strip() for s in (item.get("selected") or []) if str(s).strip()]
                    write_in = str(item.get("write_in") or "").strip()
                    all_answers.extend(selected)
                    if write_in:
                        all_answers.append(write_in)

                    if selected and write_in:
                        line_content = f"{', '.join(selected)} (User Note: {write_in})"
                    elif selected:
                        line_content = ", ".join(selected)
                    elif write_in:
                        line_content = write_in
                    else:
                        line_content = "User Skipped"
                    lines.append(f"A{q_idx + 1}: {line_content}")
                formatted_out = "\n".join(lines) if lines else "A1: User Skipped"
            else:
                answers = [str(a).strip() for a in (payload.get("answers") or []) if str(a).strip()]
                write_in = str(payload.get("write_in") or "").strip()
                all_answers = list(answers)
                if write_in:
                    all_answers.append(write_in)
                if answers and write_in:
                    line_content = f"{', '.join(answers)} (User Note: {write_in})"
                elif answers:
                    line_content = ", ".join(answers)
                elif write_in:
                    line_content = write_in
                else:
                    line_content = "User Skipped"
                formatted_out = f"A1: {line_content}"

            res = {
                "status": "success",
                "question_id": question_id,
                "skipped": False,
                "answers": all_answers,
                "formatted_output": formatted_out,
            }

        future.set_result(res)
        await self.broadcast({
            "type": "dismiss_question",
            "question_id": question_id,
            "reason": "skipped" if skipped else "answered",
        })
        return {"status": "ok", "message": "Question response accepted"}

    async def _cancel_all_pending_questions(self, reason: str = "user_aborted") -> None:
        if not self._pending_questions:
            return
        for qid, qentry in list(self._pending_questions.items()):
            fut = qentry.get("future")
            if fut and not fut.done():
                fut.set_result({
                    "status": "aborted",
                    "question_id": qid,
                    "skipped": True,
                    "answers": [],
                    "formatted_output": "A1: User Aborted",
                })
            await self.broadcast({
                "type": "dismiss_question",
                "question_id": qid,
                "reason": reason,
            })
        self._pending_questions.clear()

    async def discover_models(self) -> list[dict[str, Any]]:
        models: list[dict[str, Any]] = []
        try:
            proc = await asyncio.create_subprocess_exec(
                os.environ.get("AGY_BIN", str(AGY_BIN)),
                "models",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=8.0)
            if proc.returncode == 0:
                for line in stdout.decode("utf-8", "ignore").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split("\t", 1)
                    if not parts:
                        continue
                    model_id = parts[0].strip()
                    label = parts[1].strip() if len(parts) > 1 else model_id
                    provider = "Google" if "gemini" in model_id.lower() else "Anthropic" if "claude" in model_id.lower() else "OpenAI/OSS" if "gpt" in model_id.lower() else "Other"
                    models.append({
                        "id": model_id,
                        "label": label,
                        "provider": provider,
                    })
            else:
                logger.info("Dynamic model query returned code %s (likely unauthenticated)", proc.returncode)
        except Exception as exc:
            logger.warning("Dynamic model query failed: %s", exc)
        return models

    async def refresh_models(self) -> None:
        self.discovered_models = await self.discover_models()
        if self.discovered_models and not self.orchestrator.config.model:
            self.orchestrator.config.model = self.discovered_models[0]["id"]
        await self.broadcast({
            "type": "models_updated",
            "models": self.discovered_models,
        })

    async def get_subscription_quotas(self) -> list[dict[str, str]]:
        now = time.time()
        if self._cached_quotas and (now - self._quotas_cached_at) < 45.0:
            return self._cached_quotas

        try:
            proc = await asyncio.create_subprocess_exec(
                os.environ.get("AGY_BIN", str(AGY_BIN)),
                "-p",
                "/usage",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=8.0)
            if proc.returncode != 0:
                return []

            quotas: list[dict[str, str]] = []
            for line in stdout.decode("utf-8", "ignore").splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = [p.strip() for p in line.split("\t") if p.strip()]
                if len(parts) >= 3:
                    family = parts[0]
                    period = parts[1]
                    pct = parts[2]
                    resets = parts[3] if len(parts) > 3 else ""
                    quotas.append({
                        "family": family,
                        "period": period,
                        "remaining": pct,
                        "resets": resets,
                    })
            if quotas:
                self._cached_quotas = quotas
                self._quotas_cached_at = now
            return quotas
        except Exception as exc:
            logger.warning("Failed to query subscription quotas: %s", exc)
            return self._cached_quotas or []

    async def broadcast(self, payload: dict[str, Any]) -> None:
        if not self.clients:
            return
        raw = json.dumps(payload, ensure_ascii=False)
        active_keys = get_active_api_keys()
        if active_keys:
            raw = redact_secrets_from_text(raw, active_keys)
        await asyncio.gather(
            *(self._safe_send(client, raw) for client in list(self.clients)),
            return_exceptions=True,
        )

    async def _safe_send(self, client: ServerConnection, raw: str) -> None:
        try:
            await client.send(raw)
        except Exception:
            self.clients.discard(client)

    async def handle_http_request(
        self, connection: ServerConnection, request: Request
    ) -> Optional[Response]:
        """
        Intercepts standard HTTP requests on port 8080 to serve the PWA frontend,
        manifest.json, and health check, while allowing `/ws` WebSocket upgrades through.
        """
        upgrade = request.headers.get("Upgrade", "").lower()
        if upgrade == "websocket":
            # 1. Enforce Origin check for WebSocket upgrades (CSWSH mitigation)
            origin = request.headers.get("Origin")
            if origin and not is_valid_local_origin(origin):
                logger.warning("Rejected WebSocket handshake from unauthorized Origin: %s", origin)
                return Response(
                    int(HTTPStatus.FORBIDDEN),
                    "Forbidden",
                    Headers([("Content-Type", "text/plain; charset=utf-8")]),
                    b"Forbidden: Cross-origin WebSocket connection denied.",
                )

            # 2. Enforce local session auth token for WebSocket upgrades
            if not is_request_authorized(request):
                logger.warning("Rejected WebSocket handshake: missing or invalid session auth token")
                return Response(
                    int(HTTPStatus.UNAUTHORIZED),
                    "Unauthorized",
                    Headers([("Content-Type", "text/plain; charset=utf-8")]),
                    b"Unauthorized: Valid session auth token required.",
                )

            return None  # Proceed with authorized WebSocket handshake

        path = request.path.split("?", 1)[0]

        if path in ("/", "/index.html"):
            if not INDEX_HTML_PATH.exists():
                body = b"<h1>Error: index.html not found in ~/.agy-ui/</h1>"
                return Response(
                    int(HTTPStatus.NOT_FOUND),
                    "Not Found",
                    Headers([("Content-Type", "text/html; charset=utf-8")]),
                    body,
                )
            body = INDEX_HTML_PATH.read_bytes()
            return Response(
                int(HTTPStatus.OK),
                "OK",
                Headers(
                    [
                        ("Content-Type", "text/html; charset=utf-8"),
                        ("Cache-Control", "no-cache"),
                        ("Content-Length", str(len(body))),
                    ]
                ),
                body,
            )

        if path == "/manifest.json":
            manifest = {
                "id": "/",
                "name": "Antigravity Studio",
                "short_name": "AgyStudio",
                "start_url": "/",
                "scope": "/",
                "display": "standalone",
                "background_color": "#090d16",
                "theme_color": "#090d16",
                "orientation": "portrait",
                "icons": [
                    {
                        "src": "/icon-192.png",
                        "sizes": "192x192",
                        "type": "image/png",
                        "purpose": "any maskable"
                    },
                    {
                        "src": "/icon-512.png",
                        "sizes": "512x512",
                        "type": "image/png",
                        "purpose": "any maskable"
                    }
                ]
            }
            body = json.dumps(manifest).encode("utf-8")
            return Response(
                int(HTTPStatus.OK),
                "OK",
                Headers(
                    [
                        ("Content-Type", "application/manifest+json; charset=utf-8"),
                        ("Cache-Control", "no-cache"),
                        ("Content-Length", str(len(body))),
                    ]
                ),
                body,
            )

        if path == "/sw.js":
            sw_path = APP_DIR / "sw.js"
            if sw_path.exists():
                body = sw_path.read_bytes()
                return Response(
                    int(HTTPStatus.OK),
                    "OK",
                    Headers(
                        [
                            ("Content-Type", "application/javascript; charset=utf-8"),
                            ("Cache-Control", "no-cache"),
                            ("Content-Length", str(len(body))),
                        ]
                    ),
                    body,
                )

        if path in ("/icon-192.png", "/icon-512.png"):
            icon_file = APP_DIR / path.lstrip("/")
            if icon_file.exists():
                body = icon_file.read_bytes()
                return Response(
                    int(HTTPStatus.OK),
                    "OK",
                    Headers(
                        [
                            ("Content-Type", "image/png"),
                            ("Cache-Control", "public, max-age=86400"),
                            ("Content-Length", str(len(body))),
                        ]
                    ),
                    body,
                )

        if path.startswith("/uploads/"):
            rel_name = path[len("/uploads/"):]
            upload_file = (UPLOADS_DIR / rel_name).resolve()
            if UPLOADS_DIR.resolve() in upload_file.parents and upload_file.is_file():
                body = upload_file.read_bytes()
                mime, _ = mimetypes.guess_type(str(upload_file))
                mime = mime or "application/octet-stream"
                return Response(
                    int(HTTPStatus.OK),
                    "OK",
                    Headers(
                        [
                            ("Content-Type", mime),
                            ("Cache-Control", "public, max-age=86400"),
                            ("Content-Length", str(len(body))),
                        ]
                    ),
                    body,
                )

        def _json_response(data: Any, status: HTTPStatus = HTTPStatus.OK) -> Response:
            body = json.dumps(data).encode("utf-8")
            return Response(
                int(status),
                status.phrase,
                Headers([
                    ("Content-Type", "application/json; charset=utf-8"),
                    ("Access-Control-Allow-Origin", f"http://{HOST}:{PORT}"),
                    ("Content-Length", str(len(body))),
                ]),
                body,
            )

        if path.startswith("/api/"):
            origin = request.headers.get("Origin")
            if origin and not is_valid_local_origin(origin):
                return _json_response({"error": "Forbidden: Cross-origin request denied"}, HTTPStatus.FORBIDDEN)
            if not is_request_authorized(request):
                return _json_response({"error": "Unauthorized: Session auth token required"}, HTTPStatus.UNAUTHORIZED)

        if path == "/api/prompts/user":
            if request.method == "GET":
                return _json_response(get_user_prompt_data())
            elif request.method == "POST":
                try:
                    payload = json.loads(request.body.decode("utf-8")) if request.body else {}
                    res = save_user_prompt_data(payload.get("content", ""))
                    return _json_response(res)
                except Exception as exc:
                    return _json_response({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        if path == "/api/ssh/keys":
            if request.method == "GET":
                keys = await list_ssh_keys()
                return _json_response({"keys": keys})
            elif request.method == "DELETE":
                try:
                    payload = json.loads(request.body.decode("utf-8")) if request.body else {}
                    ok = delete_ssh_key(payload.get("name", ""))
                    return _json_response({"success": ok})
                except Exception as exc:
                    return _json_response({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        if path == "/api/ssh/keys/generate":
            if request.method == "POST":
                try:
                    payload = json.loads(request.body.decode("utf-8")) if request.body else {}
                    res = await generate_ssh_key(
                        payload.get("name", ""),
                        payload.get("key_type", "ed25519"),
                        payload.get("overwrite", False)
                    )
                    return _json_response(res)
                except Exception as exc:
                    return _json_response({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        if path == "/api/ssh/keys/export":
            if request.method == "POST":
                try:
                    payload = json.loads(request.body.decode("utf-8")) if request.body else {}
                    dest = export_ssh_key(payload.get("name", ""))
                    return _json_response({"status": "ok", "destination": dest})
                except Exception as exc:
                    return _json_response({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        if path == "/api/keys":
            if request.method == "GET":
                return _json_response({"keys": list_api_keys_masked()})
            elif request.method == "POST":
                try:
                    payload = json.loads(request.body.decode("utf-8")) if request.body else {}
                    res = save_api_key(payload.get("name", ""), payload.get("value", ""), payload.get("description", ""))
                    return _json_response(res)
                except Exception as exc:
                    return _json_response({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            elif request.method == "DELETE":
                try:
                    payload = json.loads(request.body.decode("utf-8")) if request.body else {}
                    ok = delete_api_key(payload.get("name", ""))
                    return _json_response({"success": ok})
                except Exception as exc:
                    return _json_response({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        if path == "/api/skills":
            if request.method == "GET":
                skills = discover_skills(self.orchestrator.config.workspace)
                return _json_response({"skills": skills})
            elif request.method == "DELETE":
                try:
                    qs = request.path.split("?", 1)[1] if "?" in request.path else ""
                    params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
                    target = params.get("path", "")
                    ok = delete_skill(target, self.orchestrator.config.workspace)
                    return _json_response({"success": ok})
                except Exception as exc:
                    return _json_response({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        if path == "/api/skills/content":
            if request.method == "GET":
                try:
                    qs = request.path.split("?", 1)[1] if "?" in request.path else ""
                    params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
                    target = params.get("path", "")
                    content = get_skill_content(target, self.orchestrator.config.workspace)
                    return _json_response({"content": content})
                except Exception as exc:
                    return _json_response({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        if path == "/api/skills/save":
            if request.method == "POST":
                try:
                    payload = json.loads(request.body.decode("utf-8")) if request.body else {}
                    res = save_skill(payload.get("name", ""), payload.get("scope", "global"), payload.get("content", ""), self.orchestrator.config.workspace)
                    return _json_response(res)
                except Exception as exc:
                    return _json_response({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        if path == "/api/mcp/servers":
            if request.method == "GET":
                return _json_response({"servers": load_masked_mcp_servers()})
            elif request.method == "POST":
                try:
                    payload = json.loads(request.body.decode("utf-8")) if request.body else {}
                    res = await add_mcp_server(
                        payload.get("name", ""),
                        payload.get("transport", "http"),
                        payload.get("url_or_command", ""),
                        payload.get("args"),
                        payload.get("headers"),
                        payload.get("env")
                    )
                    return _json_response(res)
                except Exception as exc:
                    return _json_response({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            elif request.method == "DELETE":
                try:
                    payload = json.loads(request.body.decode("utf-8")) if request.body else {}
                    res = await delete_mcp_server(payload.get("name", ""))
                    return _json_response(res)
                except Exception as exc:
                    return _json_response({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        if path == "/api/mcp/toggle":
            if request.method == "POST":
                try:
                    payload = json.loads(request.body.decode("utf-8")) if request.body else {}
                    res = await toggle_mcp_server(payload.get("name", ""), payload.get("enabled", True))
                    return _json_response(res)
                except Exception as exc:
                    return _json_response({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        if path == "/api/updates/check":
            res = await check_github_updates()
            return _json_response(res)

        if path == "/api/question/ask":
            if request.method == "POST":
                try:
                    payload = json.loads(request.body.decode("utf-8")) if request.body else {}
                except Exception as exc:
                    return _json_response({"error": f"Invalid JSON payload: {exc}"}, HTTPStatus.BAD_REQUEST)
                res = await self.process_question_ask(payload)
                return _json_response(res)

        if path == "/api/question/respond":
            if request.method == "POST":
                try:
                    payload = json.loads(request.body.decode("utf-8")) if request.body else {}
                except Exception as exc:
                    return _json_response({"error": f"Invalid JSON payload: {exc}"}, HTTPStatus.BAD_REQUEST)
                res = await self.resolve_question_response(payload)
                status_code = HTTPStatus.OK if res.get("status") in ("ok", "success") else HTTPStatus.BAD_REQUEST
                return _json_response(res, status_code)

        if path == "/api/question/pending":
            if request.method == "GET":
                pending_list = []
                for qid, qentry in self._pending_questions.items():
                    pending_list.append({
                        "question_id": qid,
                        "created_at": qentry.get("created_at", 0),
                        "questions": qentry.get("questions", []),
                        "tool_action": qentry.get("tool_action", ""),
                        "tool_summary": qentry.get("tool_summary", ""),
                    })
                return _json_response({"pending": pending_list})

        if path == "/api/stop":
            if request.method == "POST":
                stopped = await self.orchestrator.abort_current_turn()
                await self._cancel_all_pending_questions(reason="user_aborted")
                return _json_response({"status": "ok", "stopped": stopped})

        if path == "/health":
            body = json.dumps({"status": "ok", "pid": os.getpid()}).encode("utf-8")
            return Response(
                int(HTTPStatus.OK),
                "OK",
                Headers(
                    [
                        ("Content-Type", "application/json"),
                        ("Content-Length", str(len(body))),
                    ]
                ),
                body,
            )

        body = b"Not Found"
        return Response(
            int(HTTPStatus.NOT_FOUND),
            "Not Found",
            Headers([("Content-Type", "text/plain"), ("Content-Length", str(len(body)))]),
            body,
        )

    async def ws_handler(self, websocket: ServerConnection) -> None:
        self.clients.add(websocket)
        logger.info("Client connected (%d active)", len(self.clients))

        try:
            if not self.discovered_models:
                self.discovered_models = await self.discover_models()
            if self.discovered_models and not self.orchestrator.config.model:
                self.orchestrator.config.model = self.discovered_models[0]["id"]

            sessions = self.storage.list_conversations(limit=80)
            # If no active conversation_id is set yet, attach to most recent session
            if not self.orchestrator.config.conversation_id and sessions:
                self.orchestrator.config.conversation_id = sessions[0]["conversation_id"]

            if self.orchestrator.config.conversation_id:
                meta = self.storage.get_conversation_meta(self.orchestrator.config.conversation_id)
                if meta.get("workspaces"):
                    ws_candidate = meta["workspaces"][0]
                    if Path(ws_candidate).is_dir():
                        self.orchestrator.config.workspace = ws_candidate

            branch = get_git_branch(self.orchestrator.config.workspace)

            await websocket.send(
                json.dumps(
                    {
                        "type": "init_state",
                        "default_workspace": self.default_workspace,
                        "config": {
                            "conversation_id": self.orchestrator.config.conversation_id,
                            "model": self.orchestrator.config.model,
                            "mode": self.orchestrator.config.mode,
                            "effort": self.orchestrator.config.effort,
                            "workspace": self.orchestrator.config.workspace,
                            "git_branch": branch,
                            "models": self.discovered_models,
                            "modes": VALID_MODES,
                            "efforts": VALID_EFFORTS,
                        },
                        "system_info": {
                            "app_version": f"v{APP_VERSION}",
                            "agy_version": f"v{get_agy_version()}",
                            "device": get_android_hardware_telemetry().get("device", "Android Device"),
                        },
                        "sessions": sessions,
                        "is_running": self.orchestrator.is_running,
                        "is_authenticating": self.orchestrator.is_authenticating,
                    }
                )
            )

            # Hydrate current conversation transcript if one is selected
            if self.orchestrator.config.conversation_id:
                cid = self.orchestrator.config.conversation_id
                meta = self.storage.get_conversation_meta(cid)
                history = self.storage.load_transcript(cid)
                await websocket.send(
                    json.dumps(
                        {
                            "type": "session_history",
                            "conversation_id": cid,
                            "title": meta["title"],
                            "meta": meta,
                            "workspace": self.orchestrator.config.workspace,
                            "git_branch": branch,
                            "messages": history,
                        }
                    )
                )

            # Hydrate any active pending questions (M-14)
            if self._pending_questions:
                for qid, qentry in list(self._pending_questions.items()):
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "ask_question",
                                "question_id": qid,
                                "created_at": qentry.get("created_at", int(time.time())),
                                "tool_action": qentry.get("tool_action", ""),
                                "tool_summary": qentry.get("tool_summary", ""),
                                "questions": qentry.get("questions", []),
                            }
                        )
                    )

            async for raw_msg in websocket:
                try:
                    data = json.loads(raw_msg)
                except json.JSONDecodeError:
                    continue
                await self.handle_client_message(websocket, data)

        except websockets.ConnectionClosed:
            pass
        finally:
            self.clients.discard(websocket)
            logger.info("Client disconnected (%d active)", len(self.clients))

    async def handle_client_message(
        self, websocket: ServerConnection, data: dict[str, Any]
    ) -> None:
        mtype = data.get("type")

        if mtype == "submit_auth_code":
            code = str(data.get("code") or "").strip()
            if code:
                asyncio.create_task(self.orchestrator.submit_auth_code(code))
            return

        if mtype == "trigger_login":
            turn_id = f"auth-{uuid.uuid4().hex[:6]}"
            asyncio.create_task(self.orchestrator.trigger_in_app_login(turn_id))
            return

        if mtype == "refresh_models":
            asyncio.create_task(self.refresh_models())
            return

        # Custom Instructions
        if mtype in ("get_user_prompt", "get_prompts"):
            p_data = get_user_prompt_data()
            await websocket.send(json.dumps({"type": "user_prompt_data", **p_data}))
            return

        if mtype in ("save_user_prompt", "save_prompts"):
            content = str(data.get("content") or data.get("user_prompt") or "")
            try:
                res = save_user_prompt_data(content)
                await websocket.send(json.dumps({"type": "user_prompt_saved", **res}))
                await self.broadcast({
                    "type": "system_card",
                    "title": "Custom Instructions Saved",
                    "markdown": f"Custom user instructions saved ({res['bytes']} bytes).",
                    "level": "info",
                })
            except Exception as exc:
                await websocket.send(json.dumps({
                    "type": "system_card",
                    "title": "Save Failed",
                    "markdown": f"Failed to save user instructions: `{exc}`",
                    "level": "error",
                }))
            return

        # SSH Key Management
        if mtype == "list_ssh_keys":
            keys = await list_ssh_keys()
            await websocket.send(json.dumps({"type": "ssh_keys_list", "keys": keys}))
            return

        if mtype == "generate_ssh_key":
            name = str(data.get("name") or "").strip()
            ktype = str(data.get("key_type") or "ed25519").strip()
            overwrite = bool(data.get("overwrite", False))
            try:
                new_key = await generate_ssh_key(name, ktype, overwrite=overwrite)
                keys = await list_ssh_keys()
                await self.broadcast({"type": "ssh_keys_list", "keys": keys})
                await self.broadcast({"type": "ssh_key_created", "key": new_key})
                await self.broadcast({
                    "type": "system_card",
                    "title": "SSH Key Generated",
                    "markdown": f"Generated `{name}` ({ktype.upper()}) and synchronized `~/.ssh/config`.",
                    "level": "info",
                })
            except Exception as exc:
                await websocket.send(json.dumps({
                    "type": "system_card",
                    "title": "SSH Key Generation Failed",
                    "markdown": f"Could not generate SSH key: `{exc}`",
                    "level": "error",
                }))
            return

        if mtype == "export_ssh_key":
            name = str(data.get("name") or "").strip()
            try:
                dest = export_ssh_key(name)
                await websocket.send(json.dumps({"type": "ssh_key_exported", "name": name, "destination": dest}))
                await self.broadcast({
                    "type": "system_card",
                    "title": "Public Key Exported",
                    "markdown": f"Saved `{name}.pub` to `{dest}`.",
                    "level": "info",
                })
            except Exception as exc:
                await websocket.send(json.dumps({
                    "type": "system_card",
                    "title": "Export Failed",
                    "markdown": f"Could not export public key: `{exc}`",
                    "level": "error",
                }))
            return

        if mtype == "delete_ssh_key":
            name = str(data.get("name") or "").strip()
            if name:
                ok = delete_ssh_key(name)
                if ok:
                    keys = await list_ssh_keys()
                    await self.broadcast({"type": "ssh_keys_list", "keys": keys})
                    await self.broadcast({"type": "ssh_key_deleted", "name": name})
            return

        if mtype == "get_ssh_pub_key":
            name = str(data.get("name") or "").strip()
            try:
                pub_txt = get_ssh_pub_key(name)
                await websocket.send(json.dumps({"type": "ssh_pub_key", "name": name, "public_key": pub_txt}))
            except Exception as exc:
                await websocket.send(json.dumps({"type": "system_card", "title": "Key Read Error", "markdown": str(exc), "level": "error"}))
            return

        # API Keys Management
        if mtype == "list_api_keys":
            keys = list_api_keys_masked()
            await websocket.send(json.dumps({"type": "api_keys_list", "keys": keys}))
            return

        if mtype == "save_api_key":
            name = str(data.get("name") or "").strip()
            val = str(data.get("value") or "").strip()
            desc = str(data.get("description") or "").strip()
            try:
                res = save_api_key(name, val, desc)
                keys = list_api_keys_masked()
                await self.broadcast({"type": "api_keys_list", "keys": keys})
                await self.broadcast({"type": "api_key_saved", "key": res})
                await self.broadcast({
                    "type": "system_card",
                    "title": "API Key Stored",
                    "markdown": f"Stored `{name}` and updated `~/.gemini/config/rules/api_keys_manifest.md`.",
                    "level": "info",
                })
            except Exception as exc:
                await websocket.send(json.dumps({
                    "type": "system_card",
                    "title": "Save Failed",
                    "markdown": f"Could not store API key: `{exc}`",
                    "level": "error",
                }))
            return

        if mtype == "delete_api_key":
            name = str(data.get("name") or "").strip()
            if name:
                ok = delete_api_key(name)
                if ok:
                    keys = list_api_keys_masked()
                    await self.broadcast({"type": "api_keys_list", "keys": keys})
                    await self.broadcast({"type": "api_key_deleted", "name": name})
            return

        if mtype == "get_api_key_raw":
            name = str(data.get("name") or "").strip()
            try:
                raw_val = get_api_key_raw(name)
                await websocket.send(json.dumps({"type": "api_key_raw", "name": name, "value": raw_val}))
            except Exception as exc:
                await websocket.send(json.dumps({"type": "system_card", "title": "Key Read Error", "markdown": str(exc), "level": "error"}))
            return

        # Skills Management
        if mtype == "list_skills":
            skills = discover_skills(self.orchestrator.config.workspace)
            await websocket.send(json.dumps({"type": "skills_list", "skills": skills}))
            return

        if mtype in ("get_skill", "get_skill_content"):
            path_str = str(data.get("path") or "").strip()
            for_edit = bool(data.get("for_edit", False))
            name = str(data.get("name") or "").strip()
            try:
                content = get_skill_content(path_str, self.orchestrator.config.workspace)
                if not name and path_str:
                    p = Path(path_str)
                    name = p.parent.name if p.name == "SKILL.md" else p.stem
                await websocket.send(json.dumps({
                    "type": "skill_content",
                    "path": path_str,
                    "content": content,
                    "name": name,
                    "for_edit": for_edit
                }))
            except Exception as exc:
                await websocket.send(json.dumps({"type": "system_card", "title": "Skill Read Error", "markdown": str(exc), "level": "error"}))
            return

        if mtype == "save_skill":
            name = str(data.get("name") or data.get("folder_name") or "").strip()
            scope = str(data.get("scope") or "global").strip()
            content = str(data.get("content") or "").strip()
            try:
                res = save_skill(name, scope, content, self.orchestrator.config.workspace)
                skills = discover_skills(self.orchestrator.config.workspace)
                await self.broadcast({"type": "skills_list", "skills": skills})
                await self.broadcast({"type": "skill_saved", "skill": res})
                await self.broadcast({
                    "type": "system_card",
                    "title": "Skill Saved",
                    "markdown": f"Saved skill **`{res['name']}`** to `{res['path']}`.",
                    "level": "info",
                })
            except Exception as exc:
                await websocket.send(json.dumps({
                    "type": "system_card",
                    "title": "Skill Save Failed",
                    "markdown": f"Could not save skill: `{exc}`",
                    "level": "error",
                }))
            return

        if mtype == "delete_skill":
            path_str = str(data.get("path") or "").strip()
            try:
                ok = delete_skill(path_str, self.orchestrator.config.workspace)
                if ok:
                    skills = discover_skills(self.orchestrator.config.workspace)
                    await self.broadcast({"type": "skills_list", "skills": skills})
                    await self.broadcast({"type": "skill_deleted", "path": path_str})
            except Exception as exc:
                await websocket.send(json.dumps({
                    "type": "system_card",
                    "title": "Skill Delete Failed",
                    "markdown": f"Could not delete skill: `{exc}`",
                    "level": "error",
                }))
            return

        if mtype == "create_skill_prompt":
            s_name = str(data.get("name") or "").strip()
            s_what = str(data.get("what") or data.get("purpose") or "").strip()
            s_when = str(data.get("when") or data.get("trigger") or "").strip()
            s_scope = str(data.get("scope") or "global").strip()
            target_path = f"{self.orchestrator.config.workspace}/.agents/skills/{s_name}/SKILL.md" if s_scope.lower() == "workspace" else f"{GLOBAL_SKILLS_DIR}/{s_name}/SKILL.md"

            ai_prompt = (
                f"The user wants to create a new Antigravity skill:\n"
                f"- Name: {s_name}\n"
                f"- Purpose: {s_what}\n"
                f"- Trigger Condition: {s_when}\n"
                f"- Scope: {s_scope.capitalize()}\n"
                f"- Target Path: {target_path}\n\n"
                f"Frontmatter Requirements:\n"
                f"- Must start with fenced YAML (`---`) with `name: {s_name}` (kebab-case) and `description` (>= 20 characters explaining what it does and when to activate it).\n"
                f"- Android Sandbox Rule: If the skill includes helper scripts, always instruct the agent to run them via `bash <script>` or `python3 <script>`, never `./<script>` (due to Android SELinux W^X restrictions).\n\n"
                f"Please design the complete SKILL.md. If any parameters, scripts, or edge cases are unclear, interview the user first. Once aligned, create the skill directory and write SKILL.md."
            )
            # Reset session state for a fresh conversation
            self.orchestrator.config.conversation_id = None
            self.orchestrator.config.total_input_tokens = 0
            self.orchestrator.config.total_output_tokens = 0
            self.orchestrator.config.total_thinking_tokens = 0
            self.orchestrator.config.turn_count = 0
            branch = get_git_branch(self.orchestrator.config.workspace)
            await self.broadcast({
                "type": "session_history",
                "conversation_id": None,
                "title": f"Skill: {s_name}",
                "messages": [],
                "history": [],
                "workspace": self.orchestrator.config.workspace,
                "git_branch": branch,
                "config": {
                    "model": self.orchestrator.config.model,
                    "mode": self.orchestrator.config.mode,
                    "effort": self.orchestrator.config.effort,
                    "workspace": self.orchestrator.config.workspace,
                    "git_branch": branch,
                },
            })
            asyncio.create_task(self.orchestrator.execute_turn(ai_prompt))
            return

        if mtype == "edit_skill_prompt":
            path_str = str(data.get("path") or "").strip()
            s_name = str(data.get("name") or (Path(path_str).parent.name if Path(path_str).name == "SKILL.md" else Path(path_str).stem) or "Skill").strip()
            ai_prompt = (
                f"The user wants to edit this skill: {path_str}.\n"
                f"Study the existing SKILL.md and associated files in detail, then ask the user how they would like to modify it."
            )
            # Reset session state for a fresh conversation
            self.orchestrator.config.conversation_id = None
            self.orchestrator.config.total_input_tokens = 0
            self.orchestrator.config.total_output_tokens = 0
            self.orchestrator.config.total_thinking_tokens = 0
            self.orchestrator.config.turn_count = 0
            branch = get_git_branch(self.orchestrator.config.workspace)
            await self.broadcast({
                "type": "session_history",
                "conversation_id": None,
                "title": f"Edit: {s_name}",
                "messages": [],
                "history": [],
                "workspace": self.orchestrator.config.workspace,
                "git_branch": branch,
                "config": {
                    "model": self.orchestrator.config.model,
                    "mode": self.orchestrator.config.mode,
                    "effort": self.orchestrator.config.effort,
                    "workspace": self.orchestrator.config.workspace,
                    "git_branch": branch,
                },
            })
            asyncio.create_task(self.orchestrator.execute_turn(ai_prompt))
            return

        # MCP Servers Management
        if mtype == "list_mcp_servers":
            servers = load_masked_mcp_servers()
            await websocket.send(json.dumps({"type": "mcp_servers_list", "servers": servers}))
            async def _probe():
                status_map = await probe_mcp_health()
                await self.broadcast({"type": "mcp_health_update", "status_map": status_map})
            asyncio.create_task(_probe())
            return

        if mtype == "probe_mcp_health":
            name = str(data.get("name") or "").strip()
            if name:
                res = await probe_single_mcp_server(name)
                is_ok = res.get("status") in ("connected", "ready")
                await websocket.send(json.dumps({
                    "type": "mcp_probe_result",
                    "name": name,
                    "status": res.get("status", "error"),
                    "message": res.get("message", ""),
                    "ok": is_ok,
                }))
                await self.broadcast({
                    "type": "mcp_health_update",
                    "status_map": {name: res},
                })
            else:
                status_map = await probe_mcp_health()
                await websocket.send(json.dumps({
                    "type": "mcp_probe_result",
                    "name": "all",
                    "status": "connected",
                    "message": f"Health check completed for {len(status_map)} server(s)",
                    "ok": True,
                }))
                await self.broadcast({"type": "mcp_health_update", "status_map": status_map})
            return

        if mtype == "add_mcp_server":
            name = str(data.get("name") or "").strip()
            trans = str(data.get("transport") or "http").strip()
            url_cmd = str(data.get("url_or_command") or "").strip()
            args = data.get("args")
            headers = data.get("headers")
            env = data.get("env")
            try:
                res = await add_mcp_server(name, trans, url_cmd, args=args, headers=headers, env=env)
                servers = load_masked_mcp_servers()
                await self.broadcast({"type": "mcp_servers_list", "servers": servers})
                await self.broadcast({"type": "mcp_server_added", "server": res})
                await self.broadcast({
                    "type": "system_card",
                    "title": "MCP Server Added",
                    "markdown": f"Connected MCP server **`{name}`** ({trans.upper()}).",
                    "level": "info",
                })
            except Exception as exc:
                await websocket.send(json.dumps({
                    "type": "system_card",
                    "title": "Add MCP Server Failed",
                    "markdown": f"Could not add MCP server: `{exc}`",
                    "level": "error",
                }))
            return

        if mtype == "toggle_mcp_server":
            name = str(data.get("name") or "").strip()
            enabled = bool(data.get("enabled", True))
            try:
                res = await toggle_mcp_server(name, enabled)
                servers = load_masked_mcp_servers()
                await self.broadcast({"type": "mcp_servers_list", "servers": servers})
                await self.broadcast({"type": "mcp_server_toggled", "server": res})
            except Exception as exc:
                await websocket.send(json.dumps({
                    "type": "system_card",
                    "title": "Toggle Failed",
                    "markdown": f"Could not toggle MCP server: `{exc}`",
                    "level": "error",
                }))
            return

        if mtype == "delete_mcp_server":
            name = str(data.get("name") or "").strip()
            try:
                res = await delete_mcp_server(name)
                servers = load_masked_mcp_servers()
                await self.broadcast({"type": "mcp_servers_list", "servers": servers})
                await self.broadcast({"type": "mcp_server_deleted", "server": res})
            except Exception as exc:
                await websocket.send(json.dumps({
                    "type": "system_card",
                    "title": "Delete MCP Server Failed",
                    "markdown": f"Could not remove MCP server: `{exc}`",
                    "level": "error",
                }))
            return

        if mtype == "setup_mcp_prompt":
            brief = str(data.get("brief") or "").strip()
            name = str(data.get("name") or "").strip()
            details = str(data.get("details") or "").strip()
            if not brief and (name or details):
                brief = f"{name}: {details}".strip(": ") if details else name
            active_keys = list(load_api_keys_store().get("keys", {}).keys())
            keys_hint = ", ".join(f"`{k}`" for k in active_keys) if active_keys else "none configured yet"

            ai_prompt = (
                f"The user wants to configure an MCP server in Antigravity Studio for Android:\n"
                f"Description: {brief}\n\n"
                f"Environment Context:\n"
                f"- Config path: ~/.gemini/config/mcp_config.json (must have 0600 permissions).\n"
                f"- CLI tool: `agy mcp` (run `agy mcp --help` and `agy mcp add --help` to inspect exact flags). Always pass `--type http` for remote URLs or `--type stdio` for local commands.\n"
                f"- Available local runtimes for Stdio MCP servers: `python3` (Python 3.14), `node`/`npx` (installable on demand via `apk add --no-cache nodejs npm`), `bash`, `curl`, `git`, `busybox`. Note that `docker` is NOT supported in this Android sandbox; prefer Streamable HTTP (`--type http`) or local Python/Node MCP servers.\n"
                f"- Pre-configured environment API keys available: {keys_hint}.\n\n"
                f"Please configure this server, verify ~/.gemini/config/mcp_config.json is valid JSON with 0600 permissions, and test readiness with `agy mcp list`."
            )
            # Reset session state for a fresh conversation
            self.orchestrator.config.conversation_id = None
            self.orchestrator.config.total_input_tokens = 0
            self.orchestrator.config.total_output_tokens = 0
            self.orchestrator.config.total_thinking_tokens = 0
            self.orchestrator.config.turn_count = 0
            branch = get_git_branch(self.orchestrator.config.workspace)
            await self.broadcast({
                "type": "session_history",
                "conversation_id": None,
                "title": f"Setup MCP: {name or brief[:16]}",
                "messages": [],
                "history": [],
                "workspace": self.orchestrator.config.workspace,
                "git_branch": branch,
                "config": {
                    "model": self.orchestrator.config.model,
                    "mode": self.orchestrator.config.mode,
                    "effort": self.orchestrator.config.effort,
                    "workspace": self.orchestrator.config.workspace,
                    "git_branch": branch,
                },
            })
            asyncio.create_task(self.orchestrator.execute_turn(ai_prompt))
            return

        # Package Manager (v1.3.0)
        if mtype == "get_packages":
            pkg_data = get_installed_packages()
            await websocket.send(json.dumps({"type": "packages_data", **pkg_data}))
            return

        if mtype == "delete_package":
            pkg_name = str(data.get("name") or "").strip()
            try:
                validate_pkg_name(pkg_name)
                await self.broadcast({
                    "type": "package_mutation_status",
                    "action": "delete",
                    "package": pkg_name,
                    "in_progress": True,
                })
                success, msg = await delete_package(pkg_name)
                await self.broadcast({
                    "type": "package_mutation_result",
                    "action": "delete",
                    "package": pkg_name,
                    "success": success,
                    "message": msg,
                })
                pkg_data = get_installed_packages()
                await self.broadcast({"type": "packages_data", **pkg_data})
            except Exception as exc:
                await self.broadcast({
                    "type": "package_mutation_result",
                    "action": "delete",
                    "package": pkg_name,
                    "success": False,
                    "message": str(exc),
                })
            return

        if mtype == "upgrade_package":
            pkg_name = str(data.get("name") or "").strip()
            try:
                validate_pkg_name(pkg_name)
                await self.broadcast({
                    "type": "package_mutation_status",
                    "action": "upgrade",
                    "package": pkg_name,
                    "in_progress": True,
                })
                success, msg = await upgrade_package(pkg_name)
                await self.broadcast({
                    "type": "package_mutation_result",
                    "action": "upgrade",
                    "package": pkg_name,
                    "success": success,
                    "message": msg,
                })
                pkg_data = get_installed_packages()
                await self.broadcast({"type": "packages_data", **pkg_data})
            except Exception as exc:
                await self.broadcast({
                    "type": "package_mutation_result",
                    "action": "upgrade",
                    "package": pkg_name,
                    "success": False,
                    "message": str(exc),
                })
            return

        if mtype == "upgrade_all_packages":
            try:
                await self.broadcast({
                    "type": "package_mutation_status",
                    "action": "upgrade_all",
                    "package": "*",
                    "in_progress": True,
                })
                success, msg = await upgrade_all_packages()
                await self.broadcast({
                    "type": "package_mutation_result",
                    "action": "upgrade_all",
                    "package": "*",
                    "success": success,
                    "message": msg,
                })
                pkg_data = get_installed_packages()
                await self.broadcast({"type": "packages_data", **pkg_data})
            except Exception as exc:
                await self.broadcast({
                    "type": "package_mutation_result",
                    "action": "upgrade_all",
                    "package": "*",
                    "success": False,
                    "message": str(exc),
                })
            return

        if mtype == "install_package_prompt":
            raw_query = str(data.get("package_query") or data.get("query") or "").strip()
            clean_query = re.sub(r"[\r\n\t`]", " ", raw_query).strip()[:100]
            if not clean_query:
                return

            if self.orchestrator.is_running:
                await websocket.send(json.dumps({
                    "type": "system_card",
                    "title": "Agent Busy",
                    "markdown": "Cannot initiate package installation while a turn is actively executing.",
                    "level": "error",
                }))
                return

            ai_prompt = (
                f'The user wants to install the package: "{clean_query}" in the Antigravity Studio Alpine Linux container.\n\n'
                f"Environment Directives:\n"
                f"- Subsystem: Alpine Linux aarch64 (PRoot container with W^X anonymous ELF loader).\n"
                f"- Package Manager: `apk` (uses `apk add --no-cache <package>`).\n"
                f"- Search Tool: `apk search <term>`.\n\n"
                f"Instructions:\n"
                f"1. Exact Match Check:\n"
                f'   If "{clean_query}" unambiguously identifies a valid Alpine package (e.g., `nodejs`, `python3`, `rust`, `git`, `ffmpeg`, `go`):\n'
                f"   - Immediately execute `apk add --no-cache {clean_query}` via `run_command`.\n"
                f"   - Run a quick smoke verification (e.g. `<binary> --version`).\n"
                f"   - Confirm successful installation and installed version to the user.\n\n"
                f"2. Ambiguous Query Disambiguation (CRITICAL):\n"
                f'   If "{clean_query}" is ambiguous, a general category, or could correspond to multiple package candidates (e.g., "c++", "postgres", "java", "opencv", "linter", or a keyword search):\n'
                f"   - First, run `apk search <term>` via `run_command` to discover valid candidate packages.\n"
                f"   - Present the candidate packages to the user using the interactive question tool:\n"
                f"     ask_question << 'EOF'\n"
                f"     {{\n"
                f'       "questions": [\n'
                f"         {{\n"
                f'           "question": "Which package would you like to install?",\n'
                f'           "options": [\n'
                f'             "(Recommended) <package_1> - <brief description>",\n'
                f'             "<package_2> - <brief description>",\n'
                f'             "<package_3> - <brief description>"\n'
                f"           ],\n"
                f'           "is_multi_select": false\n'
                f"         }}\n"
                f"       ],\n"
                f'       "toolAction": "Selecting package to install",\n'
                f'       "toolSummary": "Package disambiguation"\n'
                f"     }}\n"
                f"     EOF\n"
                f"   - CRITICAL OPTION EXTRACTION: When the user selects an option from `ask_question`, extract ONLY the valid Alpine package name(s) (stripping prefixes like `(Recommended)` and suffixes like `- <description>`) before executing `apk add --no-cache <pkg>`.\n"
                f"   - Run a quick verification of the installed binary to confirm readiness."
            )

            self.orchestrator.config.conversation_id = None
            self.orchestrator.config.total_input_tokens = 0
            self.orchestrator.config.total_output_tokens = 0
            self.orchestrator.config.total_thinking_tokens = 0
            self.orchestrator.config.turn_count = 0
            branch = get_git_branch(self.orchestrator.config.workspace)
            await self.broadcast({
                "type": "session_history",
                "conversation_id": None,
                "title": f"Install: {clean_query[:20]}",
                "messages": [],
                "history": [],
                "workspace": self.orchestrator.config.workspace,
                "git_branch": branch,
                "config": {
                    "model": self.orchestrator.config.model,
                    "mode": self.orchestrator.config.mode,
                    "effort": self.orchestrator.config.effort,
                    "workspace": self.orchestrator.config.workspace,
                    "git_branch": branch,
                },
            })
            asyncio.create_task(self.orchestrator.execute_turn(ai_prompt))
            return

        # Updates Check
        if mtype == "check_for_updates":
            try:
                res = await check_github_updates()
                await websocket.send(json.dumps({"type": "update_check_result", **res}))
            except Exception as exc:
                await websocket.send(json.dumps({
                    "type": "update_check_result",
                    "status": "error",
                    "message": str(exc),
                }))
            return

        if mtype == "user_prompt":
            raw_prompt = str(data.get("prompt") or "").strip()
            if self.orchestrator.is_authenticating and raw_prompt:
                asyncio.create_task(self.orchestrator.submit_auth_code(raw_prompt))
                return

            attachments = data.get("attachments") or []
            saved_attachments = []

            if attachments and isinstance(attachments, list):
                for att in attachments:
                    if not isinstance(att, dict):
                        continue
                    fname = Path(str(att.get("name") or "file.bin")).name
                    safe_name = re.sub(r"[^\w\-.]", "_", fname)
                    unique_name = f"{uuid.uuid4().hex[:8]}_{safe_name}"
                    file_path = UPLOADS_DIR / unique_name
                    raw_data = str(att.get("data") or "")
                    try:
                        if "," in raw_data:
                            raw_data = raw_data.split(",", 1)[1]
                        file_bytes = base64.b64decode(raw_data)
                        file_path.write_bytes(file_bytes)
                        saved_attachments.append({
                            "name": fname,
                            "safe_name": safe_name,
                            "path": str(file_path),
                            "url": f"/uploads/{unique_name}",
                            "size": len(file_bytes),
                            "type": str(att.get("type") or "application/octet-stream"),
                        })
                    except Exception as exc:
                        logger.warning("Failed to decode attachment %s: %s", fname, exc)

            prompt = raw_prompt
            if saved_attachments:
                file_lines = [f"- {a['name']} (path: {a['path']})" for a in saved_attachments]
                attachment_header = "The user attached the following local file(s):\n" + "\n".join(file_lines)
                if prompt:
                    prompt = f"{attachment_header}\n\nUser request: {prompt}"
                else:
                    prompt = f"{attachment_header}\n\nPlease inspect the attached file(s) and provide a summary or address any relevant questions."

            if not prompt:
                return

            # Update session config if sent with prompt
            if data.get("model"):
                self.orchestrator.config.model = str(data["model"])
            if data.get("mode"):
                chosen_mode = str(data["mode"])
                if chosen_mode in ("default", "accept-edits", "plan"):
                    self.orchestrator.config.mode = chosen_mode
                elif chosen_mode in ("auto", "standard"):
                    self.orchestrator.config.mode = "default"
            if data.get("effort") in VALID_EFFORTS:
                self.orchestrator.config.effort = str(data["effort"])

            # Check if prompt is a built-in slash command (only if no attachments)
            if not saved_attachments and prompt.startswith("/"):
                parts = prompt.split(maxsplit=1)
                cmd = parts[0].lower()
                arg = parts[1] if len(parts) > 1 else ""
                if await self.execute_slash_command(cmd, arg):
                    return

            asyncio.create_task(
                self.orchestrator.execute_turn(
                    prompt,
                    attachments=saved_attachments,
                )
            )
            return

        if mtype == "abort_generation":
            stopped = await self.orchestrator.abort_current_turn()
            await self._cancel_all_pending_questions(reason="user_aborted")
            await self.broadcast(
                {
                    "type": "status",
                    "state": "idle",
                    "detail": "Interrupted" if stopped else "Ready",
                }
            )
            return

        if mtype == "list_sessions":
            query = str(data.get("query") or "")
            sessions = self.storage.list_conversations(search_query=query, limit=100)
            await websocket.send(
                json.dumps({"type": "session_list", "sessions": sessions})
            )
            return

        if mtype == "switch_session":
            cid = str(data.get("conversation_id") or "").strip()
            if not cid:
                return
            await self._cancel_all_pending_questions(reason="session_changed")
            self.orchestrator.config.conversation_id = cid
            meta = self.storage.get_conversation_meta(cid)
            if meta.get("workspaces"):
                ws_candidate = meta["workspaces"][0]
                if Path(ws_candidate).is_dir():
                    self.orchestrator.config.workspace = ws_candidate
            history = self.storage.load_transcript(cid)
            branch = get_git_branch(self.orchestrator.config.workspace)
            await self.broadcast(
                {
                    "type": "session_history",
                    "conversation_id": cid,
                    "title": meta["title"],
                    "meta": meta,
                    "workspace": self.orchestrator.config.workspace,
                    "git_branch": branch,
                    "messages": history,
                }
            )
            await self.broadcast(
                {
                    "type": "config_updated",
                    "config": {
                        "conversation_id": self.orchestrator.config.conversation_id,
                        "model": self.orchestrator.config.model,
                        "mode": self.orchestrator.config.mode,
                        "effort": self.orchestrator.config.effort,
                        "workspace": self.orchestrator.config.workspace,
                        "git_branch": branch,
                    },
                }
            )
            return

        if mtype == "new_session":
            await self._cancel_all_pending_questions(reason="session_changed")
            self.orchestrator.config.conversation_id = None
            self.orchestrator.config.total_input_tokens = 0
            self.orchestrator.config.total_output_tokens = 0
            self.orchestrator.config.total_thinking_tokens = 0
            self.orchestrator.config.turn_count = 0
            self.orchestrator.config.workspace = self.default_workspace
            branch = get_git_branch(self.default_workspace)
            await self.broadcast(
                {
                    "type": "session_history",
                    "conversation_id": None,
                    "title": "New Conversation",
                    "meta": {"workspaces": [self.default_workspace]},
                    "workspace": self.default_workspace,
                    "git_branch": branch,
                    "messages": [],
                }
            )
            await self.broadcast(
                {
                    "type": "config_updated",
                    "config": {
                        "conversation_id": None,
                        "model": self.orchestrator.config.model,
                        "mode": self.orchestrator.config.mode,
                        "effort": self.orchestrator.config.effort,
                        "workspace": self.default_workspace,
                        "git_branch": branch,
                    },
                }
            )
            return

        if mtype == "rename_session":
            cid = str(data.get("conversation_id") or "").strip()
            new_title = str(data.get("title") or "").strip()
            if cid and new_title:
                success = self.storage.rename_conversation(cid, new_title)
                if success:
                    sessions = self.storage.list_conversations(limit=100)
                    await self.broadcast({"type": "session_list", "sessions": sessions})
                    if self.orchestrator.config.conversation_id == cid:
                        await self.broadcast({
                            "type": "session_renamed",
                            "conversation_id": cid,
                            "title": new_title,
                        })
                else:
                    await websocket.send(json.dumps({
                        "type": "system_card",
                        "title": "Rename Failed",
                        "markdown": f"Could not rename session `{cid}`.",
                        "level": "error"
                    }))
            return

        if mtype == "delete_session":
            cid = str(data.get("conversation_id") or "").strip()
            if cid:
                is_active = (self.orchestrator.config.conversation_id == cid)
                success = self.storage.delete_conversation(cid)
                if success:
                    sessions = self.storage.list_conversations(limit=100)
                    await self.broadcast({"type": "session_list", "sessions": sessions})
                    if is_active:
                        self.orchestrator.config.conversation_id = None
                        self.orchestrator.config.total_input_tokens = 0
                        self.orchestrator.config.total_output_tokens = 0
                        self.orchestrator.config.total_thinking_tokens = 0
                        self.orchestrator.config.turn_count = 0
                        await self.broadcast({
                            "type": "session_history",
                            "conversation_id": None,
                            "title": "New Conversation",
                            "meta": {"workspaces": [self.orchestrator.config.workspace]},
                            "messages": [],
                        })
                else:
                    await websocket.send(json.dumps({
                        "type": "system_card",
                        "title": "Delete Failed",
                        "markdown": f"Could not delete session `{cid}`.",
                        "level": "error"
                    }))
            return

        if mtype == "branch_session":
            orig_cid = str(data.get("conversation_id") or self.orchestrator.config.conversation_id or "").strip()
            branch_name = str(data.get("name") or "").strip() or None
            if orig_cid:
                try:
                    new_session = self.storage.branch_conversation(orig_cid, branch_name)
                    new_cid = new_session["conversation_id"]
                    self.orchestrator.config.conversation_id = new_cid
                    sessions = self.storage.list_conversations(limit=100)
                    await self.broadcast({"type": "session_list", "sessions": sessions})
                    history = self.storage.load_transcript(new_cid)
                    meta = self.storage.get_conversation_meta(new_cid)
                    await self.broadcast(
                        {
                            "type": "session_history",
                            "conversation_id": new_cid,
                            "title": meta["title"],
                            "meta": meta,
                            "messages": history,
                        }
                    )
                    await self.broadcast(
                        {
                            "type": "system_card",
                            "title": "Conversation Branched",
                            "markdown": f"Branched to **{meta['title']}** (`{new_cid[:8]}...`). You can now diverge this session independently.",
                            "level": "info",
                        }
                    )
                except Exception as exc:
                    logger.exception("Failed to branch session %s", orig_cid)
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "system_card",
                                "title": "Branch Failed",
                                "markdown": f"Could not branch session: `{exc}`",
                                "level": "error",
                            }
                        )
                    )
            return

        if mtype == "rewind_session":
            cid = str(data.get("conversation_id") or self.orchestrator.config.conversation_id or "").strip()
            step_index = data.get("step_index")
            if isinstance(step_index, str) and step_index.isdigit():
                step_index = int(step_index)
            prompt_text = str(data.get("prompt_text") or "")
            if cid:
                try:
                    rewound_prompt = self.storage.rewind_conversation(cid, step_index, prompt_text)
                    updated_history = self.storage.load_transcript(cid)
                    sessions = self.storage.list_conversations(limit=100)
                    await self.broadcast({"type": "session_list", "sessions": sessions})
                    await self.broadcast(
                        {
                            "type": "session_rewound",
                            "conversation_id": cid,
                            "prompt_text": rewound_prompt,
                            "messages": updated_history,
                        }
                    )
                except Exception as exc:
                    logger.exception("Failed to rewind session %s", cid)
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "system_card",
                                "title": "Rewind Failed",
                                "markdown": f"Could not rewind session: `{exc}`",
                                "level": "error",
                            }
                        )
                    )
            return

        if mtype == "copy_clipboard":
            text = str(data.get("text") or "")
            if text:
                await set_android_clipboard(text)
            return

        if mtype == "update_config":
            if data.get("model"):
                self.orchestrator.config.model = str(data["model"])
            if data.get("mode"):
                chosen_mode = str(data["mode"])
                if chosen_mode in ("default", "accept-edits", "plan"):
                    self.orchestrator.config.mode = chosen_mode
                elif chosen_mode in ("auto", "standard"):
                    self.orchestrator.config.mode = "default"
            if data.get("effort") in VALID_EFFORTS:
                self.orchestrator.config.effort = str(data["effort"])
            if data.get("workspace"):
                target = Path(os.path.expanduser(str(data["workspace"]))).resolve()
                if target.is_dir():
                    self.orchestrator.config.workspace = str(target)
                    cid = self.orchestrator.config.conversation_id
                    if cid:
                        self.storage.update_conversation_workspace(cid, str(target))
            branch = get_git_branch(self.orchestrator.config.workspace)
            await self.broadcast(
                {
                    "type": "config_updated",
                    "config": {
                        "conversation_id": self.orchestrator.config.conversation_id,
                        "model": self.orchestrator.config.model,
                        "mode": self.orchestrator.config.mode,
                        "effort": self.orchestrator.config.effort,
                        "workspace": self.orchestrator.config.workspace,
                        "git_branch": branch,
                    },
                }
            )
            return

        if mtype == "list_directory":
            req_path = str(data.get("path") or "").strip()
            if not req_path:
                target = Path(self.orchestrator.config.workspace)
            elif req_path.startswith("~"):
                target = Path(os.path.expanduser(req_path)).resolve()
            else:
                target = Path(req_path).resolve()

            if not target.exists() or not target.is_dir():
                target = Path(self.orchestrator.config.workspace)
                if not target.is_dir():
                    target = Path(get_default_workspace())

            subdirs = []
            try:
                for entry in target.iterdir():
                    try:
                        if entry.is_dir() and not entry.name.startswith("."):
                            subdirs.append({
                                "name": entry.name,
                                "path": str(entry),
                                "is_git": (entry / ".git").exists(),
                            })
                    except (PermissionError, OSError):
                        continue
                subdirs.sort(key=lambda x: x["name"].lower())
            except Exception as exc:
                logger.warning("Error reading directory %s: %s", target, exc)

            branch = get_git_branch(str(target))
            parent = str(target.parent) if target.parent != target else None

            shortcuts = [
                {"name": "Documents", "path": "/storage/emulated/0/Documents"},
                {"name": "Downloads", "path": "/storage/emulated/0/Download"},
                {"name": "Shared (/sdcard)", "path": "/storage/emulated/0"},
                {"name": "App Home", "path": str(HOME_DIR)},
                {"name": "App Workspace", "path": str(HOME_DIR / "workspace")},
            ]
            valid_shortcuts = [sc for sc in shortcuts if Path(sc["path"]).exists()]

            await websocket.send(
                json.dumps(
                    {
                        "type": "directory_listing",
                        "current_path": str(target),
                        "parent_path": parent,
                        "is_git": branch is not None,
                        "git_branch": branch,
                        "directories": subdirs,
                        "shortcuts": valid_shortcuts,
                    }
                )
            )
            return

        if mtype == "create_directory":
            parent_path = str(data.get("parent_path") or "").strip()
            folder_name = str(data.get("name") or "").strip()
            if parent_path and folder_name:
                try:
                    new_folder = (Path(parent_path) / folder_name).resolve()
                    new_folder.mkdir(parents=True, exist_ok=True)
                    data["path"] = str(new_folder)
                except Exception as exc:
                    await websocket.send(json.dumps({
                        "type": "system_card",
                        "title": "Folder Creation Error",
                        "markdown": f"Failed to create directory: `{exc}`",
                        "level": "warning"
                    }))
                    return
            target = Path(str(data.get("path") or parent_path)).resolve()
            subdirs = []
            try:
                for entry in target.iterdir():
                    if entry.is_dir() and not entry.name.startswith("."):
                        subdirs.append({
                            "name": entry.name,
                            "path": str(entry),
                            "is_git": (entry / ".git").exists(),
                        })
                subdirs.sort(key=lambda x: x["name"].lower())
            except Exception:
                pass
            branch = get_git_branch(str(target))
            parent = str(target.parent) if target.parent != target else None
            await websocket.send(json.dumps({
                "type": "directory_listing",
                "current_path": str(target),
                "parent_path": parent,
                "is_git": branch is not None,
                "git_branch": branch,
                "directories": subdirs,
            }))
            return

        if mtype == "set_default_workspace":
            req_path = str(data.get("path") or "").strip()
            if req_path:
                target = Path(os.path.expanduser(req_path)).resolve()
                if target.is_dir():
                    self.default_workspace = str(target)
                    settings = load_app_settings()
                    settings["default_workspace"] = str(target)
                    save_app_settings(settings)
                    await self.broadcast({
                        "type": "default_workspace_updated",
                        "default_workspace": str(target),
                    })
                    await self.broadcast({
                        "type": "system_card",
                        "title": "Default Workspace Updated",
                        "markdown": f"Default workspace for new sessions set to: `{target}`",
                        "level": "info",
                    })
            return

        if mtype == "set_workspace":
            req_path = str(data.get("path") or "").strip()
            cid = data.get("conversation_id") or self.orchestrator.config.conversation_id
            if req_path:
                target = Path(os.path.expanduser(req_path)).resolve()
                if target.is_dir():
                    self.orchestrator.config.workspace = str(target)
                    branch = get_git_branch(str(target))
                    if cid:
                        self.storage.update_conversation_workspace(cid, str(target))

                    await self.broadcast({
                        "type": "config_updated",
                        "config": {
                            "conversation_id": self.orchestrator.config.conversation_id,
                            "model": self.orchestrator.config.model,
                            "mode": self.orchestrator.config.mode,
                            "effort": self.orchestrator.config.effort,
                            "workspace": self.orchestrator.config.workspace,
                            "git_branch": branch,
                        }
                    })
                    sessions = self.storage.list_conversations(limit=100)
                    await self.broadcast({"type": "session_list", "sessions": sessions})
                    await self.broadcast({
                        "type": "system_card",
                        "title": "Session Workspace Changed",
                        "markdown": f"Working directory for this session replaced with: `{target}`" + (f"\n\nGit branch: **`{branch}`**" if branch else ""),
                        "level": "info"
                    })
            return

        if mtype == "question_response":
            await self.resolve_question_response(data)
            return

        if mtype == "get_pending_questions":
            for qid, qentry in list(self._pending_questions.items()):
                await websocket.send(
                    json.dumps(
                        {
                            "type": "ask_question",
                            "question_id": qid,
                            "created_at": qentry.get("created_at", int(time.time())),
                            "tool_action": qentry.get("tool_action", ""),
                            "tool_summary": qentry.get("tool_summary", ""),
                            "questions": qentry.get("questions", []),
                        }
                    )
                )
            return

        if mtype == "slash_command":
            cmd = str(data.get("command") or "").lower()
            arg = str(data.get("args") or "").strip()
            await self.execute_slash_command(cmd, arg)
            return

        if mtype == "set_clipboard":
            text = str(data.get("text") or "")
            ok = await set_android_clipboard(text)
            await websocket.send(
                json.dumps({"type": "clipboard_ack", "success": ok})
            )
            return

    async def execute_slash_command(self, cmd: str, arg: str) -> bool:
        cfg = self.orchestrator.config

        if cmd == "/clear":
            cfg.conversation_id = None
            cfg.total_input_tokens = 0
            cfg.total_output_tokens = 0
            cfg.total_thinking_tokens = 0
            cfg.turn_count = 0
            cfg.workspace = self.default_workspace
            branch = get_git_branch(self.default_workspace)
            await self.broadcast(
                {
                    "type": "session_history",
                    "conversation_id": None,
                    "title": "New Conversation",
                    "meta": {"workspaces": [self.default_workspace]},
                    "workspace": self.default_workspace,
                    "git_branch": branch,
                    "messages": [],
                }
            )
            await self.broadcast(
                {
                    "type": "config_updated",
                    "config": {
                        "conversation_id": None,
                        "model": cfg.model,
                        "mode": cfg.mode,
                        "effort": cfg.effort,
                        "workspace": self.default_workspace,
                        "git_branch": branch,
                    },
                }
            )
            return True

        if cmd == "/branch":
            orig_cid = cfg.conversation_id
            if not orig_cid:
                await self.broadcast(
                    {
                        "type": "system_card",
                        "title": "Branch Error",
                        "markdown": "Cannot branch: no active conversation selected.",
                        "level": "warning",
                    }
                )
                return True

            branch_name = arg.strip() if arg.strip() else None
            try:
                new_session = self.storage.branch_conversation(orig_cid, branch_name)
                new_cid = new_session["conversation_id"]
                cfg.conversation_id = new_cid
                sessions = self.storage.list_conversations(limit=100)
                await self.broadcast({"type": "session_list", "sessions": sessions})
                history = self.storage.load_transcript(new_cid)
                meta = self.storage.get_conversation_meta(new_cid)
                await self.broadcast(
                    {
                        "type": "session_history",
                        "conversation_id": new_cid,
                        "title": meta["title"],
                        "meta": meta,
                        "messages": history,
                    }
                )
                await self.broadcast(
                    {
                        "type": "system_card",
                        "title": "Conversation Branched",
                        "markdown": f"Branched to **{meta['title']}** (`{new_cid[:8]}...`). You can now diverge this session independently.",
                        "level": "info",
                    }
                )
            except Exception as exc:
                logger.exception("Failed to branch conversation %s", orig_cid)
                await self.broadcast(
                    {
                        "type": "system_card",
                        "title": "Branch Error",
                        "markdown": f"Failed to branch conversation: `{exc}`",
                        "level": "warning",
                    }
                )
            return True

        if cmd in ("/models", "/model"):
            if not self.discovered_models:
                self.discovered_models = await self.discover_models()
            if arg:
                cfg.model = arg
                for ef in ("low", "medium", "high"):
                    if cfg.model.endswith(f"-{ef}"):
                        cfg.effort = ef
                        break
                await self.broadcast(
                    {
                        "type": "config_updated",
                        "config": {
                            "conversation_id": cfg.conversation_id,
                            "model": cfg.model,
                            "mode": cfg.mode,
                            "effort": cfg.effort,
                            "workspace": cfg.workspace,
                        },
                    }
                )
                await self.broadcast(
                    {
                        "type": "system_card",
                        "title": "Model Switched",
                        "markdown": f"Active model set to **`{cfg.model}`**.",
                        "level": "info",
                    }
                )
            else:
                if self.discovered_models:
                    rows = "\n".join(
                        f"| `{m['id']}` | {m['label']} | {m['provider']} |"
                        for m in self.discovered_models
                    )
                    md = (
                        f"**Current Model:** `{cfg.model}`\n\n"
                        "| Model ID | Description | Provider |\n"
                        "| :--- | :--- | :--- |\n"
                        f"{rows}\n\n"
                        "*Tip: Tap the Model pill above the keyboard or run `/model <id>` to switch.*"
                    )
                else:
                    md = (
                        f"**Current Model:** `{cfg.model}`\n\n"
                        "*No models discovered. Sign in with Google (`/login`) to query available models from Antigravity.*"
                    )
                await self.broadcast(
                    {"type": "system_card", "title": "Available Models", "markdown": md, "level": "info"}
                )
            return True

        if cmd == "/mode":
            mode_arg = arg.strip().lower()
            if mode_arg in ("default", "accept-edits", "plan"):
                cfg.mode = mode_arg
            elif mode_arg in ("auto", "standard"):
                cfg.mode = "default"
            if mode_arg in ("default", "accept-edits", "plan", "auto", "standard"):
                await self.broadcast(
                    {
                        "type": "config_updated",
                        "config": {
                            "conversation_id": cfg.conversation_id,
                            "model": cfg.model,
                            "mode": cfg.mode,
                            "effort": cfg.effort,
                            "workspace": cfg.workspace,
                        },
                    }
                )
            md = (
                f"**Active Execution Mode:** `{cfg.mode}`\n\n"
                "- `default`: Standard execution with automatic tool permissions\n"
                "- `accept-edits`: Automatically apply file edits in workspace\n"
                "- `plan`: Architectural planning & read-only analysis mode"
            )
            await self.broadcast(
                {"type": "system_card", "title": "Execution Mode", "markdown": md, "level": "info"}
            )
            return True

        if cmd == "/effort":
            if arg in VALID_EFFORTS:
                cfg.effort = arg
                if cfg.model.startswith("gemini-3.8-flash"):
                    cfg.model = f"gemini-3.8-flash-{arg}"
                await self.broadcast(
                    {
                        "type": "config_updated",
                        "config": {
                            "conversation_id": cfg.conversation_id,
                            "model": cfg.model,
                            "mode": cfg.mode,
                            "effort": cfg.effort,
                            "workspace": cfg.workspace,
                        },
                    }
                )
            await self.broadcast(
                {
                    "type": "system_card",
                    "title": "Reasoning Effort",
                    "markdown": f"**Active Reasoning Effort:** `{cfg.effort}` (`low` | `medium` | `high`)\n\n*(Applies to Gemini 3.8 Flash)*",
                    "level": "info",
                }
            )
            return True

        if cmd in ("/login", "/auth"):
            turn_id = f"auth-{uuid.uuid4().hex[:6]}"
            asyncio.create_task(self.orchestrator.trigger_in_app_login(turn_id))
            return True

        if cmd in ("/usage", "/quota", "/cost"):
            await self.broadcast({
                "type": "status",
                "state": "running",
                "detail": "Querying subscription quotas & context...",
            })
            try:
                # 1. Context Window breakdown
                cid = cfg.conversation_id
                latest_input = 0
                latest_cached = 0

                if cid:
                    transcript_path = (
                        BRAIN_DIR
                        / cid
                        / ".system_generated"
                        / "logs"
                        / "transcript.jsonl"
                    )
                    if transcript_path.exists():
                        try:
                            with transcript_path.open("r", encoding="utf-8", errors="replace") as f:
                                for raw_line in f:
                                    line = raw_line.strip()
                                    if not line:
                                        continue
                                    try:
                                        ev = json.loads(line)
                                    except json.JSONDecodeError:
                                        continue
                                    if ev.get("type") in ("PLANNER_RESPONSE", "ASSISTANT", "MODEL"):
                                        if "input_tokens" in ev:
                                            latest_input = ev["input_tokens"]
                                        if "cache_read_tokens" in ev:
                                            latest_cached = ev["cache_read_tokens"]
                                        elif isinstance(ev.get("usage"), dict):
                                            latest_input = ev["usage"].get("input_tokens", latest_input)
                                            latest_cached = ev["usage"].get("cache_read_tokens", latest_cached)
                        except Exception as exc:
                            logger.warning("Error reading transcript for /usage: %s", exc)

                model_limits = {
                    "gemini-3.8-flash-high": 1_000_000,
                    "gemini-3.8-flash-medium": 1_000_000,
                    "gemini-3.8-flash-low": 1_000_000,
                    "gemini-3.8-flash": 1_000_000,
                    "claude-sonnet-4-6": 200_000,
                    "claude-opus-4-6-thinking": 200_000,
                    "gpt-oss-120b-medium": 128_000,
                }
                limit = model_limits.get(cfg.model, 1_000_000)
                active_context = latest_input + latest_cached
                if active_context == 0 and cfg.total_input_tokens > 0:
                    active_context = cfg.total_input_tokens

                pct = min(100.0, (active_context / limit) * 100) if limit > 0 else 0.0
                pct_remaining = max(0.0, 100.0 - pct)
                remaining_tokens = max(0, limit - active_context)
                filled = int(round(pct / 5))
                bar = "█" * filled + "░" * (20 - filled)

                # 2. Subscription Quotas from CLI
                quotas = await self.get_subscription_quotas()

                lines = [
                    "### Context Window Utilization",
                    "",
                    f"`[{bar}]` **`{pct:.1f}%`** used (`{active_context:,}` / `{limit:,}` tokens) — **`{pct_remaining:.1f}%`** free",
                    "",
                    "| Context Metric | Value |",
                    "| :--- | :--- |",
                    f"| **Active Model** | `{cfg.model}` |",
                    f"| **Window Limit** | `{limit:,}` tokens |",
                    f"| **Active Context** | `{active_context:,}` tokens (`{pct:.1f}%`) |",
                    f"| **Remaining Space** | `{remaining_tokens:,}` tokens (`{pct_remaining:.1f}%`) |",
                    "",
                    "---",
                    "",
                    "### Subscription Quotas",
                    "",
                ]

                if quotas:
                    lines.append("| Model Family | Limit Period | Remaining Quota | Resets At |")
                    lines.append("| :--- | :--- | :--- | :--- |")
                    for q in quotas:
                        resets = q["resets"]
                        if "T" in resets:
                            resets = resets.replace("T", " ").replace("Z", " UTC")
                        lines.append(f"| **{q['family']}** | {q['period']} | **`{q['remaining']}`** remaining | {resets} |")
                else:
                    lines.append("*Quota telemetry unavailable. Sign in with Google (`/login`) to view account quotas.*")

                lines.extend([
                    "",
                    "---",
                    "",
                    "### Current Session Activity",
                    "",
                    "| Activity Metric | Count |",
                    "| :--- | :--- |",
                    f"| **Completed Turns** | `{cfg.turn_count}` |",
                    f"| **Cumulative Input Tokens** | `{cfg.total_input_tokens:,}` |",
                    f"| **Cumulative Output Tokens** | `{cfg.total_output_tokens:,}` |",
                    f"| **Cumulative Thinking Tokens** | `{cfg.total_thinking_tokens:,}` |",
                ])

                md = "\n".join(lines)
                await self.broadcast(
                    {"type": "system_card", "title": "Subscription Quotas & Context", "markdown": md, "level": "info"}
                )
            finally:
                await self.broadcast({"type": "status", "state": "idle", "detail": "Ready"})
            return True

        if cmd == "/context":
            await self.broadcast({
                "type": "status",
                "state": "running",
                "detail": "Calculating context window usage...",
            })
            try:
                cid = cfg.conversation_id
                latest_input = 0
                latest_cached = 0
                msg_count = 0
                turn_count = 0

                if cid:
                    transcript_path = (
                        BRAIN_DIR
                        / cid
                        / ".system_generated"
                        / "logs"
                        / "transcript.jsonl"
                    )
                    if transcript_path.exists():
                        try:
                            with transcript_path.open("r", encoding="utf-8", errors="replace") as f:
                                for raw_line in f:
                                    line = raw_line.strip()
                                    if not line:
                                        continue
                                    try:
                                        ev = json.loads(line)
                                    except json.JSONDecodeError:
                                        continue
                                    msg_count += 1
                                    if ev.get("type") in ("PLANNER_RESPONSE", "ASSISTANT", "MODEL"):
                                        turn_count += 1
                                        if "input_tokens" in ev:
                                            latest_input = ev["input_tokens"]
                                        if "cache_read_tokens" in ev:
                                            latest_cached = ev["cache_read_tokens"]
                                        elif isinstance(ev.get("usage"), dict):
                                            latest_input = ev["usage"].get("input_tokens", latest_input)
                                            latest_cached = ev["usage"].get("cache_read_tokens", latest_cached)
                        except Exception as exc:
                            logger.warning("Error reading transcript for /context: %s", exc)

                model_limits = {
                    "gemini-3.8-flash-high": 1_000_000,
                    "gemini-3.8-flash-medium": 1_000_000,
                    "gemini-3.8-flash-low": 1_000_000,
                    "claude-sonnet-4-6": 200_000,
                    "claude-opus-4-6-thinking": 200_000,
                    "gpt-oss-120b-medium": 128_000,
                }
                limit = model_limits.get(cfg.model, 1_000_000)
                active_context = latest_input + latest_cached
                if active_context == 0 and cfg.total_input_tokens > 0:
                    active_context = cfg.total_input_tokens

                pct = min(100.0, (active_context / limit) * 100) if limit > 0 else 0.0
                remaining = max(0, limit - active_context)

                filled = int(round(pct / 5))
                bar = "█" * filled + "░" * (20 - filled)

                md = (
                    f"### Context Window & Token Breakdown\n\n"
                    f"| Context Parameter | Count / Value | Details |\n"
                    f"| :--- | :--- | :--- |\n"
                    f"| **Active Model** | `{cfg.model}` | Window limit: `{limit:,}` tokens |\n"
                    f"| **Context Window Limit** | **`{limit:,}`** tokens | Maximum supported capacity |\n"
                    f"| **Current Active Context** | **`{active_context:,}`** tokens | **`{pct:.2f}%`** used |\n"
                    f"| **Context Remaining** | `{remaining:,}` tokens | `{(100 - pct):.2f}%` capacity free |\n"
                    f"| **Prompt Cache Tokens** | `{latest_cached:,}` tokens | Reused from prompt cache |\n"
                    f"| **New Input Tokens** | `{latest_input:,}` tokens | Fresh prompt & delta input |\n"
                    f"| **Total Session Output** | `{cfg.total_output_tokens:,}` tokens | Cumulative response tokens |\n"
                    f"| **Total Thinking Tokens** | `{cfg.total_thinking_tokens:,}` tokens | Extended reasoning trace |\n"
                    f"| **Conversation History** | `{msg_count:,}` steps | `{turn_count or cfg.turn_count}` turns recorded |\n"
                    f"| **Active Workspace** | `{cfg.workspace}` | Local root directory |\n\n"
                    f"**Context Utilization:**\n"
                    f"`[{bar}]` **`{pct:.2f}%`** (`{active_context:,}` / `{limit:,}`)\n\n"
                    f"> *Tip: If context approaches 80%, run `/compact` to generate a structured summary or `/branch` to start an independent branch.*"
                )
                await self.broadcast(
                    {"type": "system_card", "title": "Context Window Breakdown", "markdown": md, "level": "info"}
                )
            finally:
                await self.broadcast({"type": "status", "state": "idle", "detail": "Ready"})
            return True

        if cmd == "/compact":
            prompt = (
                "Please generate a comprehensive, structured summary checkpoint of our conversation so far, "
                "covering key decisions, modified files, technical architecture, and current next steps. "
                "This checkpoint will serve as our compacted context baseline."
            )
            asyncio.create_task(self.orchestrator.execute_turn(prompt))
            return True

        if cmd == "/status":
            await self.broadcast({
                "type": "status",
                "state": "running",
                "detail": "Inspecting device hardware & diagnostics...",
            })
            try:
                hw = get_android_hardware_telemetry()
                sessions = self.storage.list_conversations(limit=5)
                md = (
                    f"| Parameter | Value |\n"
                    f"| :--- | :--- |\n"
                    f"| **Hardware** | {hw['device']} |\n"
                    f"| **CPU / Arch** | {hw['arch']} (Load: `{hw['loadavg']}`) |\n"
                    f"| **System Memory** | `{hw['mem_avail']}` available / `{hw['mem_total']}` total |\n"
                    f"| **Runtime** | Python `{hw['python']}` + `websockets {websockets.__version__}` |\n"
                    f"| **CLI Binary** | `{AGY_BIN}` (`{'Present' if AGY_BIN.exists() else 'Missing'}`) |\n"
                    f"| **Active Session** | `{cfg.conversation_id or 'New (Unassigned)'}` |\n"
                    f"| **Model / Mode / Effort** | `{cfg.model}` · `{cfg.mode}` · `{cfg.effort}` |\n"
                    f"| **Workspace** | `{cfg.workspace}` |\n"
                    f"| **App Version** | `v{APP_VERSION}` |\n"
                    f"| **AGY CLI Version** | `v{get_agy_version()}` |\n"
                    f"| **Indexed Sessions** | `{len(sessions)}+` in `conversation_summaries.db` |"
                )
                await self.broadcast(
                    {"type": "system_card", "title": "System & Runtime Diagnostics", "markdown": md, "level": "info"}
                )
            finally:
                await self.broadcast({"type": "status", "state": "idle", "detail": "Ready"})
            return True

        if cmd == "/version":
            await self.broadcast({
                "type": "status",
                "state": "running",
                "detail": "Checking runtime versions...",
            })
            try:
                md = (
                    f"| Component | Version |\n"
                    f"| :--- | :--- |\n"
                    f"| **Antigravity Studio App** | `v{APP_VERSION}` |\n"
                    f"| **Antigravity CLI Runtime** | `v{get_agy_version()}` |\n"
                    f"| **Python Backend** | Python `{sys.version.split()[0]}` |\n"
                )
                await self.broadcast(
                    {"type": "system_card", "title": "Version Information", "markdown": md, "level": "info"}
                )
            finally:
                await self.broadcast({"type": "status", "state": "idle", "detail": "Ready"})
            return True

        if cmd == "/help":
            md = (
                "| Slash Command | Action |\n"
                "| :--- | :--- |\n"
                "| `/quota` | View subscription quotas remaining & context window utilization |\n"
                "| `/login` / `/auth` | Sign in with Google to authenticate CLI & refresh models |\n"
                "| `/context` | Display context window breakdown, capacity limit, and token utilization |\n"
                "| `/compact` | Generate a structured context checkpoint to compact conversation history |\n"
                "| `/branch [name]` | Fork current conversation into a new branch and switch to it |\n"
                "| `/models [id]` | Inspect or switch the active model (`gemini-3.8-flash-high`, `claude-sonnet-4-6`, etc.) |\n"
                "| `/mode [mode]` | Switch execution mode (`default`, `accept-edits`, `plan`) |\n"
                "| `/status` | Display device hardware & Antigravity CLI status |\n"
                "| `/clear` | Start a fresh conversation session |\n"
                "| `/exit` | Interrupt any running `agy` worker subprocess |"
            )
            await self.broadcast(
                {"type": "system_card", "title": "Antigravity Mobile Command Reference", "markdown": md, "level": "info"}
            )
            return True

        if cmd == "/exit":
            await self.orchestrator.abort_current_turn()
            await self.broadcast(
                {
                    "type": "system_card",
                    "title": "Worker Reset",
                    "markdown": "Active `agy` subprocess terminated cleanly. Ready for new commands.",
                    "level": "info",
                }
            )
            return True

        return False


async def main() -> None:
    sync_gemini_md_prompt()
    app = AgyWebServer()
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    await app.start_ipc_server()
    try:
        async with serve(
            app.ws_handler,
            HOST,
            PORT,
            process_request=app.handle_http_request,
            max_size=16 * 1024 * 1024,
            ping_interval=20,
            ping_timeout=20,
        ):
            logger.info("Antigravity Mobile UI listening on http://%s:%d/?auth=%s", HOST, PORT, AUTH_TOKEN)
            await stop_event.wait()
            logger.info("Shutting down Antigravity Mobile UI server...")
            await app.orchestrator.abort_current_turn()
    finally:
        await app.stop_ipc_server()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
