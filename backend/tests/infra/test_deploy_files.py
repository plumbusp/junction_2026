"""Static checks of the deployment files. No Docker, no network, no server."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE = REPO_ROOT / "docker-compose.yml"
DOCKERFILE = REPO_ROOT / "Dockerfile"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
DEPLOY_DIR = REPO_ROOT / "deploy"


def _dockerignore_lines() -> list[str]:
    lines = DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


def _compose_app() -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["app"]


# --- docker-compose.yml -------------------------------------------------------


def test_compose_publishes_no_ports() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    assert not re.search(r"^\s*ports\s*:", text, re.MULTILINE)
    assert "ports" not in _compose_app()


def test_compose_has_traefik_labels() -> None:
    labels = _compose_app()["labels"]
    joined = "\n".join(labels)
    assert "traefik.enable=true" in labels
    assert re.search(r"routers\.\w+\.rule=Host\(`", joined)
    assert re.search(r"routers\.\w+\.entrypoints=websecure$", joined, re.MULTILINE)
    assert re.search(r"routers\.\w+\.tls\.certresolver=letsencrypt$", joined, re.MULTILINE)
    assert re.search(r"loadbalancer\.server\.port=8000$", joined, re.MULTILINE)


def test_compose_env_and_data_stay_outside_the_image() -> None:
    app = _compose_app()
    env_file = app["env_file"][0]["path"]
    assert "/opt/spotthespin/app/.env" in env_file
    assert app["restart"] == "unless-stopped"
    assert app["environment"]["DB_PATH"] == "/app/var/app.db"
    # The volume must not cover /app/data, or it would hide the image's data/dev/.
    assert app["volumes"] == ["app-data:/app/var"]


def test_compose_uses_default_network_like_hello() -> None:
    # Traefik runs with network mode "host" and reaches the default project network.
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert "networks" not in doc
    assert "networks" not in doc["services"]["app"]
    assert not any("traefik.docker.network" in label for label in doc["services"]["app"]["labels"])


# --- .dockerignore --------------------------------------------------------------


def test_dockerignore_excludes_env_and_secrets() -> None:
    lines = _dockerignore_lines()
    for required in (".env", ".env.*", "secrets", ".git", ".claude", "docs", "data/raw"):
        assert required in lines, f"{required} missing from .dockerignore"


def test_dockerignore_deny_list_comes_after_allowlist() -> None:
    # Last match wins, so ".env" must come after "!backend/" to also cover backend/.env.
    lines = _dockerignore_lines()
    assert lines[0] == "*"
    last_allow = max(i for i, ln in enumerate(lines) if ln.startswith("!") and ln != "!.env.example")
    assert lines.index("**/.env") > last_allow
    assert lines.index("**/secrets") > last_allow


def test_dockerignore_includes_data_dev_only() -> None:
    # data/dev/ holds runtime few-shot examples; raw corpora and the frozen eval set stay out.
    lines = _dockerignore_lines()
    reinclude = lines.index("!data/dev/**")
    assert lines.index("data") < lines.index("!data/dev/") < reinclude
    for excluded in ("data/raw", "data/eval", "data/news"):
        assert lines.index(excluded) > reinclude, f"{excluded} must come after the data/dev re-include"


# --- Dockerfile -----------------------------------------------------------------


def test_dockerfile_multistage_nonroot_with_stdlib_healthcheck() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    code = re.sub(r"^\s*#.*$", "", text, flags=re.MULTILINE)
    assert len(re.findall(r"^FROM ", code, re.MULTILINE)) >= 3
    assert "python:3.12-slim" in code
    users = re.findall(r"^USER\s+(\S+)", code, re.MULTILINE)
    assert users and users[-1] not in ("root", "0")
    assert "HEALTHCHECK" in code and "urllib" in code and "curl" not in code
    assert re.search(r"^EXPOSE 8000$", code, re.MULTILINE)
    assert "app.main:app" in code
    assert not re.search(r"\.env\b|secrets/", code)


# --- deploy/ helpers ------------------------------------------------------------

needs_bash = pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None or shutil.which("git") is None,
    reason="needs bash and git (run the suite from WSL or Linux)",
)


def _run_without_confirm(script: str, *args: str, tmp_path: Path) -> subprocess.CompletedProcess:
    # A fake ssh that leaves a marker: a dry run must never call it.
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "ssh_called"
    fake_ssh = fake_bin / "ssh"
    fake_ssh.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 99\n", encoding="utf-8")
    fake_ssh.chmod(0o755)
    env = {**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"}
    result = subprocess.run(
        ["bash", str(DEPLOY_DIR / script), *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert not marker.exists(), f"{script} called ssh without --confirm"
    return result


@needs_bash
@pytest.mark.parametrize("script", ["deploy.sh", "rollback.sh"])
def test_helpers_are_dry_run_without_confirm(script: str, tmp_path: Path) -> None:
    result = _run_without_confirm(script, tmp_path=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "DRY RUN" in result.stdout
    assert "--confirm" in result.stdout


@needs_bash
def test_rollback_rejects_a_non_sha_target(tmp_path: Path) -> None:
    result = _run_without_confirm("rollback.sh", "--to", "x; rm -rf /", tmp_path=tmp_path)
    assert result.returncode != 0
    assert "must be a short git sha" in result.stderr
