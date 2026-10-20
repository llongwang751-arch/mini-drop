#!/usr/bin/env python3
"""Stage-only synthetic Alertmanager -> maintenance -> audit transport check.

Run on the Docker host after the root task hands over the isolated stage queue.
Only this invocation's new synthetic alert is posted; rules are not evaluated.
"""

import argparse
from datetime import datetime, timedelta, timezone
import http.client
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from urllib.parse import urlencode

PROJECT = "dropfullstage"
HOST = "127.0.0.1"
PORT = 19093
DEADLINE_SECONDS = 480
MAX_OUTPUT = 65536
NAME = re.compile(r"ImportedStageTransportProbe_[0-9a-f]{32}\Z")
ID = re.compile(r"[0-9a-f]{64}\Z")
INSPECT = (
    '{"id":{{json .Id}},"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
    '"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
    '"running":{{json .State.Running}},"ports":{{json .HostConfig.PortBindings}}}'
)
PSQL = (
    'export PGPASSWORD="$(cat /run/secrets/database_password)"; '
    "exec psql -X --no-password --quiet --tuples-only --no-align "
    '-v ON_ERROR_STOP=1 -v "alert_name=$1" -h 127.0.0.1 -U drop -d drop --file=-'
)
AUDIT_SQL = """BEGIN READ ONLY;
SET LOCAL statement_timeout='3s';
WITH selected AS (
 SELECT id, detail->>'status' AS status,
   action='MONITORING_ALERT' AND resource_type='monitoring'
   AND request_id='alertmanager' AND detail->>'alertname'=:'alert_name'
   AND detail->>'severity'='info' AND detail->>'job'='imported-stage-transport-probe'
   AND detail->>'instance'='dropfullstage' AS valid
 FROM audit_logs WHERE resource_id=:'alert_name'
), counted AS (
 SELECT count(*) AS total,
   count(*) FILTER (WHERE valid AND status='firing') AS firing,
   count(*) FILTER (WHERE valid AND status='resolved') AS resolved,
   min(id) FILTER (WHERE valid AND status='firing') AS first_firing,
   min(id) FILTER (WHERE valid AND status='resolved') AS first_resolved
 FROM selected
)
SELECT json_build_object('total',total,'firing',firing,'resolved',resolved,
 'other',total-firing-resolved,'first_firing',first_firing,'first_resolved',first_resolved)
FROM counted;
COMMIT;
"""


class Failure(Exception):
    """Only fixed public error codes cross the command-line boundary."""


def require(condition, code):
    if not condition:
        raise Failure(code)


def alert_name(value):
    require(isinstance(value, str) and NAME.fullmatch(value), "ALERT_IDENTITY_INVALID")
    return value


def decode(payload):
    require(isinstance(payload, bytes) and len(payload) <= MAX_OUTPUT, "RESPONSE_LIMIT")
    try:
        return json.loads(payload)
    except (ValueError, UnicodeError, RecursionError):
        raise Failure("RESPONSE_INVALID") from None


def validate_container(data, service):
    require(
        isinstance(data, dict)
        and set(data) == {"id", "project", "service", "running", "ports"}
        and isinstance(data["id"], str)
        and ID.fullmatch(data["id"])
        and data["project"] == PROJECT
        and data["service"] == service
        and data["running"] is True,
        "STAGE_CONTAINER_REQUIRED",
    )
    require(
        data["ports"] is None or isinstance(data["ports"], dict),
        "STAGE_PORT_METADATA_INVALID",
    )
    ports = data["ports"] or {}
    if service == "alertmanager":
        require(
            ports == {"9093/tcp": [{"HostIp": HOST, "HostPort": str(PORT)}]},
            "STAGE_LOOPBACK_BINDING_REQUIRED",
        )
    else:
        require(service == "postgres" and not ports, "STAGE_DATABASE_REQUIRED")
    return data["id"]


def scalar(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def validate_receivers(config, *, verified_redacted_url=False):
    # Alertmanager /status returns its parsed, serialized active configuration.
    # Inspect only its receiver section; never log the original configuration.
    require(
        isinstance(config, str) and len(config) <= MAX_OUTPUT, "ROUTE_CONFIG_INVALID"
    )
    lines = config.splitlines()
    starts = [index for index, line in enumerate(lines) if line == "receivers:"]
    require(len(starts) == 1, "INTERNAL_AUDIT_RECEIVER_REQUIRED")
    section = []
    for line in lines[starts[0] + 1 :]:
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*:", line):
            break
        if line.strip() and not line.lstrip().startswith("#"):
            require(
                not re.search(r"(?:<<:|(?:^|\s)[&*][A-Za-z_])", line),
                "INTERNAL_AUDIT_RECEIVER_REQUIRED",
            )
            section.append(line)
    pairs = []
    for line in section:
        matched = re.fullmatch(
            r"\s*(?:-\s*)?([A-Za-z_][A-Za-z0-9_]*):\s*(.*?)\s*", line
        )
        require(matched is not None, "INTERNAL_AUDIT_RECEIVER_REQUIRED")
        pairs.append((matched[1], scalar(matched[2])))
    expected_urls = [["http://maintenance:9105/alerts"]]
    if verified_redacted_url:
        expected_urls.append(["<secret>"])
    require(
        [scalar(match[1]) for line in section if (match := re.fullmatch(r"\s*- name:\s*(.*?)\s*", line))] == ["drop-audit"]
        and [key for key, _ in pairs if key.endswith("_configs")] == ["webhook_configs"]
        and [value for key, value in pairs if key == "url"]
        in expected_urls
        and [value for key, value in pairs if key == "send_resolved"] == ["true"]
        and all(not value for key, value in pairs if key in {"url_file", "proxy_url"})
        and all(
            value == "false" for key, value in pairs if key == "proxy_from_environment"
        ),
        "INTERNAL_AUDIT_RECEIVER_REQUIRED",
    )
    require(
        re.search(r"(?m)^\s+group_wait: 30s\s*$", config)
        and re.search(r"(?m)^\s+group_interval: 5m(?:0s)?\s*$", config),
        "REVIEWED_GROUP_TIMING_REQUIRED",
    )


def parse_counts(payload):
    data = decode(payload)
    require(
        isinstance(data, dict)
        and set(data)
        == {"total", "firing", "resolved", "other", "first_firing", "first_resolved"},
        "AUDIT_RESULT_INVALID",
    )
    require(
        all(
            type(data[key]) is int and 0 <= data[key] <= 1000
            for key in ("total", "firing", "resolved", "other")
        )
        and data["other"] == 0
        and data["total"] == data["firing"] + data["resolved"],
        "AUDIT_RESULT_INVALID",
    )
    for status in ("firing", "resolved"):
        identifier = data["first_" + status]
        require(
            (data[status] == 0 and identifier is None)
            or (
                data[status] > 0
                and type(identifier) is int
                and 0 < identifier <= 2**63 - 1
            ),
            "AUDIT_RESULT_INVALID",
        )
    return data


def docker(arguments, stdin=None):
    # The local daemon is explicit: inherited Docker contexts/proxies/credentials
    # cannot redirect an isolated-stage check to another host.
    require(stdin is None or len(stdin) < 4096, "COMMAND_INPUT_LIMIT")
    try:
        process = subprocess.Popen(
            ["/usr/bin/docker", *arguments],
            stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin", "DOCKER_HOST": "unix:///var/run/docker.sock"},
        )
    except (OSError, ValueError):
        raise Failure("STAGE_DOCKER_UNAVAILABLE") from None
    output = bytearray()
    exceeded, failed = threading.Event(), threading.Event()

    def kill():
        try:
            process.kill()
        except OSError:
            pass

    def drain():
        try:
            while chunk := process.stdout.read(4096):
                if len(output) + len(chunk) > MAX_OUTPUT:
                    exceeded.set()
                    kill()
                    return
                output.extend(chunk)
        except OSError:
            failed.set()
            kill()
        finally:
            process.stdout.close()

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        if stdin:
            try:
                process.stdin.write(stdin)
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        kill()
        process.wait(timeout=3)
        raise Failure("STAGE_COMMAND_TIMEOUT") from None
    finally:
        reader.join(timeout=3)
    require(not exceeded.is_set(), "COMMAND_OUTPUT_LIMIT")
    require(not failed.is_set() and not reader.is_alive(), "COMMAND_OUTPUT_FAILED")
    require(process.returncode == 0, "STAGE_COMMAND_FAILED")
    return bytes(output)


class Host:
    def __init__(self):
        self.containers = {}

    def preflight(self):
        require(
            (PROJECT, HOST, PORT) == ("dropfullstage", "127.0.0.1", 19093),
            "FIXED_STAGE_SCOPE_REQUIRED",
        )
        for service in ("alertmanager", "postgres"):
            names = (
                docker(
                    [
                        "ps",
                        "--no-trunc",
                        "--filter",
                        "label=com.docker.compose.project=" + PROJECT,
                        "--filter",
                        "label=com.docker.compose.service=" + service,
                        "--format",
                        "{{.ID}}",
                    ]
                )
                .decode("ascii")
                .splitlines()
            )
            require(
                len(names) == 1 and ID.fullmatch(names[0]),
                "UNIQUE_STAGE_CONTAINER_REQUIRED",
            )
            container = validate_container(
                decode(docker(["inspect", "--format", INSPECT, names[0]])), service
            )
            require(
                self.containers.get(service, container) == container,
                "STAGE_CONTAINER_CHANGED",
            )
            self.containers[service] = container

    def audit(self, name):
        alert_name(name)
        return parse_counts(
            docker(
                [
                    "exec",
                    "-i",
                    self.containers["postgres"],
                    "sh",
                    "-eu",
                    "-c",
                    PSQL,
                    "sh",
                    name,
                ],
                AUDIT_SQL.encode(),
            )
        )


class API:
    def reload_reviewed_internal_receiver(self):
        # New Alertmanager versions redact webhook URLs in /status. Verify the
        # actual stage-only input, explicitly reload it, and fence acceptance by
        # the successful reload timestamp. A redacted URL alone is insufficient.
        host = Host()
        host.preflight()
        container = host.containers["alertmanager"]
        arguments = decode(docker(["inspect", "--format", "{{json .Config.Cmd}}", container]))
        require(isinstance(arguments, list) and all(isinstance(x, str) for x in arguments)
                and [x for x in arguments if x.lstrip("-").split("=", 1)[0] == "config.file"] == ["--config.file=/etc/alertmanager/alertmanager.yml"], "STAGE_CONFIG_PATH_REJECTED")
        original = docker(["exec", container, "cat", "/etc/alertmanager/alertmanager.yml"])
        validate_receivers(original.decode("utf-8"))
        def reload_state():
            text = self.request("GET", "/metrics").decode("ascii")
            stamp = re.search(r"(?m)^alertmanager_config_last_reload_success_timestamp_seconds ([0-9.e+]+)$", text)
            successful = re.search(r"(?m)^alertmanager_config_last_reload_successful 1$", text)
            require(stamp is not None and successful is not None, "STAGE_CONFIG_RELOAD_REQUIRED")
            return float(stamp[1])
        previous = reload_state()
        host.preflight()
        docker(["kill", "--signal", "HUP", container])
        deadline = time.monotonic() + 10
        while reload_state() <= previous:
            require(time.monotonic() < deadline, "STAGE_CONFIG_RELOAD_REQUIRED")
            time.sleep(.2)
        host.preflight()
        require(docker(["exec", container, "cat", "/etc/alertmanager/alertmanager.yml"]) == original, "STAGE_CONFIG_CHANGED")

    def request(self, method, path, payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        require(body is None or len(body) <= 2048, "ALERT_BODY_LIMIT")
        connection = http.client.HTTPConnection(HOST, PORT, timeout=5)
        try:
            connection.request(
                method,
                path,
                body,
                {"Accept": "application/json", "Content-Type": "application/json"},
            )
            response = connection.getresponse()
            encoded = response.read(MAX_OUTPUT + 1)
            require(
                response.status == 200 and len(encoded) <= MAX_OUTPUT,
                "STAGE_ALERT_HTTP_FAILED",
            )
            return encoded
        except Failure:
            raise
        except Exception:
            raise Failure("STAGE_ALERT_HTTP_FAILED") from None
        finally:
            connection.close()

    def check_receivers(self):
        status = decode(self.request("GET", "/api/v2/status"))
        require(
            isinstance(status, dict) and isinstance(status.get("config"), dict),
            "ROUTE_CONFIG_INVALID",
        )
        original = status["config"].get("original")
        if isinstance(original, str) and "url: <secret>" in original:
            self.reload_reviewed_internal_receiver()
            status = decode(self.request("GET", "/api/v2/status"))
            validate_receivers(status["config"].get("original"), verified_redacted_url=True)
        else:
            validate_receivers(original)

    def active(self, name):
        query = urlencode({"filter": 'alertname="' + alert_name(name) + '"'})
        result = decode(self.request("GET", "/api/v2/alerts?" + query))
        require(isinstance(result, list) and len(result) <= 100, "ALERT_LOOKUP_INVALID")
        return result

    def post(self, alert):
        self.request("POST", "/api/v2/alerts", [alert])


def rfc3339(value):
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def exercise(
    host,
    api,
    proof,
    monotonic=time.monotonic,
    sleep=time.sleep,
    now=lambda: datetime.now(timezone.utc),
):
    deadline = monotonic() + DEADLINE_SECONDS
    name = alert_name("ImportedStageTransportProbe_" + secrets.token_hex(16))
    proof["alert_name"] = name
    host.preflight()
    api.check_receivers()
    require(
        host.audit(name)["total"] == 0 and not api.active(name), "ALERT_IDENTITY_REUSED"
    )
    start = now()
    alert = {
        "labels": {
            "alertname": name,
            "severity": "info",
            "job": "imported-stage-transport-probe",
            "instance": PROJECT,
        },
        "annotations": {
            "summary": "Explicitly imported synthetic stage transport probe; no rule evaluation"
        },
        "startsAt": rfc3339(start),
        "endsAt": rfc3339(start + timedelta(minutes=15)),
    }
    posted = resolved = False

    def submit(value):
        host.preflight()
        api.check_receivers()
        api.post(value)

    def wait_for(status):
        while True:
            require(monotonic() < deadline, "STAGE_ALERT_TRANSPORT_DEADLINE")
            state = host.audit(name)
            require(monotonic() < deadline, "STAGE_ALERT_TRANSPORT_DEADLINE")
            proof.update(
                firing_audit_count=state["firing"],
                resolved_audit_count=state["resolved"],
            )
            if status == "firing":
                require(state["resolved"] == 0, "UNEXPECTED_RESOLUTION")
            if state[status] > 0:
                return state
            sleep(min(5, max(0, deadline - monotonic())))

    try:
        posted = True
        submit(alert)
        wait_for("firing")
        require(monotonic() < deadline, "STAGE_ALERT_TRANSPORT_DEADLINE")
        submit(alert | {"endsAt": rfc3339(now())})
        resolved = True
        state = wait_for("resolved")
        require(
            state["firing"] > 0 and state["first_firing"] < state["first_resolved"],
            "AUDIT_DELIVERY_ORDER_INVALID",
        )
        proof.update(success=True, firing_before_resolved=True)
    finally:
        if posted and not resolved:
            try:
                submit(alert | {"endsAt": rfc3339(now())})
                proof["cleanup_resolution_submitted"] = True
            except Exception:
                proof["cleanup_resolution_submitted"] = False


def run(proof):
    require(sys.platform == "linux" and os.geteuid() == 0, "STAGE_LINUX_HOST_REQUIRED")
    exercise(Host(), API(), proof)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Failure("OPTIONS_INVALID")


def main(argv=None):
    proof = {
        "schema_version": 1,
        "success": False,
        "project": PROJECT,
        "scope": "synthetic_transport_only",
        "rule_evaluation_verified": False,
    }
    started = time.monotonic()
    try:
        parser = Parser(description=__doc__)
        parser.add_argument("--confirm-stage", required=True, choices=["dropfullstage"])
        parser.parse_args(argv)
        run(proof)
    except Failure as error:
        proof["error_code"] = str(error)
    except KeyboardInterrupt:
        proof["error_code"] = "VALIDATION_INTERRUPTED"
    except Exception:
        proof["error_code"] = "STAGE_ALERT_TRANSPORT_FAILED"
    proof["elapsed_seconds"] = round(time.monotonic() - started, 3)
    print(json.dumps(proof, sort_keys=True))
    return 0 if proof["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
