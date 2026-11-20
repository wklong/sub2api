import json
import http.client
from pathlib import Path
import tempfile
import socket
import threading
import unittest
from unittest.mock import patch
import urllib.error

import updater


COMMIT = "a" * 40
TARGET = {"commit": COMMIT, "digest": "sha256:" + "b" * 64,
          "image": updater.IMAGE + "@sha256:" + "b" * 64,
          "published_at": "2026-11-21T00:00:00Z", "html_url": "https://github.com/wklong/sub2api/actions/runs/1"}


class UpdaterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = updater.Updater(self.root, self.root / "state")
        self.commands = []

    def tearDown(self):
        self.temp.cleanup()

    def command(self, args, timeout=900):
        self.commands.append(args)
        if "ps" in args:
            return "container-id"
        if args[:2] == ["docker", "inspect"]:
            return COMMIT if "Labels" in args[3] else "previous-image@sha256:" + "c" * 64
        return ""

    def perform(self):
        self.service.lock.acquire()
        self.service.perform(TARGET)

    @patch("updater.time.sleep")
    def test_updates_only_application_and_pins_digest(self, _sleep):
        with patch.object(self.service, "command", side_effect=self.command):
            self.perform()
        self.assertEqual(self.service.state["phase"], "succeeded")
        self.assertIn("previous-image@", self.service.state["previous_image"])
        self.assertEqual(json.loads(self.service.override.read_text())["services"]["sub2api"]["image"], TARGET["image"])
        self.assertIn(["docker", "pull", TARGET["image"]], self.commands)
        for args in self.commands:
            if "up" in args:
                self.assertIn("--no-deps", args)
                self.assertEqual(args[-1], "sub2api")
                self.assertNotIn("postgres", args)
                self.assertIn("--pull", args)
        self.assertFalse(self.service.lock.locked())

    @patch("updater.time.sleep")
    def test_pull_failure_does_not_change_override_or_recreate(self, _sleep):
        self.service.override.write_text('{"services": {"sub2api": {"image": "old"}}}')
        original = self.service.override.read_bytes()
        def command(args, **_kw):
            if args[:2] == ["docker", "pull"]:
                raise ValueError("manifest unknown")
            return self.command(args)
        with patch.object(self.service, "command", side_effect=command):
            self.perform()
        self.assertEqual(self.service.state["phase"], "failed")
        self.assertEqual(self.service.override.read_bytes(), original)
        self.assertFalse(any("up" in args for args in self.commands))

    @patch("updater.time.sleep")
    def test_health_failure_records_previous_and_does_not_auto_rollback(self, _sleep):
        def command(args, **_kw):
            if "--wait" in args:
                raise ValueError("健康检查失败")
            return self.command(args)
        with patch.object(self.service, "command", side_effect=command):
            self.perform()
        self.assertEqual(self.service.state["phase"], "failed")
        self.assertIn("未自动回退", self.service.state["message"])
        self.assertIn("previous-image", self.service.state["previous_image"])
        self.assertEqual(json.loads(self.service.override.read_text())["services"]["sub2api"]["image"], TARGET["image"])

    def test_changed_target_and_injection_rejected_before_mutation(self):
        with patch.object(self.service, "latest", return_value=TARGET):
            for selection in ({}, {"commit": "$(touch /tmp/pwn)", "digest": TARGET["digest"]},
                              {"commit": COMMIT, "digest": "sha256:" + "c" * 64}):
                with self.assertRaises(ValueError):
                    self.service.enqueue(selection)
        self.assertFalse(self.service.override.exists())
        self.assertFalse(self.service.lock.locked())

    def test_concurrent_request_rejected(self):
        self.service.lock.acquire()
        with self.assertRaisesRegex(ValueError, "已有升级任务"):
            self.service.enqueue(TARGET)
        self.service.lock.release()

    def test_repeated_completed_target_is_not_reinstalled(self):
        with patch.object(self.service, "latest", return_value=TARGET), \
                patch.object(self.service, "compose", return_value="container-id"), \
                patch.object(self.service, "command", return_value=TARGET["image"]):
            with self.assertRaisesRegex(ValueError, "已经安装"):
                self.service.enqueue(TARGET)
        self.assertFalse(self.service.lock.locked())
        self.assertFalse(self.service.override.exists())

    def test_restart_marks_interrupted_task_unknown_failure(self):
        self.service.save(phase="checking", target=TARGET, previous_image="old")
        restarted = updater.Updater(self.root, self.root / "state")
        self.assertEqual(restarted.state["phase"], "failed")
        self.assertEqual(restarted.state["previous_image"], "old")

    def test_latest_uses_successful_main_run_with_existing_manifest(self):
        def run(sha, **extra):
            return {"head_sha": sha, "head_branch": "main", "event": "push", "conclusion": "success",
                    "head_repository": {"full_name": updater.REPO}, "html_url": "run", **extra}
        runs = [run("x" * 40), run("b" * 40, head_branch="test"), run("c" * 40), run(COMMIT)]
        missing = urllib.error.HTTPError("url", 404, "missing", {}, None)
        with patch("updater.fetch_json", return_value=({"workflow_runs": runs}, {}, b"")), \
                patch("updater.manifest", side_effect=[missing, TARGET["digest"]]) as manifest:
            self.assertEqual(updater.latest_release()["commit"], COMMIT)
            self.assertEqual(manifest.call_count, 2)

    def test_manifest_rejects_wrong_platform_and_computes_content_digest(self):
        token = ({"token": "test"}, {}, b"")
        raw = b'actual manifest bytes'
        data = {"manifests": [{"platform": {"os": "linux", "architecture": "arm64"}}]}
        with patch("updater.fetch_json", side_effect=[token, (data, {}, raw)]):
            with self.assertRaisesRegex(ValueError, "amd64"):
                updater.manifest(COMMIT)
        data["manifests"][0]["platform"]["architecture"] = "amd64"
        with patch("updater.fetch_json", side_effect=[token, (data, {}, raw)]):
            self.assertEqual(updater.manifest(COMMIT), "sha256:" + updater.hashlib.sha256(raw).hexdigest())

    @unittest.skipUnless(hasattr(updater, "Server"), "需要 Unix socket")
    def test_unix_http_status_and_invalid_update_are_bounded(self):
        # Unix socket 必须位于 Linux 文件系统，Windows 挂载目录不支持该文件类型。
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            path = str(Path(directory) / "updater.sock")
            with updater.Server(path, updater.Handler) as server:
                server.updater = self.service
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    def request(method, endpoint, body=None):
                        client = http.client.HTTPConnection("localhost", timeout=3)
                        client.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                        client.sock.connect(path)
                        client.request(method, endpoint, body=body)
                        response = client.getresponse()
                        result = response.status, json.loads(response.read())
                        client.close()
                        return result
                    self.assertEqual(request("GET", "/status"), (200, {"phase": "idle"}))
                    self.assertEqual(request("POST", "/update", "x" * 1025)[0], 409)
                    self.assertEqual(request("POST", "/arbitrary-command", "{}")[0], 404)
                    with patch.object(self.service, "latest", return_value=TARGET):
                        self.assertEqual(request("POST", "/update", "{}")[0], 409)
                    self.assertFalse(self.service.lock.locked())
                finally:
                    server.shutdown()
                    thread.join()


if __name__ == "__main__":
    unittest.main()
