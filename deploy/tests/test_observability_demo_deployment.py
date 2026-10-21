"""通过真实 Compose 合并检查受控遥测演示的采集身份与隔离边界。

所有插值都是明确标注的测试值，不读取实际 .env、连接 Docker 引擎或启动业务。
正负对照覆盖的是最终配置语义；Linux 已加载的 AppArmor 策略及真实采集效果，
仍须由独立运行验收证明，不能用本测试替代。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_agentscope_deployment import BASE_OVERLAYS, DEPLOY, FIXTURE_ENV


DEMO_SERVICES = {"observability-catalog", "observability-gateway"}
EXPECTED_SECURITY = {"no-new-privileges:true", "apparmor=drop-diagnostic-target"}


def boundary_errors(baseline: dict, merged: dict) -> list[str]:
    """验证控制器与目标的配对关系，不通过增加权限绕过身份读取失败。"""
    errors: list[str] = []

    def require(ok: object, code: str) -> None:
        if not ok:
            errors.append(code)

    before = baseline["services"]
    services = merged["services"]
    require(set(services) - set(before) == DEMO_SERVICES, "UNEXPECTED_SERVICE_ADDED")
    require(set(before) <= set(services), "EXISTING_SERVICE_REMOVED")
    for name, service in before.items():
        require(services.get(name) == service, "EXISTING_SERVICE_CHANGED:" + name)
    for resource in ("volumes", "networks", "secrets", "configs"):
        require(merged.get(resource, {}) == baseline.get(resource, {}), "RESOURCE_CHANGED:" + resource)

    agent = services.get("agent", {})
    require(agent.get("user") == "10001:10001", "AGENT_UID_CHANGED")
    require(set(agent.get("security_opt", [])) == {"no-new-privileges:true", "apparmor=drop-agent-controller"}, "AGENT_CONTROLLER_ROLE_MISSING")
    require(not agent.get("cap_add") and set(agent.get("cap_drop", [])) == {"ALL"}, "AGENT_CAPABILITY_WIDENED")

    for name in sorted(DEMO_SERVICES):
        service = services.get(name, {})
        prefix = name + ":"
        require(service.get("user") == agent.get("user") == "10001:10001", prefix + "TARGET_UID_MISMATCH")
        require(service.get("pid") == "service:agent", prefix + "PID_NAMESPACE_NOT_AGENT")
        # docker-default 与 unconfined 均不是已登记目标角色：同 UID 和共享 PID
        # 不会自动绕过 AppArmor 对 /proc/PID/exe 的 ptrace(read) 对端检查。
        require(set(service.get("security_opt", [])) == EXPECTED_SECURITY, prefix + "TARGET_PEER_ROLE_MISSING")
        require(not service.get("privileged"), prefix + "PRIVILEGED")
        require(set(service.get("cap_drop", [])) == {"ALL"}, prefix + "CAP_DROP_MISSING")
        require(service.get("read_only") is True, prefix + "ROOTFS_WRITABLE")
        for field in ("ports", "expose", "cap_add", "devices", "device_cgroup_rules", "volumes", "volumes_from", "secrets", "configs", "environment", "env_file", "group_add", "sysctls", "links", "external_links", "extra_hosts"):
            require(not service.get(field), prefix + "UNEXPECTED_ACCESS:" + field)
        for field in ("network_mode", "ipc", "uts", "userns_mode"):
            require(not service.get(field), prefix + "UNEXPECTED_NAMESPACE:" + field)
        require(set(service.get("networks", {})) == {"control"}, prefix + "NETWORK_CHANGED")
        require(service.get("depends_on", {}).get("agent", {}).get("condition") == "service_started", prefix + "AGENT_DEPENDENCY_MISSING")
        require(service.get("entrypoint") == ["/opt/drop-tools/venv/bin/python", "/opt/drop-observability-demo/server.py"], prefix + "ENTRYPOINT_CHANGED")
        require(service.get("build", {}).get("network") == "none", prefix + "BUILD_NETWORK_ENABLED")
        require(service.get("init") is True, prefix + "CHILD_REAPING_MISSING")
        require(0 < int(service.get("mem_limit", 0)) <= 192 * 1024**2, prefix + "MEMORY_UNBOUNDED")
        require(service.get("memswap_limit") == service.get("mem_limit"), prefix + "SWAP_UNBOUNDED")
        require(0 < float(service.get("cpus", 0)) <= 0.5, prefix + "CPU_UNBOUNDED")
        require(0 < service.get("pids_limit", 0) <= 32, prefix + "PIDS_UNBOUNDED")
        require(service.get("tmpfs") == ["/tmp:size=32m,mode=1777,nosuid,nodev,noexec"], prefix + "SCRATCH_BOUNDARY_CHANGED")

    catalog = services.get("observability-catalog", {})
    gateway = services.get("observability-gateway", {})
    require(catalog.get("command") == ["--role", "catalog", "--port", "8131"], "CATALOG_ROLE_CHANGED")
    require(gateway.get("command") == ["--role", "gateway", "--port", "8130", "--downstream", "http://observability-catalog:8131"], "GATEWAY_UPSTREAM_CHANGED")
    require(gateway.get("depends_on", {}).get("observability-catalog", {}).get("condition") == "service_healthy", "CATALOG_HEALTH_DEPENDENCY_MISSING")
    return errors


class ObservabilityDemoDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.docker = shutil.which("docker")
        if not cls.docker:
            raise unittest.SkipTest("需要 Docker Compose CLI；仅生成配置，不需要容器引擎")
        cls.temporary = tempfile.TemporaryDirectory(prefix="drop-observability-compose-fixture-")
        cls.fixture_dir = Path(cls.temporary.name)
        fixture_env = dict(FIXTURE_ENV)
        fixture_env["AI_OBSERVABILITY_HOST_CONFIG"] = "/explicit-imported-fixture/observability.json"
        cls.env_file = cls.fixture_dir / "explicit-imported-fixture.env"
        cls.env_file.write_text("\n".join(key + "=" + value for key, value in fixture_env.items()) + "\n", encoding="utf-8")
        # 清除宿主业务环境，也将 DOCKER_CONFIG 放入空临时目录；config 不读真实认证。
        cls.child_env = {key: value for key, value in os.environ.items() if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA"}}
        cls.child_env.update(fixture_env)
        cls.child_env["DOCKER_CONFIG"] = str(cls.fixture_dir / "docker-config")
        cls.child_env["DOCKER_CLI_HINTS"] = "false"
        version = subprocess.run([cls.docker, "compose", "version", "--short"], env=cls.child_env, capture_output=True, text=True, timeout=20)
        if version.returncode:
            cls.temporary.cleanup()
            raise unittest.SkipTest("需要 Docker Compose 插件；本检查不启动任何容器")
        cls.compose_version = version.stdout.strip()
        try:
            cls.baseline = cls.compose(False)
            cls.merged = cls.compose(True)
        except BaseException:
            cls.temporary.cleanup()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @classmethod
    def compose(cls, include_demo: bool, override: dict | None = None, *, override_text: str | None = None) -> dict:
        files = list(BASE_OVERLAYS) + ["compose.agentscope.yml", "compose.diagnostic-knowledge.yml", "compose.agent-expansion.yml"]
        if include_demo:
            files.append("compose.agent-expansion-demo.yml")
        command = [cls.docker, "compose", "--project-name", "txdrop-explicit-imported-fixture", "--project-directory", str(cls.fixture_dir), "--env-file", str(cls.env_file)]
        for filename in files:
            command += ["-f", str(DEPLOY / filename)]
        if override is not None or override_text is not None:
            path = cls.fixture_dir / "explicit-imported-negative-overlay.yaml"
            path.write_text(override_text if override_text is not None else json.dumps(override), encoding="utf-8")
            command += ["-f", str(path)]
        command += ["--profile", "demo", "config", "--format", "json"]
        result = subprocess.run(command, cwd=cls.fixture_dir, env=cls.child_env, capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise AssertionError("测试值 Compose 合并失败：" + result.stderr[-2500:])
        return json.loads(result.stdout)

    def test_actual_complete_merge_only_adds_two_bounded_collectable_targets(self):
        self.assertEqual(boundary_errors(self.baseline, self.merged), [])
        self.assertGreaterEqual(len(self.baseline["services"]), 27)

    def test_actual_merge_rejects_removed_or_wrong_target_peer_role(self):
        # !override 确实替换最终 security_opt，避免 Compose 的列表合并
        # 意外留下正确角色，使“删除角色”的负对照形同虚设。
        for name in sorted(DEMO_SERVICES):
            for options in (
                ["no-new-privileges:true"],
                ["no-new-privileges:true", "apparmor=docker-default"],
                ["no-new-privileges:true", "apparmor=unconfined"],
                ["no-new-privileges:true", "apparmor=drop-agent-controller"],
            ):
                with self.subTest(service=name, options=options):
                    text = "services:\n  " + name + ":\n    security_opt: !override " + json.dumps(options) + "\n"
                    candidate = self.compose(True, override_text=text)
                    self.assertEqual(candidate["services"][name]["security_opt"], options)
                    self.assertIn(name + ":TARGET_PEER_ROLE_MISSING", boundary_errors(self.baseline, candidate))

    def test_actual_merge_rejects_controller_role_or_target_identity_mismatch(self):
        candidate = self.compose(True, override_text="services:\n  agent:\n    security_opt: !override [no-new-privileges:true, apparmor=docker-default]\n")
        self.assertIn("AGENT_CONTROLLER_ROLE_MISSING", boundary_errors(self.baseline, candidate))
        for name in sorted(DEMO_SERVICES):
            for fields, code in (
                ({"pid": "host"}, "PID_NAMESPACE_NOT_AGENT"),
                ({"pid": "service:collector-helper"}, "PID_NAMESPACE_NOT_AGENT"),
                ({"user": "0:0"}, "TARGET_UID_MISMATCH"),
            ):
                with self.subTest(service=name, fields=fields):
                    candidate = self.compose(True, {"services": {name: fields}})
                    self.assertIn(name + ":" + code, boundary_errors(self.baseline, candidate))

    def test_actual_merge_rejects_extra_privilege_ports_and_unbounded_resources(self):
        cases = (
            ({"ports": ["18131:8131"]}, "UNEXPECTED_ACCESS:ports"),
            ({"cap_add": ["SYS_PTRACE"]}, "UNEXPECTED_ACCESS:cap_add"),
            ({"privileged": True}, "PRIVILEGED"),
            ({"volumes": ["/var/run/docker.sock:/var/run/docker.sock:ro"]}, "UNEXPECTED_ACCESS:volumes"),
            ({"secrets": ["database_url"]}, "UNEXPECTED_ACCESS:secrets"),
            ({"environment": {"AI_API_KEY_FILE": "/run/secrets/ai_api_key"}}, "UNEXPECTED_ACCESS:environment"),
            ({"read_only": False}, "ROOTFS_WRITABLE"),
            ({"networks": ["model-egress"]}, "NETWORK_CHANGED"),
            ({"mem_limit": "256m", "memswap_limit": "256m"}, "MEMORY_UNBOUNDED"),
            ({"memswap_limit": "256m"}, "SWAP_UNBOUNDED"),
            ({"cpus": 1}, "CPU_UNBOUNDED"),
            ({"pids_limit": 64}, "PIDS_UNBOUNDED"),
        )
        for fields, code in cases:
            with self.subTest(fields=fields):
                candidate = self.compose(True, {"services": {"observability-catalog": fields}})
                self.assertIn("observability-catalog:" + code, boundary_errors(self.baseline, candidate))

    def test_actual_merge_rejects_existing_service_or_resource_changes(self):
        candidate = self.compose(True, {"services": {"collector-helper": {"cap_add": ["SYS_ADMIN"]}}})
        self.assertIn("EXISTING_SERVICE_CHANGED:collector-helper", boundary_errors(self.baseline, candidate))
        # Compose 会裁掉未引用的新卷；改动已引用卷才能真正进入最终模型。
        candidate = self.compose(True, {"volumes": {"pg_data": {"labels": {"explicit-imported-fixture": "unreviewed-change"}}}})
        self.assertIn("RESOURCE_CHANGED:volumes", boundary_errors(self.baseline, candidate))


if __name__ == "__main__":
    unittest.main(verbosity=2)
