# Agent perf seccomp policy

`agent-perfmon.json` was derived from the effective OCI seccomp policy of the
running, unprivileged `drop-agent-1` on Docker 29.1.3 / Ubuntu 24.04 x86_64.
The original Agent had `cap_drop: ALL`. The sole policy change is one
`SCMP_ACT_ALLOW` rule for `perf_event_open`; default deny, architecture list,
argument filters, and all other syscall rules are unchanged. It is not an
allow-all profile. The controlled probe accepted this exact policy.

`compose.perf.yml` retains `cap_drop: ALL`, no-new-privileges, read-only root,
the private PID namespace, resource limits, and controlled-target enforcement.
It adds only CAP_PERFMON. Docker currently delivers an empty effective
capability set for UID 10001, so this optional mode uses UID 0 with only
CAP_PERFMON. A non-root helper/ambient-capability design is not implemented.

The dedicated `agent_perf_data` volume uses `nocopy: true` to avoid copying
the base image's UID-10001 workdir ownership into it. Its root-owned workdir
is changed to 0700 by Agent. The original `agent_data` volume is retained
unchanged. Do not switch modes while an attempt is active; inspect journals
before retiring either volume.

After a Docker, kernel or architecture upgrade, compare the new default policy
and repeat the controlled perf probe. Do not disable seccomp or add SYS_ADMIN,
SYS_PTRACE, host PID mode, privileged mode or host sysctl changes to work around
an unexplained failure.
