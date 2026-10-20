"""Validate deployment trust boundaries; this is not a Linux runtime proof."""

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


class ComposeLoader(yaml.SafeLoader):
    pass


def sequence(loader, node):
    return loader.construct_sequence(node)


ComposeLoader.add_constructor("!reset", sequence)
ComposeLoader.add_constructor("!override", sequence)


class HelperDeploymentTests(unittest.TestCase):
    def test_compose_agent_has_no_capability_and_cannot_write_socket_directory(self):
        config = yaml.load(
            (ROOT / "deploy/compose.helper.yml").read_text(), Loader=ComposeLoader
        )
        agent = config["services"]["agent"]
        helper = config["services"]["collector-helper"]
        self.assertEqual(agent["user"], "10001:10001")
        self.assertEqual(agent["cap_add"], [])
        self.assertEqual(agent["cap_drop"], ["ALL"])
        self.assertEqual(agent["security_opt"], ["no-new-privileges:true"])
        self.assertIn("helper_socket:/run/drop-helper:ro", agent["volumes"])
        self.assertEqual(helper["pid"], "service:agent")
        self.assertEqual(helper["network_mode"], "service:agent")
        self.assertNotIn("collector-helper", agent["depends_on"])
        self.assertEqual(helper["user"], "0:10001")
        self.assertEqual(
            agent["environment"]["COLLECTOR_HELPER_REQUIRE_BOUNDED_FS"], "true"
        )
        self.assertEqual(
            agent["environment"]["NATIVE_CONTROL_ROOT"],
            "/run/drop-helper-incoming/native-control",
        )
        self.assertEqual(
            helper["environment"]["NATIVE_CONTROL_ROOT"],
            agent["environment"]["NATIVE_CONTROL_ROOT"],
        )

    def test_helper_has_only_explicit_sampling_rights_and_no_credentials(self):
        config = yaml.load(
            (ROOT / "deploy/compose.helper.yml").read_text(), Loader=ComposeLoader
        )
        helper = config["services"]["collector-helper"]
        self.assertNotIn("secrets", helper)
        self.assertNotIn("privileged", helper)
        self.assertNotIn("ports", helper)
        self.assertEqual(
            set(helper["cap_add"]), {"PERFMON", "SYS_PTRACE", "DAC_READ_SEARCH", "KILL"}
        )
        self.assertEqual(helper["cap_drop"], ["ALL"])
        self.assertTrue(helper["read_only"])
        self.assertEqual(helper["mem_limit"], helper["memswap_limit"])
        for name in helper["environment"]:
            self.assertFalse(
                any(
                    secret in name
                    for secret in ("TLS", "S3_", "DATABASE", "PASSWORD", "TOKEN")
                )
            )
        for mount in helper["volumes"]:
            self.assertNotIn("secrets", mount)
            self.assertNotIn("docker.sock", mount)
            self.assertNotIn("agent_perf_data", mount)
        ebpf = yaml.load(
            (ROOT / "deploy/compose.helper-ebpf.yml").read_text(), Loader=ComposeLoader
        )
        self.assertEqual(set(ebpf["services"]), {"collector-helper"})
        self.assertEqual(
            set(ebpf["services"]["collector-helper"]["cap_add"]),
            {"PERFMON", "SYS_PTRACE", "DAC_READ_SEARCH", "KILL", "BPF"},
        )

    def test_systemd_agent_and_helper_credentials_are_separate(self):
        agent = (ROOT / "deploy/systemd/drop-agent.service").read_text()
        helper = (ROOT / "deploy/systemd/drop-collector-helper.service").read_text()
        self.assertIn("User=drop\n", agent)
        self.assertIn("CapabilityBoundingSet=\n", agent)
        self.assertIn("AmbientCapabilities=\n", agent)
        self.assertIn("User=root\nGroup=drop\n", helper)
        self.assertIn("EnvironmentFile=/etc/drop/collector-helper.env\n", helper)
        self.assertNotIn("/etc/drop/agent.env", helper)
        self.assertNotIn("SYS_ADMIN", helper)
        self.assertIn("Requires=drop-helper-spool.service", helper)
        self.assertIn("CAP_DAC_READ_SEARCH", helper)
        self.assertIn(
            "NATIVE_CONTROL_ROOT=/opt/drop-runtime/helper-spool/incoming/native-control",
            (ROOT / "deploy/systemd/agent.env.example").read_text(),
        )

    def test_kubernetes_tls_only_reaches_non_root_agent(self):
        documents = list(
            yaml.safe_load_all(
                (ROOT / "deploy/kubernetes/agent-daemonset.yaml").read_text()
            )
        )
        pod = next(item for item in documents if item["kind"] == "DaemonSet")["spec"][
            "template"
        ]["spec"]
        containers = {item["name"]: item for item in pod["containers"]}
        agent = containers["agent"]
        helper = containers["collector-helper"]
        self.assertEqual(agent["securityContext"]["runAsUser"], 10001)
        self.assertEqual(agent["securityContext"]["capabilities"], {"drop": ["ALL"]})
        self.assertTrue(agent["securityContext"]["runAsNonRoot"])
        self.assertEqual(helper["securityContext"]["runAsGroup"], 10001)
        self.assertNotIn("tls", {item["name"] for item in helper["volumeMounts"]})
        self.assertFalse(helper["securityContext"]["privileged"])
        self.assertFalse(pod["automountServiceAccountToken"])


if __name__ == "__main__":
    unittest.main()
