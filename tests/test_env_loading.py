"""Regression tests for .env loading.

The load originally lived in config.py on the assumption that everything imports it.
It does not: `scrape_test.brief` and the whole `llm` package never touch config, so
secrets silently failed to load for CLI and library callers while appearing to work
through the API (which imports config for WEB_DIR). It now lives in the package
__init__, the one module every import path runs.

These shell out to a subprocess because .env loading happens at import time.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_isolated(code: str, env_file_contents: str | None, extra_env: dict | None = None):
    """Run code in a subprocess with a temporary project .env, then restore."""
    import os

    env_path = PROJECT_ROOT / ".env"
    had_existing = env_path.exists()
    backup = env_path.read_text() if had_existing else None
    try:
        if env_file_contents is None:
            env_path.unlink(missing_ok=True)
        else:
            env_path.write_text(env_file_contents)
        env = {**os.environ, **(extra_env or {})}
        for key in ("DEEPSEEK_API_KEY", "SCRAPE_TEST_MODEL", "SCRAPE_TEST_LLM"):
            if extra_env is None or key not in extra_env:
                env.pop(key, None)
        return subprocess.run(
            [sys.executable, "-c", textwrap.dedent(code)],
            capture_output=True,
            text=True,
            env=env,
            cwd=PROJECT_ROOT,
            timeout=60,
        )
    finally:
        if backup is not None:
            env_path.write_text(backup)
        else:
            env_path.unlink(missing_ok=True)


class TestDotenvChokepoint:
    def test_brief_entry_point_sees_dotenv(self):
        """The path that was broken: importing brief without touching config."""
        result = run_isolated(
            """
            from scrape_test.brief import active_provider
            p = active_provider()
            print(f"{p.model}|{p.credentials_available()}")
            """,
            "DEEPSEEK_API_KEY=sk-from-dotenv\nSCRAPE_TEST_MODEL=deepseek-flash\n",
        )
        assert result.returncode == 0, result.stderr
        assert "deepseek-flash|True" in result.stdout, result.stdout

    def test_real_env_var_beats_the_file(self):
        result = run_isolated(
            """
            from scrape_test.brief import active_provider
            print(active_provider().model)
            """,
            "SCRAPE_TEST_MODEL=deepseek-flash\n",
            extra_env={"SCRAPE_TEST_MODEL": "deepseek-v4-pro"},
        )
        assert result.returncode == 0, result.stderr
        assert "deepseek-v4-pro" in result.stdout

    def test_absent_dotenv_is_not_an_error(self):
        """A missing .env must degrade to 'no credentials', never crash on import."""
        result = run_isolated(
            """
            from scrape_test.brief import active_provider
            print(f"ok|{active_provider().credentials_available()}")
            """,
            None,
        )
        assert result.returncode == 0, result.stderr
        assert "ok|False" in result.stdout


class TestSecretsAreIgnored:
    def test_gitignore_covers_dotenv(self):
        """This repo is public. A committed .env would publish live keys."""
        patterns = (PROJECT_ROOT / ".gitignore").read_text().splitlines()
        assert ".env" in patterns

    def test_git_actually_ignores_a_dotenv(self):
        env_path = PROJECT_ROOT / ".env"
        had_existing = env_path.exists()
        backup = env_path.read_text() if had_existing else None
        try:
            env_path.write_text("DEEPSEEK_API_KEY=sk-must-never-be-committed\n")
            result = subprocess.run(
                ["git", "check-ignore", "-q", ".env"], cwd=PROJECT_ROOT, timeout=30
            )
            assert result.returncode == 0, "git would track .env - secrets could be pushed"
        finally:
            if backup is not None:
                env_path.write_text(backup)
            else:
                env_path.unlink(missing_ok=True)

    def test_example_file_carries_no_real_key(self):
        text = (PROJECT_ROOT / ".env.example").read_text()
        assert "DEEPSEEK_API_KEY=" in text
        for line in text.splitlines():
            if line.startswith("DEEPSEEK_API_KEY=") or line.startswith("ANTHROPIC_API_KEY="):
                assert line.split("=", 1)[1].strip() == "", f"real value in example: {line}"
