"""Isolation and recovery guards for the local launcher; no Docker required."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import dev


class DevelopmentIsolationTests(unittest.TestCase):
    def setUp(self):
        self.config = dev.settings("unit", 18080)
        self.workspace = dev.workspace("unit")

    def rendered(self):
        return {"name": self.config["project"], "volumes": {"pg_data": {"name": self.config["project"] + "_pg_data"}},
                "secrets": {"database_url": {"file": str(self.workspace / "secrets" / "database_url")}},
                "services": {"web": {"ports": [{"host_ip":"127.0.0.1", "published":"18080"}]}}}

    def test_development_project_and_volume_namespace_are_distinct(self):
        dev.validate_rendered(self.config, self.rendered())
        for project in ["drop", "production", "txdrop-dev-other"]:
            value = self.rendered(); value["name"] = project
            with self.assertRaises(dev.DevError): dev.validate_rendered(self.config, value)

    def test_external_or_production_volumes_are_rejected(self):
        for volume in [{"external": True, "name":"shared"}, {"name":"drop_pg_data"}]:
            value = self.rendered(); value["volumes"]["pg_data"] = volume
            with self.assertRaises(dev.DevError): dev.validate_rendered(self.config, value)

    def test_no_public_listener_or_production_credentials(self):
        for host in ["0.0.0.0", "::", ""]:
            value = self.rendered(); value["services"]["web"]["ports"][0]["host_ip"] = host
            with self.assertRaises(dev.DevError): dev.validate_rendered(self.config, value)
        value = self.rendered(); value["secrets"]["database_url"]["file"] = str(dev.ROOT / "deploy/secrets/database_url")
        with self.assertRaises(dev.DevError): dev.validate_rendered(self.config, value)

    def test_no_host_filesystem_or_host_process_access(self):
        for service in [{"privileged":True}, {"pid":"host"}, {"volumes":[{"type":"bind","source":"/var/run/docker.sock"}]}]:
            value = self.rendered(); value["services"]["agent"] = service
            with self.assertRaises(dev.DevError): dev.validate_rendered(self.config, value)

    def test_invalid_names_ports_and_images_are_rejected(self):
        for instance in ["../drop", "", "drop/production", "a"*25]:
            with self.assertRaises(dev.DevError): dev.workspace(instance)
        for port in [80, 0, 65536]:
            with self.assertRaises(dev.DevError): dev.settings("test", port)
        with self.assertRaises(dev.DevError): dev.settings("test",18080,{"core":"bad image;command"})

    def test_environment_cannot_redirect_commands_to_production(self):
        with patch.dict(dev.os.environ, {"COMPOSE_FILE":"production.yml", "COMPOSE_PROJECT_NAME":"drop", "PUBLIC_URL":"https://production", "DROP_CONFIG":"private", "RELEASE_TAG":"prod"}):
            env = dev.environment()
        for key in ["COMPOSE_FILE","COMPOSE_PROJECT_NAME","PUBLIC_URL","DROP_CONFIG","RELEASE_TAG"]:
            self.assertNotIn(key, env)

    def test_compose_uses_explicit_files_and_project_directory(self):
        argv = dev.compose(self.config, self.workspace)
        self.assertIn("txdrop-dev-unit", argv)
        self.assertIn(str(self.workspace / ".env"), argv)
        self.assertNotIn("down", argv)
        self.assertNotIn("-v", argv)
        self.assertEqual(dev.overlay(self.config)["services"]["drop-server"]["build"]["target"], "runtime")

    def test_smoke_verifies_running_project_and_port_before_login(self):
        import json
        labels={"com.docker.compose.project":self.config["project"],"com.docker.compose.service":"web"}
        ports={"8080/tcp":[{"HostIp":"127.0.0.1","HostPort":"18080"}]}
        with patch.object(dev,"run",side_effect=["a"*64,"\n".join(map(json.dumps,[labels,ports,True]))]):
            dev.validate_live_web(self.config,self.workspace)
        labels["com.docker.compose.project"]="drop"
        with patch.object(dev,"run",side_effect=["a"*64,"\n".join(map(json.dumps,[labels,ports,True]))]):
            with self.assertRaises(dev.DevError): dev.validate_live_web(self.config,self.workspace)


if __name__ == "__main__":
    unittest.main()
