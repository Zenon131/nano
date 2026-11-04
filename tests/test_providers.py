"""Regression tests for Nano's provider-specific configuration."""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from nano_agent.core import Nano
from nano_agent.providers import ProviderSettings


class ProviderSwitchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="nano-provider-test-")
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.old_cwd = Path.cwd()
        os.chdir(self.directory)
        self.addCleanup(os.chdir, self.old_cwd)
        self.env = patch.dict(os.environ, {
            "HOME": str(self.directory),
            "OPENROUTER_API_KEY": "router-test-key",
            "OPENAI_API_KEY": "openai-test-key",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.discovery = patch("nano_agent.core.ContextImporter.discover_and_import", return_value=[])
        self.discovery.start()
        self.addCleanup(self.discovery.stop)
        self.confirmation = patch("rich.prompt.Confirm.ask", return_value=True)
        self.confirmation.start()
        self.addCleanup(self.confirmation.stop)

    def write_config(self, config):
        (self.directory / ".nano.json").write_text(json.dumps(config), encoding="utf-8")

    def test_declining_provider_switch_keeps_conversation_and_config_unchanged(self):
        self.write_config({"model": "gemma4:31b-cloud", "base_url": "http://localhost:11434/v1", "api_key": "ollama"})
        agent = Nano()
        agent.messages.append({"role": "user", "content": "private conversation"})
        original_config = (self.directory / ".nano.json").read_text()
        with patch("rich.prompt.Confirm.ask", return_value=False) as confirm:
            asyncio.run(agent.run("/provider openrouter"))
        confirm.assert_called_once()
        self.assertEqual(agent.provider, "ollama")
        self.assertEqual(agent.base_url, "http://localhost:11434/v1")
        self.assertEqual(agent.messages[-1]["content"], "private conversation")
        self.assertEqual((self.directory / ".nano.json").read_text(), original_config)

    def test_declining_endpoint_change_does_not_retarget_a_saved_credential(self):
        self.write_config({"model": "model-a", "base_url": "http://127.0.0.1:8000/v1", "api_key": "legacy-key"})
        agent = Nano()
        before = (self.directory / ".nano.json").read_text()
        with patch("nano_agent.core.Prompt.ask", side_effect=["url", "http://127.0.0.1:9000/v1"]):
            with patch("rich.prompt.Confirm.ask", return_value=False) as confirm:
                asyncio.run(agent.run("/config"))
        confirm.assert_called_once()
        self.assertEqual(agent.base_url, "http://127.0.0.1:8000/v1")
        self.assertEqual((self.directory / ".nano.json").read_text(), before)

    def test_malformed_config_is_never_overwritten(self):
        path = self.directory / ".nano.json"
        malformed = '{"model": broken'
        path.write_text(malformed, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "configuration"):
            Nano()
        self.assertEqual(path.read_text(), malformed)

    def test_stale_provider_write_is_rejected_and_selection_rolls_back(self):
        self.write_config({"model": "original", "base_url": "http://localhost:11434/v1", "api_key": "ollama"})
        first, stale = Nano(), Nano()
        asyncio.run(first.run("/model first-change"))
        saved = (self.directory / ".nano.json").read_text()
        with self.assertRaisesRegex(ValueError, "(?i)provider.*changed"):
            asyncio.run(stale.run("/provider openrouter"))
        self.assertEqual((self.directory / ".nano.json").read_text(), saved)
        self.assertEqual(stale.provider, "ollama")
        self.assertEqual(stale.model, "original")
        self.assertEqual(stale.base_url, "http://localhost:11434/v1")
        self.assertEqual(stale.api_key, "ollama")

    @unittest.skipIf(os.name == "nt", "Creating symlinks requires special Windows privileges")
    def test_symlinked_config_save_fails_closed_and_rolls_back_runtime(self):
        target = self.directory / "settings.json"
        target.write_text(json.dumps({"model": "original", "base_url": "http://localhost:11434/v1", "api_key": "ollama"}))
        path = self.directory / ".nano.json"
        path.symlink_to(target.name)
        original_target = target.read_bytes()
        original_link = os.readlink(path)
        agent = Nano(is_subagent=True)
        original_snapshot = agent._provider_snapshot.copy()

        operations = (("model", lambda: agent._select_model("changed")),
                      ("provider", lambda: agent._select_provider("openrouter")))
        for name, operation in operations:
            with self.subTest(operation=name):
                with (patch("nano_agent.core.console.print"),
                      patch("nano_agent.core.os.replace") as replace,
                      self.assertRaisesRegex(ValueError, "(?i)symlink.*no settings were changed")):
                    operation()
                replace.assert_not_called()
                self.assertTrue(path.is_symlink())
                self.assertEqual(os.readlink(path), original_link)
                self.assertTrue(target.read_bytes() == original_target)
                self.assertEqual(agent.provider, "ollama")
                self.assertEqual(agent.model, "original")
                self.assertEqual(agent.base_url, "http://localhost:11434/v1")
                self.assertTrue(agent.api_key == "ollama" and agent.client.api_key == "ollama")
                self.assertTrue(agent._provider_snapshot == original_snapshot)
                self.assertFalse(list(self.directory.glob(".nano-config-*")))

    @unittest.skipIf(os.name == "nt", "Creating symlinks requires special Windows privileges")
    def test_dangling_config_symlink_is_not_replaced(self):
        path = self.directory / ".nano.json"
        target = self.directory / "missing.json"
        path.symlink_to(target.name)
        agent = Nano(is_subagent=True)
        original_model = agent.model
        with self.assertRaisesRegex(ValueError, "(?i)symlink.*no settings were changed"):
            agent._select_model("changed")
        self.assertTrue(path.is_symlink())
        self.assertEqual(os.readlink(path), target.name)
        self.assertFalse(target.exists())
        self.assertEqual(agent.model, original_model)

    @unittest.skipIf(os.name == "nt", "Creating symlinks requires special Windows privileges")
    def test_config_becoming_symlink_during_save_is_not_replaced(self):
        self.write_config({"model": "original", "base_url": "http://localhost:11434/v1", "api_key": "ollama"})
        path = self.directory / ".nano.json"
        target = self.directory / "settings.json"
        target.write_bytes(path.read_bytes())
        before = target.read_bytes()
        agent = Nano(is_subagent=True)
        load = agent._load_config

        def replace_with_link_during_load():
            config = load()
            path.unlink()
            path.symlink_to(target.name)
            return config

        with (patch.object(agent, "_load_config", side_effect=replace_with_link_during_load),
              patch("nano_agent.core.os.replace") as replace,
              self.assertRaisesRegex(ValueError, "(?i)symlink.*no settings were changed")):
            agent._select_model("changed")
        replace.assert_not_called()
        self.assertTrue(path.is_symlink())
        self.assertEqual(os.readlink(path), target.name)
        self.assertTrue(target.read_bytes() == before)
        self.assertEqual(agent.model, "original")
        self.assertFalse(list(self.directory.glob(".nano-config-*")))

    def test_simultaneous_provider_writes_cannot_both_succeed(self):
        self.write_config({"model": "original", "base_url": "http://localhost:11434/v1", "api_key": "ollama"})
        first, second = Nano(), Nano()
        replace_started, second_started, second_replace, release = (threading.Event() for _ in range(4))
        original_replace = os.replace
        errors = []

        def controlled_replace(src, dst):
            if threading.current_thread().name == "first-writer":
                replace_started.set()
                if not release.wait(5):
                    raise RuntimeError("timed out waiting for test")
            else:
                second_replace.set()
            original_replace(src, dst)

        def write(agent, name):
            try:
                if name == "second":
                    second_started.set()
                asyncio.run(agent.run("/model " + name))
            except Exception as error:
                errors.append((name, error))

        with patch("nano_agent.core.os.replace", side_effect=controlled_replace):
            a = threading.Thread(target=write, args=(first, "first"), name="first-writer")
            b = threading.Thread(target=write, args=(second, "second"), name="second-writer")
            a.start()
            try:
                self.assertTrue(replace_started.wait(5))
                b.start()
                self.assertTrue(second_started.wait(5))
                second_replace.wait(0.5)
            finally:
                release.set()
                a.join(5)
                if b.ident is not None:
                    b.join(5)
        self.assertFalse(a.is_alive())
        self.assertFalse(b.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0][0], "second")
        self.assertIsInstance(errors[0][1], ValueError)
        self.assertRegex(str(errors[0][1]), "(?i)provider.*changed")
        self.assertEqual(second.model, "original")
        self.assertEqual(json.loads((self.directory / ".nano.json").read_text())["providers"]["ollama"]["model"], "first")

    def test_provider_settings_race_across_processes(self):
        self.write_config({"model": "original", "base_url": "http://localhost:11434/v1", "api_key": "ollama"})
        script = '''
import sys, time
from pathlib import Path
from unittest.mock import patch
from nano_agent.core import Nano
with patch("nano_agent.core.ContextImporter.discover_and_import", return_value=[]):
    agent = Nano()
Path(sys.argv[1] + ".ready").touch()
start = Path("start")
deadline = time.monotonic() + 15
while not start.exists():
    if time.monotonic() > deadline:
        raise RuntimeError("start signal timed out")
    time.sleep(0.01)
try:
    agent._select_model(sys.argv[1])
except ValueError as error:
    if "Provider settings changed" not in str(error):
        raise
    print("RESULT stale " + agent.model, flush=True)
else:
    print("RESULT won " + agent.model, flush=True)
'''
        env = {"HOME": str(self.directory), "PATH": os.defpath,
               "PYTHONPATH": str(Path(__file__).resolve().parent.parent),
               "PYTHON_DOTENV_DISABLED": "true", "PYTHONDONTWRITEBYTECODE": "1",
               "TMPDIR": tempfile.gettempdir()}
        processes = [subprocess.Popen([sys.executable, "-u", "-c", script, name],
                                      cwd=self.directory, env=env, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True)
                     for name in ("first", "second")]
        try:
            deadline = time.monotonic() + 15
            while not all((self.directory / (name + ".ready")).exists() for name in ("first", "second")):
                self.assertFalse(any(process.poll() is not None for process in processes), "child exited before ready")
                self.assertLess(time.monotonic(), deadline, "children did not become ready")
                time.sleep(0.01)
            (self.directory / "start").touch()
            outputs = [process.communicate(timeout=15) for process in processes]
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
        for process, (_, stderr) in zip(processes, outputs):
            self.assertEqual(process.returncode, 0, stderr)
        results = [line.split() for stdout, _ in outputs for line in stdout.splitlines() if line.startswith("RESULT ")]
        self.assertEqual(len(results), 2)
        self.assertEqual(sum(result[1] == "stale" and result[2] == "original" for result in results), 1)
        winners = [result[2] for result in results if result[1] == "won"]
        self.assertEqual(len(winners), 1)
        self.assertEqual(json.loads((self.directory / ".nano.json").read_text())["providers"]["ollama"]["model"], winners[0])

    def test_unrelated_config_updates_survive_provider_save(self):
        self.write_config({"model": "original", "base_url": "http://localhost:11434/v1", "api_key": "ollama"})
        agent = Nano()
        config = json.loads((self.directory / ".nano.json").read_text())
        config["system_prompt"] = "updated elsewhere"
        self.write_config(config)
        asyncio.run(agent.run("/model changed"))
        self.assertEqual(json.loads((self.directory / ".nano.json").read_text())["system_prompt"], "updated elsewhere")

    def test_config_corrupted_after_start_is_preserved_and_switch_rolls_back(self):
        self.write_config({"model": "gemma4:31b-cloud", "base_url": "http://localhost:11434/v1", "api_key": "ollama"})
        agent = Nano()
        path = self.directory / ".nano.json"
        malformed = '{"model": broken'
        path.write_text(malformed, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "configuration"):
            asyncio.run(agent.run("/provider openrouter"))
        self.assertEqual(agent.provider, "ollama")
        self.assertEqual(agent.base_url, "http://localhost:11434/v1")
        self.assertEqual(path.read_text(), malformed)

    def test_invalid_endpoint_port_is_rejected_without_crash(self):
        self.write_config({"model": "model-a", "base_url": "http://127.0.0.1:8000/v1", "api_key": "legacy-key"})
        agent = Nano()
        before = (self.directory / ".nano.json").read_text()
        with patch("nano_agent.core.Prompt.ask", side_effect=["url", "http://localhost:notaport/v1"]):
            asyncio.run(agent.run("/config"))
        self.assertEqual(agent.base_url, "http://127.0.0.1:8000/v1")
        self.assertEqual((self.directory / ".nano.json").read_text(), before)

    def test_legacy_endpoint_and_credentials_match_previous_client(self):
        cases = [
            ({"model": "legacy"}, {}, "https://api.openai.com/v1", "ollama"),
            ({"model": "legacy", "base_url": None, "api_key": None}, {"OPENAI_BASE_URL": "http://127.0.0.1:8123/v1", "OPENAI_API_KEY": "legacy-env-key"},
             "http://127.0.0.1:8123/v1", "legacy-env-key"),
            ({"model": "legacy"}, {"OPENAI_BASE_URL": "http://127.0.0.1:8123/v1", "OPENAI_API_KEY": "legacy-env-key"},
             "http://127.0.0.1:8123/v1", "legacy-env-key"),
            ({"model": "legacy", "base_url": "http://127.0.0.1:9123/v1"}, {"OPENAI_BASE_URL": "http://127.0.0.1:8123/v1", "OPENAI_API_KEY": "legacy-env-key"},
             "http://127.0.0.1:9123/v1", "legacy-env-key"),
            ({"model": "legacy", "base_url": "http://127.0.0.1:9123/v1"}, {"OPENAI_BASE_URL": "http://127.0.0.1:8123/v1"},
             "http://127.0.0.1:9123/v1", "ollama"),
            ({"model": "legacy", "base_url": "https://openrouter.ai/api/v1", "api_key": "ollama"}, {"OPENAI_API_KEY": "legacy-env-key", "OPENROUTER_API_KEY": "router-test-key"},
             "https://openrouter.ai/api/v1", "router-test-key"),
        ]
        for index, (config, env, expected_url, expected_key) in enumerate(cases):
            with self.subTest(case=index), patch.dict(os.environ, env, clear=True):
                self.write_config(config)
                agent = Nano(is_subagent=True)
                self.assertEqual(agent.base_url, expected_url)
                self.assertTrue(agent.api_key == expected_key, "incorrect selected credential")
                self.assertEqual(str(agent.client.base_url).rstrip("/"), expected_url.rstrip("/"))
                self.assertTrue(agent.client.api_key == expected_key, "incorrect client credential")

    def test_legacy_openrouter_placeholder_uses_environment_through_migration(self):
        self.write_config({"model": "google/gemma-4-31b-it", "base_url": "https://openrouter.ai/api/v1", "api_key": "ollama"})
        env = {"HOME": str(self.directory), "OPENROUTER_API_KEY": "router-test-key", "OPENAI_API_KEY": "openai-test-key"}
        with patch.dict(os.environ, env, clear=True), patch("nano_agent.core.console.print") as output:
            agent = Nano(is_subagent=True)
            self.assertEqual(agent.provider, "openrouter")
            self.assertTrue(agent.api_key == "router-test-key", "OpenRouter credential not selected")
            self.assertTrue(agent.client.api_key == "router-test-key", "client credential not selected")
            with patch.object(agent.client.chat.completions, "create", return_value=iter([])) as create:
                asyncio.run(agent.run("fixture request"))
            create.assert_called_once()
            agent._select_model("google/gemma-4-31b-it")
            saved = (self.directory / ".nano.json").read_text()
            self.assertTrue("router-test-key" not in saved and "openai-test-key" not in saved)
            agent._select_provider("openai")
            self.assertTrue(agent.api_key == "openai-test-key", "OpenAI credential not isolated")
            agent._select_provider("custom")
            self.assertTrue(agent.api_key is None, "custom provider inherited a different provider's credential")
            agent._select_provider("openrouter")
            self.assertTrue(agent.api_key == "router-test-key", "OpenRouter credential not restored")
            reloaded = Nano(is_subagent=True)
            self.assertTrue(reloaded.api_key == "router-test-key", "OpenRouter credential not restored on reload")
            self.assertTrue("router-test-key" not in str(output.call_args_list))
            self.assertTrue("openai-test-key" not in str(output.call_args_list))

    def test_legacy_openrouter_credential_precedence_and_provider_isolation(self):
        router = "https://openrouter.ai/api/v1"
        env = {"OPENROUTER_API_KEY": "router-fixture", "OPENAI_API_KEY": "openai-fixture"}
        cases = [
            ("placeholder", {"base_url": router, "api_key": "ollama"}, env, "router-fixture"),
            ("missing", {"base_url": router}, env, "router-fixture"),
            ("environment-endpoint", {"base_url": None}, {**env, "OPENAI_BASE_URL": router}, "router-fixture"),
            ("null", {"base_url": router, "api_key": None}, env, "router-fixture"),
            ("empty", {"base_url": router, "api_key": ""}, env, "router-fixture"),
            ("explicit", {"base_url": router, "api_key": "saved-fixture"}, env, "saved-fixture"),
            ("missing-router-env-placeholder", {"base_url": router, "api_key": "ollama"}, {"OPENAI_API_KEY": "openai-fixture"}, "ollama"),
            ("empty-router-env-placeholder", {"base_url": router, "api_key": "ollama"}, {"OPENROUTER_API_KEY": "", "OPENAI_API_KEY": "openai-fixture"}, "ollama"),
            ("missing-router-env-missing-key", {"base_url": router}, {"OPENAI_API_KEY": "openai-fixture"}, "openai-fixture"),
            ("empty-router-env-missing-key", {"base_url": router}, {"OPENROUTER_API_KEY": "", "OPENAI_API_KEY": "openai-fixture"}, "openai-fixture"),
            ("no-environment", {"base_url": router}, {}, "ollama"),
            ("openai", {"base_url": "https://api.openai.com/v1"}, env, "openai-fixture"),
            ("custom", {"base_url": "https://example.invalid/v1"}, env, "openai-fixture"),
            ("local", {"base_url": "http://localhost:11434/v1", "api_key": "ollama"}, env, "ollama"),
            ("scoped-openrouter", {"active_provider": "openrouter", "providers": {"openrouter": {"api_key_env": "OTHER_KEY"}}}, {**env, "OTHER_KEY": "scoped-fixture"}, "scoped-fixture"),
        ]
        for name, config, environment, expected in cases:
            with self.subTest(case=name), patch.dict(os.environ, environment, clear=True):
                settings = ProviderSettings(config)
                self.assertTrue(settings.credential() == expected, "incorrect credential precedence")
                reloaded = ProviderSettings(settings.serialized())
                self.assertTrue(reloaded.credential() == expected, "credential changed after serialization")

    def test_legacy_environment_endpoint_routes_and_survives_migration(self):
        requests_seen = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_POST(self):
                size = int(self.headers["Content-Length"])
                body = json.loads(self.rfile.read(size))
                requests_seen.append((self.path, self.headers.get("Authorization"), body["model"]))
                chunk = {"id": "test", "object": "chat.completion.chunk", "created": 0,
                         "model": body["model"], "choices": [{"index": 0,
                         "delta": {"role": "assistant", "content": "LEGACY"}, "finish_reason": None}]}
                payload = ("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}/legacy/v1"
        self.write_config({"model": "legacy-model"})
        with patch.dict(os.environ, {"HOME": str(self.directory), "OPENAI_BASE_URL": url,
                                    "OPENAI_API_KEY": "legacy-test-key"}, clear=True):
            agent = Nano()
            self.assertEqual(asyncio.run(agent.run("hello")), "LEGACY")
            self.assertEqual(requests_seen, [("/legacy/v1/chat/completions", "Bearer legacy-test-key", "legacy-model")])
            asyncio.run(agent.run("/model changed"))
            os.environ["OPENAI_BASE_URL"] = "http://127.0.0.1:1/v1"
            reloaded = Nano()
            self.assertEqual(reloaded.base_url, url)
            self.assertEqual(reloaded.api_key, "legacy-test-key")
            self.assertEqual(reloaded.model, "changed")
            self.assertNotIn("legacy-test-key", (self.directory / ".nano.json").read_text())

    def test_configuring_legacy_profile_key_exits_legacy_credential_mode(self):
        self.write_config({"model": "legacy", "base_url": "http://127.0.0.1:9123/v1"})
        with patch.dict(os.environ, {"MY_CUSTOM_KEY": "new-test-key"}):
            agent = Nano()
            self.assertEqual(agent.api_key, "openai-test-key")
            with patch("nano_agent.core.Prompt.ask", side_effect=["key", "MY_CUSTOM_KEY"]):
                asyncio.run(agent.run("/config"))
            self.assertEqual(agent.api_key, "new-test-key")
            reloaded = Nano()
            self.assertEqual(reloaded.api_key, "new-test-key")
            self.assertNotIn("new-test-key", (self.directory / ".nano.json").read_text())

    def test_new_provider_profiles_do_not_inherit_legacy_key_or_endpoint(self):
        self.write_config({"model": "legacy", "base_url": "https://api.openai.com/v1"})
        with patch.dict(os.environ, {"HOME": str(self.directory), "OPENAI_BASE_URL": "http://127.0.0.1:8123/v1", "OPENAI_API_KEY": "legacy-env-key", "OPENROUTER_API_KEY": "router-test-key"}, clear=True):
            agent = Nano()
            agent._select_provider("openrouter")
            self.assertEqual(agent.base_url, "https://openrouter.ai/api/v1")
            self.assertEqual(agent.api_key, "router-test-key")
            agent._select_provider("custom")
            self.assertIsNone(agent.base_url)
            self.assertIsNone(agent.api_key)

    def test_config_temporary_files_are_gitignored_at_any_depth(self):
        repo = Path(__file__).resolve().parent.parent
        for path in (".nano-config-fixture", "nested/.nano-config-fixture"):
            with self.subTest(path=path):
                result = subprocess.run(
                    ["git", "-c", "core.excludesFile=/dev/null", "check-ignore", "--no-index", "-v", "--", path],
                    cwd=repo, capture_output=True, text=True, check=False,
                )
                self.assertEqual(result.returncode, 0, "configuration temp file must be ignored")
                self.assertTrue(result.stdout.startswith(".gitignore:"), "ignore rule must come from the project")
                self.assertIn(":.nano-config-*\t", result.stdout)

    @unittest.skipIf(os.name == "nt", "POSIX file permissions differ on Windows")
    def test_atomic_config_temporary_file_is_private_and_contains_no_environment_secrets(self):
        self.write_config({"model": "legacy", "base_url": "http://localhost:11434/v1", "api_key": "saved-fixture-secret"})
        path = self.directory / ".nano.json"
        original = path.read_text()
        agent = Nano()
        original_replace = os.replace
        observations = []

        def inspect_replace(src, dst):
            temporary = Path(src)
            content = temporary.read_text()
            observations.append((temporary.name.startswith(".nano-config-"),
                                 temporary.stat().st_mode & 0o777 == 0o600,
                                 path.read_text() == original,
                                 "saved-fixture-secret" in content,
                                 "router-test-key" not in content and "openai-test-key" not in content))
            original_replace(src, dst)

        with patch("nano_agent.core.os.replace", side_effect=inspect_replace):
            agent._select_model("changed")
        self.assertEqual(observations, [(True, True, True, True, True)])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertFalse(list(self.directory.glob(".nano-config-*")))

    @unittest.skipIf(os.name == "nt", "POSIX file permissions differ on Windows")
    def test_migrated_settings_file_is_private(self):
        self.write_config({"model": "gemma4:31b-cloud", "base_url": "http://localhost:11434/v1", "api_key": "legacy-key"})
        (self.directory / ".nano.json").chmod(0o644)
        agent = Nano()
        asyncio.run(agent.run("/provider openrouter"))
        self.assertEqual((self.directory / ".nano.json").stat().st_mode & 0o777, 0o600)

    def test_subagent_inherits_parent_provider_and_confirmation_without_saved_history(self):
        self.write_config({"model": "gemma4:31b-cloud", "base_url": "http://localhost:11434/v1", "api_key": "ollama"})
        callback = lambda name, args: False
        parent = Nano(confirm_callback=callback)
        asyncio.run(parent.run("/provider openrouter"))
        asyncio.run(parent.run("/model google/gemma-4-31b-it"))
        assert parent.session is not None
        parent.session.save_session([{"role": "system", "content": "system"}, {"role": "user", "content": "private saved history"}])
        config = json.loads((self.directory / ".nano.json").read_text())
        config["active_provider"] = "ollama"
        self.write_config(config)
        observed = {}

        async def observe(sub, goal):
            observed.update(provider=sub.provider, model=sub.model, session=sub.session,
                            callback=sub.confirm_callback, messages=list(sub.messages))
            return "done"

        with patch.object(Nano, "run", new=observe):
            result = asyncio.run(parent.registry.tools["spawn_sub_agent"]("test goal"))
        self.assertEqual(result, "done")
        self.assertEqual(observed["provider"], "openrouter")
        self.assertEqual(observed["model"], "google/gemma-4-31b-it")
        self.assertIs(observed["callback"], callback)
        self.assertIsNone(observed["session"])
        self.assertEqual(len(observed["messages"]), 1)
        self.assertNotIn("private saved history", str(observed["messages"]))

    def test_subagent_goals_never_execute_slash_commands_or_change_config(self):
        self.write_config({"model": "legacy", "base_url": "http://127.0.0.1:9123/v1", "api_key": "dummy"})
        before = (self.directory / ".nano.json").read_text()
        goals = ["/provider custom", "/model malicious", "/config", "/help", "/save archive",
                 "/sessions", "/context", "/debug", "normal goal"]
        with patch("nano_agent.core.OpenAI") as client_factory, patch("nano_agent.core.Prompt.ask", return_value="back"):
            create = client_factory.return_value.chat.completions.create
            create.side_effect = lambda **kwargs: iter([])
            parent = Nano(confirm_callback=lambda name, args: False)
            for goal in goals:
                with self.subTest(goal=goal):
                    count = create.call_count
                    result = asyncio.run(parent.registry.tools["spawn_sub_agent"](goal))
                    self.assertEqual(result, "")
                    self.assertEqual(create.call_count, count + 1)
                    self.assertEqual(create.call_args.kwargs["messages"][-2], {"role": "user", "content": goal})
                    self.assertEqual((self.directory / ".nano.json").read_text(), before)
                    self.assertEqual(parent.model, "legacy")
                    self.assertEqual(parent.base_url, "http://127.0.0.1:9123/v1")

    def test_subagent_goal_still_obeys_unsafe_tool_confirmation(self):
        self.write_config({"model": "legacy", "base_url": "http://127.0.0.1:9123/v1", "api_key": "dummy"})
        before = (self.directory / ".nano.json").read_text()
        seen = []
        callback = lambda name, args: seen.append((name, args)) or False
        call = SimpleNamespace(index=0, id="call-test", function=SimpleNamespace(
            name="write_file", arguments=json.dumps({"path": ".nano.json", "content": "not allowed"})))
        first = SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=[call]))])
        final = SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="done", tool_calls=None))])
        with patch("nano_agent.core.OpenAI") as client_factory:
            create = client_factory.return_value.chat.completions.create
            create.side_effect = [iter([first]), iter([final])]
            parent = Nano(confirm_callback=callback)
            result = asyncio.run(parent.registry.tools["spawn_sub_agent"]("/model malicious"))
        self.assertEqual(result, "done")
        self.assertEqual(seen, [("write_file", {"path": ".nano.json", "content": "not allowed"})])
        self.assertEqual((self.directory / ".nano.json").read_text(), before)

    def test_dangerous_builtins_are_marked_unsafe_at_discovery(self):
        agent = Nano()
        for name in ("run_command", "write_file", "edit_file"):
            schema = next(item for item in agent.registry.schemas if item["function"]["name"] == name)
            self.assertTrue(schema["unsafe"], name)

    def test_unsafe_tool_without_confirmation_callback_is_denied(self):
        agent = Nano(is_subagent=True)
        tool = {"function": {"name": "run_command", "arguments": "{}"}}
        execute = AsyncMock(return_value="should not execute")
        with patch.object(agent.registry, "execute", new=execute):
            result = asyncio.run(agent._execute_tool_wrapper(tool))
        self.assertIn("confirmation", result.lower())
        execute.assert_not_awaited()

    def test_unsafe_write_requires_user_approval(self):
        path = self.directory / "approval-test.txt"
        agent = Nano(confirm_callback=lambda name, args: False)
        tool = {"function": {"name": "write_file", "arguments": json.dumps({"path": str(path), "content": "approved"})}}
        denied = asyncio.run(agent._execute_tool_wrapper(tool))
        self.assertIn("denied", denied.lower())
        self.assertFalse(path.exists())
        agent.confirm_callback = lambda name, args: True
        accepted = asyncio.run(agent._execute_tool_wrapper(tool))
        self.assertIn("Successfully wrote", accepted)
        self.assertEqual(path.read_text(), "approved")

    def test_config_url_cannot_silently_change_provider(self):
        self.write_config({"model": "gemma4:31b-cloud", "base_url": "http://localhost:11434/v1", "api_key": "ollama"})
        agent = Nano()
        with patch("nano_agent.core.Prompt.ask", side_effect=["url", "https://openrouter.ai/api/v1"]):
            asyncio.run(agent.run("/config"))
        self.assertEqual(agent.provider, "ollama")
        self.assertEqual(agent.base_url, "http://localhost:11434/v1")
        self.assertEqual(agent.api_key, "ollama")

    def test_custom_endpoint_and_key_environment_are_scoped(self):
        self.write_config({"model": "gemma4:31b-cloud", "base_url": "http://localhost:11434/v1", "api_key": "ollama"})
        with patch.dict(os.environ, {"MY_CUSTOM_KEY": "custom-test-key"}):
            agent = Nano()
            asyncio.run(agent.run("/provider custom"))
            with patch("nano_agent.core.Prompt.ask", side_effect=["url", "http://127.0.0.1:8000/v1"]):
                asyncio.run(agent.run("/config"))
            with patch("nano_agent.core.Prompt.ask", side_effect=["key", "MY_CUSTOM_KEY"]):
                asyncio.run(agent.run("/config"))
            asyncio.run(agent.run("/model custom-model"))
            self.assertEqual(agent.base_url, "http://127.0.0.1:8000/v1")
            self.assertEqual(agent.api_key, "custom-test-key")
            self.assertNotIn("custom-test-key", (self.directory / ".nano.json").read_text())
            asyncio.run(agent.run("/provider ollama"))
            self.assertEqual(agent.api_key, "ollama")
            self.assertEqual(agent.model, "gemma4:31b-cloud")

    def test_ollama_discovery_ignores_malformed_response_entries(self):
        self.write_config({"model": "original", "base_url": "http://localhost:11434/v1", "api_key": "ollama"})
        agent = Nano(is_subagent=True)
        cases = [
            ({"models": [None, 42, "invalid", [], {}, {"name": 7}, {"name": "valid-model"}]}, ["valid-model"]),
            ({"models": None}, []),
            ({"models": {"name": "not-a-list"}}, []),
            (None, []),
            ([], []),
        ]
        with patch("nano_agent.core.requests.get") as get:
            for payload, expected in cases:
                with self.subTest(payload=payload):
                    get.return_value.json.return_value = payload
                    self.assertEqual(agent._get_ollama_models(), expected)
                    get.assert_called_with("http://localhost:11434/api/tags", timeout=2)

    def test_missing_key_does_not_send_or_modify_conversation(self):
        self.write_config({"active_provider": "openrouter", "providers": {"openrouter": {"model": "google/gemma-4-31b-it"}}})
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}):
            agent = Nano()
            before = list(agent.messages)
            with patch.object(agent.client.chat.completions, "create", side_effect=AssertionError("should not call provider")):
                result = asyncio.run(agent.run("hello"))
            self.assertEqual(result, "Provider API key not configured.")
            self.assertEqual(agent.messages, before)

    def test_provider_request_does_not_include_internal_tool_metadata(self):
        self.write_config({"model": "gemma4:31b-cloud", "base_url": "http://localhost:11434/v1", "api_key": "ollama"})
        agent = Nano()
        with patch.object(agent.client.chat.completions, "create", return_value=iter([])) as create:
            asyncio.run(agent.run("test request"))
        tools = create.call_args.kwargs["tools"]
        self.assertGreater(len(tools), 0)
        for tool in tools:
            self.assertEqual(set(tool), {"type", "function"})
        self.assertTrue(any(item["unsafe"] for item in agent.registry.schemas))

    def test_provider_switch_routes_real_stream_requests_and_credentials(self):
        requests_a, requests_b = [], []

        def start_server(record):
            class Handler(BaseHTTPRequestHandler):
                def log_message(self, format, *args):
                    pass

                def do_POST(self):
                    size = int(self.headers["Content-Length"])
                    body = json.loads(self.rfile.read(size))
                    record.append((self.path, self.headers.get("Authorization"), body["model"]))
                    chunk = {"id": "test", "object": "chat.completion.chunk", "created": 0,
                             "model": body["model"], "choices": [{"index": 0,
                             "delta": {"role": "assistant", "content": "READY"}, "finish_reason": None}]}
                    payload = ("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            return f"http://127.0.0.1:{server.server_port}/v1"

        url_a, url_b = start_server(requests_a), start_server(requests_b)
        self.write_config({"active_provider": "openrouter", "providers": {
            "openrouter": {"base_url": url_a, "api_key_env": "OPENROUTER_API_KEY", "model": "google/gemma-4-31b-it"},
            "openai": {"base_url": url_b, "api_key_env": "OPENAI_API_KEY", "model": "openai-test-model"},
        }})
        agent = Nano()
        self.assertEqual(asyncio.run(agent.run("first")), "READY")
        asyncio.run(agent.run("/provider openai"))
        self.assertEqual(asyncio.run(agent.run("second")), "READY")
        self.assertEqual(requests_a, [("/v1/chat/completions", "Bearer router-test-key", "google/gemma-4-31b-it")])
        self.assertEqual(requests_b, [("/v1/chat/completions", "Bearer openai-test-key", "openai-test-model")])
        saved = (self.directory / ".nano.json").read_text()
        self.assertNotIn("router-test-key", saved)
        self.assertNotIn("openai-test-key", saved)

    def test_switching_provider_preserves_its_own_endpoint_and_model(self):
        self.write_config({
            "model": "gemma4:31b-cloud",
            "base_url": "http://localhost:11434/v1",
            "api_key": "ollama",
        })
        agent = Nano()
        with patch.object(agent.client.chat.completions, "create", side_effect=AssertionError("slash command sent to model")):
            asyncio.run(agent.run("/provider openrouter"))
        asyncio.run(agent.run("/model google/gemma-4-31b-it"))
        self.assertEqual(agent.base_url, "https://openrouter.ai/api/v1")
        self.assertEqual(agent.model, "google/gemma-4-31b-it")
        self.assertEqual(agent.api_key, "router-test-key")

        asyncio.run(agent.run("/provider ollama"))
        self.assertEqual(agent.base_url, "http://localhost:11434/v1")
        self.assertEqual(agent.model, "gemma4:31b-cloud")
        self.assertEqual(agent.api_key, "ollama")
        asyncio.run(agent.run("/provider openrouter"))
        self.assertEqual(agent.model, "google/gemma-4-31b-it")

        reloaded = Nano()
        self.assertEqual(reloaded.base_url, "https://openrouter.ai/api/v1")
        self.assertEqual(reloaded.model, "google/gemma-4-31b-it")
        self.assertEqual(reloaded.api_key, "router-test-key")
        self.assertNotIn("router-test-key", (self.directory / ".nano.json").read_text())


if __name__ == "__main__":
    unittest.main()
