# Bounded collector helper spool

The host prepares one **2 GiB ext4 filesystem** shared by helper `cache`,
`incoming` and scratch output. This enforces a common filesystem ENOSPC limit
for external JVM/memray writers as well as helper-owned files. Ext4 metadata
uses part of that capacity. The helper container receives directory binds and
has no SYS_ADMIN capability; it cannot enlarge the backing image.

Run only on the deployment host as root, before creating the helper container:

```sh
# Production default; the explicit spelling is preferred in deployment scripts.
sh /opt/drop/deploy/prepare-helper-spool.sh --root /opt/drop-runtime
# The only other accepted root is the independent staging runtime directory.
sh /opt/drop-stage-full/deploy/prepare-helper-spool.sh --root /opt/drop-stage-full/runtime
```

Python 3, e2fsprogs and util-linux are required. Both roots are exact allowlist
entries; relative paths, path aliases and other directories are refused. Every
existing ancestor must be a root-owned directory without group/other writes
and without symlinks. The dedicated root and mounted filesystem root use
root:10001 mode0750 so host targets in group10001 can traverse to `incoming`.
The staging parent must independently allow that group's traversal; this script
does not change directories outside the selected runtime root. Existing roots
with other ownership/modes fail before provisioning.

The script exclusively creates `helper-spool.ext4` and `helper-spool.json` with
root0600 permissions. First creation needs **3 GiB available host space**:
2 GiB backing storage plus a 1 GiB reserve. It prefers physical allocation and
uses truncate only when the filesystem lacks allocation support. A root-owned
lock prevents concurrent preparation. `mkfs.ext4` only receives this invocation's
new regular file. An existing image is never formatted, resized or overwritten.
Existing metadata, ext4 UUID, file/superblock size and loop backing file/offset
must match. A pre-existing unmounted nonempty mountpoint is refused.

The mount uses `rw,nodev,nosuid,noexec`. Its children are:

| Directory | Host owner/mode | Intended bind |
| --- | --- | --- |
| `helper-spool/cache` | root:root 0700 | Helper-only cache and scratch |
| `helper-spool/incoming` | root:10001 1770 | Agent/target staging; sticky directory |

Docker's two named volumes must bind these two host subdirectories. They share
the same underlying 2 GiB filesystem; they are not two separate 2 GiB budgets.
Configure those binds in the deployment overlay before starting the helper.
The helper's independent same-filesystem/capacity check must remain enabled.
For `compose.helper.yml`, set `DROP_HELPER_SPOOL_ROOT` to the mounted directory
`/opt/drop-runtime/helper-spool`, or `/opt/drop-stage-full/runtime/helper-spool`
for staging. `helper_data` binds its `cache` child and `helper_incoming` binds
its `incoming` child. Keep Agent persistent state on its separate volume.

Install `systemd/drop-helper-spool.service` and enable it for boot. It runs
before Docker and ordinary host collection services. The ordinary helper unit
should require this spool service. Docker itself does **not** require it: a
damaged helper filesystem must not prevent PostgreSQL/API/control-plane startup.

Host service order is spool, helper, Agent. Docker order is spool, Agent,
helper: the root helper joins the running non-root Agent's container PID/network
namespaces. After replacing or restarting the Agent container, recreate the
helper after the Agent starts; an existing helper retains its old namespace
references. Before any spool mount change, quiesce collection and stop both
consumers. Verify the mount first, then restart in the relevant order. See the
[host/container deployment notes](systemd/README.md) for credential separation
and target permissions.

A failed mount preserves the completed image and metadata for a safe rerun.
Earlier incomplete files are removed only if this invocation created their
exact inode and positively observes no mount. A power interruption can leave
an image without complete metadata; the next run refuses it for manual review.
There is no recursive delete, automatic reformat, data migration or automatic
unmount. Stopping the oneshot unit leaves the live mount and data in place.

Local regression tests use fake commands and 4 KiB temporary images:

```sh
python -m unittest discover -s deploy/tests -p test_helper_spool.py -v
```

Those tests do not claim a real Linux mount, ENOSPC exercise or reboot test.
Run those separately on the isolated staging root before production use.
