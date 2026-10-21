"""不连接容器引擎、不读取真实 .env/密钥，验证最终 Compose 合并结果。

插值环境值全部是明确导入的测试夹具。这些检查证明生成配置的权限边界，
不能证明 Linux 运行时 UID、socket 对端身份、出站防火墙或生产健康状态。
"""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
BASE_OVERLAYS = (
    "compose.yml",
    "compose.tools.yml",
    "compose.helper.yml",
    "compose.helper-ebpf.yml",
    "compose.https.yml",
    "compose.observability.yml",
    "compose.tencent.yml",
    "compose.agent-v2.yml",
    "compose.demo.yml",
    "compose.saber.yml",
    "compose.helper-targets.yml",
    "compose.helper-uprobes.yml",
    "compose.completion.yml",
)
FIXTURE_ENV = {
    "DROP_HELPER_SPOOL_ROOT": "/explicit-imported-fixture/helper-spool",
    "PUBLIC_URL": "https://explicit-imported-fixture.invalid",
    "RELEASE_TAG": "explicit-imported-fixture",
    "COMPOSE_PROFILES": "demo",
}


def mounts(service: dict) -> dict:
    return {item["target"]: item for item in service.get("volumes", [])}


def names(items: list[dict]) -> set[str]:
    return {item["source"] for item in items}


def boundary_errors(baseline: dict, merged: dict) -> list[str]:
    """根据 Docker 规范化后的模型验证权限语义边界，而不是只匹配 YAML 文本。"""
    errors = []

    def require(ok: object, code: str) -> None:
        if not ok:
            errors.append(code)

    before, services = baseline["services"], merged["services"]
    added = set(services) - set(before)
    require(added == {"agent-runtime", "agent-runtime-permission-init"}, "UNEXPECTED_SERVICE_ADDED")
    require(set(before) <= set(services), "EXISTING_SERVICE_REMOVED")
    for name, service in before.items():
        if name != "api":
            require(services.get(name) == service, "UNRELATED_SERVICE_CHANGED:" + name)
    require(set(merged.get("volumes", {})) - set(baseline.get("volumes", {})) == {"ai_runner_socket", "ai_tool_socket"}, "UNEXPECTED_VOLUME_CHANGE")
    require(set(merged.get("networks", {})) - set(baseline.get("networks", {})) == {"model-egress"}, "UNEXPECTED_NETWORK_CHANGE")
    for kind in ("volumes", "networks", "secrets", "configs"):
        for name, item in baseline.get(kind, {}).items():
            require(merged.get(kind, {}).get(name) == item, "EXISTING_RESOURCE_CHANGED:" + kind + ":" + name)

    runtime = services.get("agent-runtime", {})
    init = services.get("agent-runtime-permission-init", {})
    api = services.get("api", {})
    require(runtime.get("user") == "10002:10003", "RUNTIME_IDENTITY_CHANGED")
    require(set(runtime.get("networks", {})) == {"model-egress"}, "RUNTIME_NETWORK_REACHES_CONTROL")
    egress_peers = {name for name, item in services.items() if "model-egress" in item.get("networks", {})}
    require(egress_peers == {"agent-runtime"}, "EGRESS_NETWORK_SHARED_WITH_BUSINESS")
    require(not merged.get("networks", {}).get("model-egress", {}).get("internal", False), "MODEL_EGRESS_DISABLED")
    for field in ("ports", "expose", "cap_add", "devices", "device_cgroup_rules", "volumes_from", "external_links", "links"):
        require(not runtime.get(field), "RUNTIME_UNEXPECTED_PRIVILEGE:" + field)
    for field in ("pid", "ipc", "uts", "network_mode", "userns_mode"):
        require(not runtime.get(field), "RUNTIME_HOST_OR_SHARED_NAMESPACE:" + field)
    require(not runtime.get("privileged"), "RUNTIME_PRIVILEGED")
    require(runtime.get("read_only") is True, "RUNTIME_ROOTFS_WRITABLE")
    require(set(runtime.get("cap_drop", [])) == {"ALL"}, "RUNTIME_CAP_DROP_MISSING")
    require(set(runtime.get("security_opt", [])) == {"no-new-privileges:true"}, "RUNTIME_SECURITY_POLICY_CHANGED")
    require(names(runtime.get("secrets", [])) == {"ai_api_key"}, "RUNTIME_BUSINESS_SECRET_OR_MODEL_SECRET_MISSING")
    require(not runtime.get("configs"), "RUNTIME_UNEXPECTED_CONFIG_MOUNT")
    allowed_environment = {
        "AGENTSCOPE_RUNNER_SOCKET", "AGENTSCOPE_GATEWAY_SOCKET", "AI_ENDPOINT", "AI_MODEL",
        "AI_API_KEY_FILE", "OTEL_SDK_DISABLED", "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED",
    }
    require(set(runtime.get("environment", {})) <= allowed_environment, "RUNTIME_BUSINESS_OR_UNREVIEWED_ENVIRONMENT")
    require(runtime.get("environment", {}).get("AI_API_KEY_FILE") == "/run/secrets/ai_api_key", "MODEL_KEY_NOT_FILE_ONLY")
    require(not runtime.get("environment", {}).get("AI_API_KEY"), "INLINE_MODEL_KEY")
    expected_runtime_mounts = {
        "/run/drop-ai-runner": ("ai_runner_socket", False),
        "/run/drop-ai-tools": ("ai_tool_socket", True),
    }
    current_mounts = mounts(runtime)
    require(set(current_mounts) == set(expected_runtime_mounts), "RUNTIME_FORBIDDEN_OR_MISSING_MOUNT")
    for target, (source, read_only) in expected_runtime_mounts.items():
        actual = current_mounts.get(target, {})
        require(actual.get("type") == "volume" and actual.get("source") == source and bool(actual.get("read_only", False)) == read_only, "RUNTIME_SOCKET_DIRECTION:" + target)
    require(0 < int(runtime.get("mem_limit", 0)) <= 512 * 1024**2, "RUNTIME_MEMORY_UNBOUNDED")
    require(runtime.get("memswap_limit") == runtime.get("mem_limit"), "RUNTIME_SWAP_NOT_BOUNDED")
    require(0 < float(runtime.get("cpus", 0)) <= 0.5, "RUNTIME_CPU_UNBOUNDED")
    require(0 < runtime.get("pids_limit", 0) <= 64, "RUNTIME_PIDS_UNBOUNDED")
    require(runtime.get("init") is True, "RUNTIME_CHILD_REAPING_MISSING")
    require(runtime.get("depends_on", {}).get("agent-runtime-permission-init", {}).get("condition") == "service_completed_successfully", "RUNTIME_SOCKET_INIT_NOT_REQUIRED")

    require(init.get("network_mode") == "none" and not init.get("networks"), "INIT_HAS_NETWORK")
    require(init.get("user") == "0:0" and init.get("restart") == "no", "INIT_LIFECYCLE_CHANGED")
    # 未授予 FSETID 时，root 必须属于目标组，否则 Linux 可能在 chmod 时清除 SGID；
    # 保持消费者目录不可写，不通过增加 capability 绕过。
    require(set(str(group) for group in init.get("group_add", [])) == {"10003"}, "INIT_SOCKET_SUPPLEMENTARY_GROUP_INVALID")
    require(set(init.get("cap_drop", [])) == {"ALL"} and set(init.get("cap_add", [])) <= {"CHOWN", "DAC_OVERRIDE", "FOWNER"}, "INIT_CAPABILITY_WIDENED")
    require(not init.get("secrets") and not init.get("ports") and not init.get("privileged"), "INIT_HAS_SECRETS_OR_PRIVILEGE")
    require(init.get("read_only") is True, "INIT_ROOTFS_WRITABLE")
    init_mounts = mounts(init)
    require(set(init_mounts) == {"/runner", "/tools"} and {item.get("source") for item in init_mounts.values()} == {"ai_runner_socket", "ai_tool_socket"}, "INIT_CAN_ACCESS_BUSINESS_MOUNTS")
    require(all(item.get("type") == "volume" and not item.get("read_only", False) for item in init_mounts.values()), "INIT_SOCKET_MOUNT_NOT_WRITABLE")
    init_command = " ".join(init.get("command", []))
    approved_init = "chown 10002:10003 /runner && chmod 2750 /runner && chown 10001:10003 /tools && chmod 2750 /tools"
    require(init_command.split() == approved_init.split(), "SOCKET_DIRECTORY_PRODUCER_OWNERSHIP_WRONG")
    require(0 < int(init.get("mem_limit", 0)) <= 48 * 1024**2 and init.get("memswap_limit") == init.get("mem_limit"), "INIT_MEMORY_UNBOUNDED")
    require(0 < float(init.get("cpus", 0)) <= 0.1 and 0 < init.get("pids_limit", 0) <= 16, "INIT_CPU_OR_PIDS_UNBOUNDED")

    api_mounts = mounts(api)
    require(api.get("user") == before["api"].get("user") == "10001:10001", "API_IDENTITY_CHANGED")
    require("10003" in api.get("group_add", []), "API_SOCKET_GROUP_MISSING")
    for target, (source, read_only) in {
        "/run/drop-ai-runner": ("ai_runner_socket", True),
        "/run/drop-ai-tools": ("ai_tool_socket", False),
    }.items():
        actual = api_mounts.get(target, {})
        require(actual.get("type") == "volume" and actual.get("source") == source and bool(actual.get("read_only", False)) == read_only, "API_SOCKET_DIRECTION:" + target)
    for target, mount in mounts(before["api"]).items():
        require(api_mounts.get(target) == mount, "API_EXISTING_MOUNT_CHANGED:" + target)
    require(set(api_mounts) == set(mounts(before["api"])) | {"/run/drop-ai-runner", "/run/drop-ai-tools"}, "API_UNEXPECTED_NEW_MOUNT")
    code_executor = api_mounts.get("/run/drop-code-executor", {})
    require(code_executor.get("read_only") is True and code_executor.get("type") == "bind", "API_CODE_EXECUTOR_SOCKET_NOT_READ_ONLY")
    require(api.get("networks") == before["api"].get("networks"), "API_NETWORK_CHANGED")
    require(api.get("environment", {}).get("AI_AGENT_BACKEND") == "agentscope", "API_NOT_USING_FRAMEWORK")
    allowed_environment_changes = {"AI_AGENT_BACKEND", "AGENTSCOPE_RUNNER_SOCKET", "AGENTSCOPE_GATEWAY_SOCKET", "AI_API_KEY_FILE", "AI_ENDPOINT", "AI_MODEL"}
    for key in set(before["api"].get("environment", {})) | set(api.get("environment", {})):
        if key not in allowed_environment_changes:
            require(api.get("environment", {}).get(key) == before["api"].get("environment", {}).get(key), "API_UNRELATED_ENVIRONMENT_CHANGED:" + key)
    for dependency, condition in (("agent-runtime", "service_healthy"), ("agent-runtime-permission-init", "service_completed_successfully")):
        require(api.get("depends_on", {}).get(dependency, {}).get("condition") == condition, "API_RUNTIME_DEPENDENCY_MISSING:" + dependency)
    allowed_api_changes = {"image", "environment", "volumes", "group_add", "depends_on", "secrets"}
    for field in set(before["api"]) | set(api):
        if field not in allowed_api_changes:
            require(api.get(field) == before["api"].get(field), "API_UNRELATED_FIELD_CHANGED:" + field)
    prior_secrets = names(before["api"].get("secrets", [])) - {"ai_api_key"}
    require(names(api.get("secrets", [])) - {"ai_api_key"} == prior_secrets, "API_BUSINESS_SECRET_CHANGED")
    return errors


class AgentScopeDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.docker = shutil.which("docker")
        if not cls.docker:
            raise unittest.SkipTest("Docker CLI unavailable; run this test where docker compose config is installed; no engine needed")
        cls.temporary = tempfile.TemporaryDirectory(prefix="drop-agentscope-compose-fixture-")
        cls.fixture_dir = Path(cls.temporary.name)
        cls.env_file = cls.fixture_dir / "explicit-imported-fixture.env"
        cls.env_file.write_text("\n".join(key + "=" + value for key, value in FIXTURE_ENV.items()) + "\n", encoding="utf-8")
        cls.child_env = {key: value for key, value in os.environ.items() if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA"}}
        cls.child_env.update(FIXTURE_ENV)
        cls.child_env["DOCKER_CONFIG"] = str(cls.fixture_dir / "docker-config")
        cls.child_env["DOCKER_CLI_HINTS"] = "false"
        version = subprocess.run([cls.docker, "compose", "version", "--short"], env=cls.child_env, capture_output=True, text=True, timeout=20)
        if version.returncode:
            cls.temporary.cleanup()
            raise unittest.SkipTest("Docker Compose plugin unavailable; config-only remote entry is this same unittest module")
        cls.compose_version = version.stdout.strip()
        cls.baseline = cls.compose(False)
        cls.merged = cls.compose(True)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @classmethod
    def compose(cls, agentscope: bool, override: dict | None = None, *, override_text: str | None = None) -> dict:
        command = [cls.docker, "compose", "--project-name", "txdrop-explicit-imported-fixture", "--project-directory", str(cls.fixture_dir), "--env-file", str(cls.env_file)]
        files = list(BASE_OVERLAYS) + (["compose.agentscope.yml"] if agentscope else [])
        for filename in files:
            command += ["-f", str(DEPLOY / filename)]
        if override is not None or override_text is not None:
            filename = cls.fixture_dir / "explicit-imported-negative-overlay.yaml"
            filename.write_text(override_text if override_text is not None else json.dumps(override), encoding="utf-8")
            command += ["-f", str(filename)]
        command += ["--profile", "demo", "config", "--format", "json"]
        result = subprocess.run(command, cwd=cls.fixture_dir, env=cls.child_env, capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise AssertionError("Fixture-only Docker Compose config failed: " + result.stderr[-2500:])
        return json.loads(result.stdout)

    def test_actual_complete_merge_preserves_existing_services_and_boundaries(self):
        self.assertEqual(boundary_errors(self.baseline, self.merged), [])
        self.assertGreaterEqual(len(self.baseline["services"]), 26)
        self.assertEqual(self.merged["services"]["api"]["environment"]["PUBLIC_URL"], FIXTURE_ENV["PUBLIC_URL"])
        self.assertNotEqual(self.merged["services"]["api"]["image"], self.baseline["services"]["api"]["image"])

    def test_real_merge_rejects_hostile_late_overlay_privilege_or_credential_leaks(self):
        cases = (
            ({"ports": ["12345:12345"]}, "RUNTIME_UNEXPECTED_PRIVILEGE:ports"),
            ({"cap_add": ["SYS_ADMIN"]}, "RUNTIME_UNEXPECTED_PRIVILEGE:cap_add"),
            ({"privileged": True}, "RUNTIME_PRIVILEGED"),
            ({"pid": "host"}, "RUNTIME_HOST_OR_SHARED_NAMESPACE:pid"),
            ({"networks": ["control"]}, "RUNTIME_NETWORK_REACHES_CONTROL"),
            ({"secrets": ["database_url"]}, "RUNTIME_BUSINESS_SECRET_OR_MODEL_SECRET_MISSING"),
            ({"environment": {"S3_SECRET_KEY_FILE": "/run/secrets/s3_secret_key"}}, "RUNTIME_BUSINESS_OR_UNREVIEWED_ENVIRONMENT"),
            ({"volumes": ["/var/run/docker.sock:/var/run/docker.sock:ro"]}, "RUNTIME_FORBIDDEN_OR_MISSING_MOUNT"),
            ({"volumes": ["/opt/drop-code-executor/run:/run/drop-code-executor:ro"]}, "RUNTIME_FORBIDDEN_OR_MISSING_MOUNT"),
            ({"volumes": ["helper_socket:/run/drop-helper:ro"]}, "RUNTIME_FORBIDDEN_OR_MISSING_MOUNT"),
            ({"volumes": ["ai_tool_socket:/run/drop-ai-tools"]}, "RUNTIME_SOCKET_DIRECTION:/run/drop-ai-tools"),
            ({"mem_limit": "1g"}, "RUNTIME_MEMORY_UNBOUNDED"),
            ({"memswap_limit": "1g"}, "RUNTIME_SWAP_NOT_BOUNDED"),
            ({"cpus": 1}, "RUNTIME_CPU_UNBOUNDED"),
            ({"pids_limit": 128}, "RUNTIME_PIDS_UNBOUNDED"),
            ({"read_only": False}, "RUNTIME_ROOTFS_WRITABLE"),
        )
        for change, expected in cases:
            with self.subTest(expected=expected, change=change):
                candidate = self.compose(True, {"services": {"agent-runtime": change}})
                self.assertIn(expected, boundary_errors(self.baseline, candidate))
        # Compose 2.40 会在返回规范配置前拒绝 -1；无限 swap 的负对照
        # 应独立于解析器，上面的真实合并使用合法但超过 memory 的大小。
        unlimited_swap = deepcopy(self.merged)
        unlimited_swap["services"]["agent-runtime"]["memswap_limit"] = -1
        self.assertIn("RUNTIME_SWAP_NOT_BOUNDED", boundary_errors(self.baseline, unlimited_swap))

    def test_real_merge_rejects_api_consumer_write_and_other_service_changes(self):
        for changes, expected in (
            ({"api": {"volumes": ["ai_runner_socket:/run/drop-ai-runner"]}}, "API_SOCKET_DIRECTION:/run/drop-ai-runner"),
            ({"api": {"volumes": ["/opt/drop-code-executor/run:/run/drop-code-executor"]}}, "API_EXISTING_MOUNT_CHANGED:/run/drop-code-executor"),
            ({"api": {"volumes": ["/var/run/docker.sock:/var/run/docker.sock:ro"]}}, "API_UNEXPECTED_NEW_MOUNT"),
            ({"analyzer": {"networks": ["model-egress"]}}, "EGRESS_NETWORK_SHARED_WITH_BUSINESS"),
            ({"collector-helper": {"cap_add": ["SYS_ADMIN"]}}, "UNRELATED_SERVICE_CHANGED:collector-helper"),
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, boundary_errors(self.baseline, self.compose(True, {"services": changes})))

    def test_real_merge_rejects_permission_init_widening(self):
        for change, expected in (
            ({"cap_add": ["SYS_ADMIN"]}, "INIT_CAPABILITY_WIDENED"),
            ({"secrets": ["database_url"]}, "INIT_HAS_SECRETS_OR_PRIVILEGE"),
            ({"volumes": ["pg_data:/business"]}, "INIT_CAN_ACCESS_BUSINESS_MOUNTS"),
            ({"command": ["chown 10002:10003 /runner && chmod 2750 /runner && chown 10001:10003 /tools && chmod 2750 /tools && chmod 777 /tools"]}, "SOCKET_DIRECTORY_PRODUCER_OWNERSHIP_WRONG"),
            ({"pids_limit": 128}, "INIT_CPU_OR_PIDS_UNBOUNDED"),
        ):
            with self.subTest(expected=expected):
                candidate = self.compose(True, {"services": {"agent-runtime-permission-init": change}})
                self.assertIn(expected, boundary_errors(self.baseline, candidate))
        # Compose 默认合并 group_add 列表；负用例需显式移除或替换，
        # 确保测到的是最终配置中的真实缺失。
        for groups in ("[]", "['10004']"):
            with self.subTest(init_supplementary_groups=groups):
                candidate = self.compose(True, override_text=(
                    "services:\n  agent-runtime-permission-init:\n"
                    "    group_add: !override " + groups + "\n"
                ))
                self.assertIn("INIT_SOCKET_SUPPLEMENTARY_GROUP_INVALID", boundary_errors(self.baseline, candidate))

    def test_manifest_phase_limits_preserve_shared_budgets_and_final_format_restriction(self):
        manifest = json.loads((ROOT / "contracts/agent-runtime-v1.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], 1)
        self.assertEqual(manifest["run"]["runtime_values"], {"framework": "agentscope", "framework_version": "2.0.10", "agent_class": "agentscope.agent.Agent"})
        maxima = {"planning": (6, 10, 150000), "diagnosis": (9, 14, 150000), "repair": (8, 12, 420000)}
        self.assertEqual(set(manifest["phases"]), set(maxima))
        for phase, (models, tools, timeout) in maxima.items():
            budget = manifest["phases"][phase]
            self.assertEqual((budget["max_model_calls"], budget["max_tool_calls"], budget["timeout_ms"]), (models, tools, timeout))
            self.assertEqual(budget["max_tools_per_round"], 2)
        diagnosis = manifest["phases"]["diagnosis"]
        # 新请求有八轮普通模型预算，最后一轮必须由前一轮失败的 finish 换取。
        # 格式轮随请求上限计算；历史 4+1 请求的 SDK 回归仍单独验证，恢复不能升额。
        self.assertEqual(diagnosis["regular_model_calls"], 8)
        self.assertEqual(diagnosis["regular_model_calls"], diagnosis["max_model_calls"] - 1)
        self.assertIn("Only the final requested model round", diagnosis["format_retry"])
        self.assertIn("round max_model_calls-1", diagnosis["format_retry"])
        self.assertIn("no tools", diagnosis["format_retry"])
        self.assertIn("older 4+1 requests remain valid within their lower budgets", diagnosis["format_retry"])
        self.assertIn("lower historical budget is never silently raised on recovery", diagnosis["compatibility"])
        self.assertEqual(manifest["phases"]["repair"]["max_sandbox_tests"], 2)
        self.assertLessEqual(manifest["run"]["request_max_bytes"], 262144)
        self.assertLessEqual(manifest["callback"]["request_max_bytes"], 65536)


if __name__ == "__main__":
    unittest.main(verbosity=2)
