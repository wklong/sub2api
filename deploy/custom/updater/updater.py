#!/usr/bin/env python3
"""固定仓库、固定 Compose 服务的宿主机升级器；仅通过 Unix socket 接收请求。"""
import argparse
import base64
import hashlib
import http.server
import json
import os
from pathlib import Path
import re
import socketserver
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

REPO = "wklong/sub2api"
IMAGE = "ghcr.io/" + REPO
SHA = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
ACTIVE = {"queued", "pulling", "recreating", "checking"}


def fetch_json(url, headers=None, timeout=25):
    request = urllib.request.Request(url, headers={"User-Agent": "sub2api-updater", **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read(4 * 1024 * 1024 + 1)
        if len(data) > 4 * 1024 * 1024:
            raise ValueError("远端响应过大")
        return json.loads(data), dict(response.headers), data


def manifest(commit, timeout=25):
    if not SHA.fullmatch(commit):
        raise ValueError("无效的提交哈希")
    headers = {}
    token = os.environ.get("GHCR_TOKEN", "")
    if token:
        basic = base64.b64encode((os.environ.get("GHCR_USERNAME", "wklong") + ":" + token).encode()).decode()
        headers["Authorization"] = "Basic " + basic
    auth, _, _ = fetch_json("https://ghcr.io/token?service=ghcr.io&scope=repository:" + REPO + ":pull", headers, timeout)
    data, _, raw = fetch_json("https://ghcr.io/v2/" + REPO + "/manifests/sha-" + commit, {
        "Authorization": "Bearer " + auth["token"],
        "Accept": "application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json",
    }, timeout)
    if not any(m.get("platform", {}).get("os") == "linux" and
               m.get("platform", {}).get("architecture") == "amd64" for m in data.get("manifests", [])):
        raise ValueError("目标镜像未包含 linux/amd64 平台")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def latest_release():
    deadline = time.monotonic() + 55
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("UPDATE_GITHUB_TOKEN", "")
    if token:
        headers["Authorization"] = "Bearer " + token
    runs, _, _ = fetch_json("https://api.github.com/repos/" + REPO +
        "/actions/workflows/custom-release.yml/runs?branch=main&status=success&per_page=10", headers)
    for run in runs.get("workflow_runs", []):
        commit = run.get("head_sha", "")
        if (run.get("head_branch") != "main" or run.get("event") not in {"push", "workflow_dispatch"} or
                run.get("conclusion") != "success" or
                run.get("head_repository", {}).get("full_name", "").lower() != REPO or not SHA.fullmatch(commit)):
            continue
        try:
            remaining = deadline - time.monotonic()
            if remaining < 1:
                raise ValueError("查询镜像超时，请稍后重试")
            digest = manifest(commit, min(12, remaining / 2))
        except urllib.error.HTTPError as error:
            if error.code == 404:
                continue  # 构建成功但标签被删除或未发布，继续寻找可安装版本。
            raise
        return {"commit": commit, "digest": digest, "image": IMAGE + "@" + digest,
                "published_at": run.get("updated_at", ""), "html_url": run["html_url"]}
    raise ValueError("没有找到已成功发布且可拉取的 main 镜像")


def atomic_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


class Updater:
    def __init__(self, deploy_dir, state_dir):
        self.deploy_dir = Path(deploy_dir).resolve()
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.override = self.deploy_dir / "online-image.json"
        self.state_path = self.state_dir / "status.json"
        self.lock = threading.Lock()
        self.cache_lock = threading.Lock()
        self.cache = None
        self.cache_time = 0
        self.state = {"phase": "idle"}
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if self.state.get("phase") in ACTIVE:
                self.save(phase="failed", message="升级服务曾中断，请检查容器与数据库后重试；未自动回退")

    def save(self, **fields):
        self.state = {**self.state, **fields, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        atomic_json(self.state_path, self.state)

    def latest(self, force=False):
        with self.cache_lock:
            if force or self.cache is None or time.monotonic() - self.cache_time > 60:
                self.cache = latest_release()
                self.cache_time = time.monotonic()
            return dict(self.cache)

    def command(self, args, timeout=900):
        # 无 shell，不执行请求中的命令或路径，不在响应中回显含凭据的命令输出。
        result = subprocess.run(args, cwd=self.deploy_dir, capture_output=True, timeout=timeout)
        if result.returncode:
            raise ValueError("Docker 操作失败（退出码 %d），请检查宿主机 Docker、镜像权限和 Compose 配置" % result.returncode)
        return result.stdout.decode("utf-8").strip()

    def compose(self, args):
        command = ["docker", "compose", "--project-directory", str(self.deploy_dir),
                   "--env-file", str(self.deploy_dir / ".env"), "-f", str(self.deploy_dir / "compose.yml")]
        if self.override.exists():
            command += ["-f", str(self.override)]
        return self.command(command + args)

    def enqueue(self, selection):
        if not self.lock.acquire(blocking=False):
            raise ValueError("已有升级任务进行中")
        try:
            target = self.latest(force=True)
            if (selection.get("commit") != target["commit"] or selection.get("digest") != target["digest"]):
                raise ValueError("目标版本已变化，请刷新版本并重新确认")
            container = self.compose(["ps", "-q", "sub2api"])
            if not container or "\n" in container:
                raise ValueError("必须存在唯一的 sub2api 应用容器")
            installed = self.command(["docker", "inspect", "--format", "{{.Config.Image}}", container])
            if installed == target["image"]:
                raise ValueError("该镜像已经安装，请刷新页面")
            self.save(phase="queued", message="升级已排队", target=target, previous_image="", previous_image_id="")
            threading.Thread(target=self.perform, args=(target,), daemon=True).start()
            return dict(self.state)
        except Exception:
            self.lock.release()
            raise

    def perform(self, target):
        try:
            # 留出时间让后台将排队响应返回给浏览器。
            time.sleep(2)
            self.compose(["config", "--quiet"])
            container = self.compose(["ps", "-q", "sub2api"])
            if not container or "\n" in container:
                raise ValueError("必须存在唯一的 sub2api 应用容器")
            previous = self.command(["docker", "inspect", "--format", "{{.Config.Image}}", container])
            previous_id = self.command(["docker", "inspect", "--format", "{{.Image}}", container])
            self.save(phase="pulling", message="正在拉取已验证的自有镜像",
                      previous_image=previous, previous_image_id=previous_id)
            self.command(["docker", "pull", target["image"]])
            if self.override.exists():
                (self.state_dir / "previous-image.json").write_bytes(self.override.read_bytes())
            atomic_json(self.override, {"services": {"sub2api": {"image": target["image"]}}})
            self.save(phase="recreating", message="正在重建应用容器，服务将短暂中断")
            self.compose(["up", "-d", "--no-deps", "--no-build", "--pull", "never", "sub2api"])
            self.save(phase="checking", message="正在等待应用健康检查")
            self.compose(["up", "-d", "--no-deps", "--no-build", "--pull", "never", "--wait", "--wait-timeout", "300", "sub2api"])
            container = self.compose(["ps", "-q", "sub2api"])
            revision = self.command(["docker", "inspect", "--format",
                                     '{{index .Config.Labels "org.opencontainers.image.revision"}}', container])
            if revision != target["commit"]:
                raise ValueError("容器实际提交与目标版本不一致，请检查部署配置")
            self.save(phase="succeeded", message="升级完成，应用健康检查通过")
        except Exception as error:
            message = str(error) if isinstance(error, ValueError) else "升级执行异常，请检查宿主机状态"
            self.save(phase="failed", message=message + "；未自动回退，数据库迁移可能已执行")
        finally:
            self.lock.release()


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def reply(self, status, data):
        raw = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path)
        try:
            if path.path == "/latest":
                self.reply(200, self.server.updater.latest("force=true" in path.query))
            elif path.path == "/status":
                self.reply(200, dict(self.server.updater.state))
            else:
                self.reply(404, {"message": "接口不存在"})
        except Exception:
            self.reply(503, {"message": "无法验证自有镜像，请检查 GitHub/GHCR 网络、权限和发布状态"})

    def do_POST(self):
        if self.path != "/update":
            self.reply(404, {"message": "接口不存在"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 1024:
                raise ValueError("无效请求长度")
            selection = json.loads(self.rfile.read(size))
            if not isinstance(selection, dict):
                raise ValueError("无效请求")
            self.reply(202, self.server.updater.enqueue(selection))
        except ValueError as error:
            self.reply(409, {"message": str(error)})
        except Exception:
            self.reply(503, {"message": "无法验证目标镜像，未执行升级"})


if hasattr(socketserver, "UnixStreamServer"):
    class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True

        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(60)
            return connection, address


def main():
    if not hasattr(socketserver, "UnixStreamServer"):
        raise SystemExit("升级服务仅支持 Linux 宿主机")

    parser = argparse.ArgumentParser()
    parser.add_argument("--deploy-dir", required=True)
    parser.add_argument("--state-dir", default="/var/lib/sub2api-updater")
    parser.add_argument("--socket", default="/run/sub2api-updater/updater.sock")
    args = parser.parse_args()
    import fcntl
    state_dir = Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    instance_lock = (state_dir / "instance.lock").open("w")
    fcntl.flock(instance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    updater = Updater(args.deploy_dir, args.state_dir)
    path = Path(args.socket)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    with Server(str(path), Handler) as server:
        server.updater = updater
        os.chown(path, 0, 1000)
        os.chmod(path, 0o660)
        server.serve_forever()


if __name__ == "__main__":
    main()
