# Plan: fix `soda` RAM exhaustion, entirely via Ansible playbooks

## Context (what the next agent needs to know)

`soda` is a Hetzner VM (7.6 GiB RAM, 4 GiB swap) that hosts both Concourse (web + worker) and VictoriaMetrics, plus PostgreSQL, tailscaled and node_exporter. On 2026-10-03 it entered an OOM loop from about 17:29 to 17:42 and then rebooted. Concourse and the `metrics` service were unreachable during the outage, which is why the `uhlig.it` deploy builds 35 and 36 are stuck in a `started` state.

Because `soda` is the Tailscale service host for both `concourse.tailnet-204f.ts.net` (VIP 100.108.203.66) and `metrics.tailnet-204f.ts.net` (100.108.165.186), both services black-holed together while the host was down.

Evidence gathered on `soda` (kernel 6.8.0-138):

- Repeated OOM kill of `victoria-metrics` six times (17:29, 17:32, 17:34, 17:35, 17:37, 17:40); the kill line reads `oom-kill:constraint=CONSTRAINT_NONE,nodemask=(null),cpuset=init.scope,mems_allowed=0,global_oom,task_memcg=/system.slice/victoria-metrics.service,task=victoria-metric`.
- At every OOM the swap was full: `Total swap = 4194300kB` with `Free swap = 32–232 kB`.
- VictoriaMetrics starts with `limiting caches to 4876635340 bytes (4.54 GiB), leaving 3251090228 bytes (3.03 GiB) to the OS according to -memory.allowedPercent=60, system memory limit 8127725568 bytes`.
- `systemctl show victoria-metrics` reports `MemoryMax=infinity`, `MemoryHigh=infinity`, `OOMScoreAdjust=0`, `Restart=always`, `RestartUSec=100ms`, `OOMPolicy=stop`.
- Sysctls: `vm.swappiness = 10`, `vm.overcommit_memory = 0`, `vm.overcommit_ratio = 50`. Even while idle, `Committed_AS` (8.68 GB) already exceeds `CommitLimit` (8.16 GB).
- VM data: `/var/lib/victoria-metrics` is only 1.2 GB, `-retentionPeriod=12`, seven scrape jobs. The service runs as user `victoria-metrics` with binary `/usr/local/bin/victoria-metrics-prod` v1.150.0.
- Swap file is `/swapfile` (4194300 kB) with the fstab entry `/swapfile none swap sw 0 0`.
- After the reboot, `fly -t uh workers` shows the `soda` worker running again and Concourse answers again.

## Workspace and tooling constraints

The infrastructure repo lives at `/Users/suhlig/git/github.com/uhlig-it/infrastructure` (branch `main`), but it is not currently a workspace root in this project (only `www` is). The next agent should add `infrastructure` and likely `metrics` and `concourse-deployment` as workspace roots, or operate inside a `wtg` workspace, before editing anything.

Candidate repos that may own the relevant config, to be confirmed during discovery:

- `/Users/suhlig/git/github.com/uhlig-it/infrastructure` for the host base (sysctls, swap, systemd overrides)
- `/Users/suhlig/git/github.com/uhlig-it/metrics` for the VictoriaMetrics unit, flags and scrape config
- `/Users/suhlig/git/github.com/uhlig-it/concourse-deployment` for the Concourse web and worker units
- `/Users/suhlig/git/github.com/suhlig/ansible-role-simple-systemd-service` for the generic systemd unit role

Everything must be expressed as idempotent Ansible tasks and templates in those repos and then rolled out through the normal pipeline. Do not hand-edit `soda` and leave the repo untouched.

## Step 0: discovery

For each candidate repo, find where `soda` is defined and where each piece lives.

```sh
grep -rn -iE "victoria|swapfile|swappiness|overcommit|OOMScoreAdjust|MemoryMax" \
  --include='*.yml' --include='*.j2' --include='*.conf' .
grep -rn -i "soda" --include='*.yml' --include='*.ini' --include='*.cfg' .
```

Locate the VictoriaMetrics systemd unit template (its `ExecStart` currently matches the block quoted above), the sysctl tasks, the swap tasks, the inventory entry for `soda`, and the Concourse unit names (`concourse-web.service`, `concourse-worker.service` or a single `concourse.service`). Report the exact role and playbook file paths that will be edited before changing anything.

## Step 1: cap VictoriaMetrics to the box

Add an explicit cache budget to the unit's `ExecStart`, replacing reliance on the `-memory.allowedPercent=60` default.

```yaml
# Prefer an absolute budget on a shared, memory-constrained box.
-memory.allowedBytes=1500MB
```

Rationale: 1.2 GB of data and seven jobs do not need 4.5 GiB of cache. This leaves roughly 6 GiB for the OS, Concourse, PostgreSQL and builds. Use `-memory.allowedBytes` (absolute) rather than `-memory.allowedPercent` so the cap does not silently scale back up on a small host.

## Step 2: add a cgroup ceiling on `victoria-metrics.service`

Add to the unit's `[Service]` section.

```ini
MemoryHigh=1400M
MemoryMax=1800M
RestartSec=10
```

`MemoryHigh` and `MemoryMax` keep reclaim inside the unit instead of letting a single process exhaust the host and trigger a global OOM. `Restart=always` is fine, but the current `RestartUSec=100ms` re-warms 1.2 GB of caches immediately and feeds the loop, so set `RestartSec=10` and keep the existing `OOMPolicy=stop`. Leave the existing sandboxing block untouched, its comments about tailscaled and `PrivateNetwork` are intentional.

## Step 3: protect Concourse

In the Concourse unit or units (resolve the exact name in Step 0), add the following.

```ini
OOMScoreAdjust=-500
```

Consider `-300` for PostgreSQL as well. The goal is that if memory spikes again the kernel kills VictoriaMetrics rather than the CI system. Raising VictoriaMetrics to a positive `OOMScoreAdjust` is an alternative, but explicitly protecting Concourse is clearer.

## Step 4: grow swap and raise swappiness

The safest idempotent approach is to add a second swapfile rather than resize the live one, because growing in place requires `swapoff`, which is risky under memory pressure. Create `/swapfile2` of 4 GiB for a total of 8 GiB, using `command` with `creates: /swapfile2`, then `mkswap` and `swapon`, plus an fstab entry via `ansible.posix.mount` or `lineinfile`. If a single 8 GiB file is preferred, do it during a maintenance window with `swapoff -a` first.

Set `vm.swappiness` to 30. That is deliberately conservative because this is a single-disk box where too much swap causes I/O thrash, which is what produced the `context deadline exceeded` scrape failures during the incident. Roll the memory sysctls together.

```yaml
- name: Tune memory sysctls
  ansible.posix.sysctl:
    name: "{{ item.name }}"
    value: "{{ item.value }}"
    sysctl_set: true
    state: present
    reload: true
  loop:
    - { name: vm.swappiness, value: "30" }
    - { name: vm.overcommit_memory, value: "2" }
    - { name: vm.overcommit_ratio, value: "100" }
```

## Step 5: set `vm.overcommit_memory=2`

The caveat that matters here: mode 2 enforces `CommitLimit = swap + RAM * overcommit_ratio/100`. With `overcommit_ratio=100` and 8 GiB of swap the limit becomes `7.57 + 8 ≈ 15.6 GiB`, comfortably above the current `Committed_AS` of 8.68 GB. Do not use a low ratio such as 50 (about 11.8 GiB) without measuring peak commit, or Go and PostgreSQL reservations can start failing allocation.

Before enabling mode 2 in production, verify the peak commit on a busy day.

```sh
grep -E 'CommitLimit|Committed_AS' /proc/meminfo
```

If `Committed_AS` sits close to the proposed limit, either raise `overcommit_ratio` or keep `overcommit_memory=0`. This is the riskiest of the five changes, so make it a separate commit that can be reverted on its own.

## Step 6: validate on the box after deploy

```sh
free -h && cat /proc/swaps
sysctl vm.swappiness vm.overcommit_memory vm.overcommit_ratio
systemctl show victoria-metrics -p MemoryMax -p MemoryHigh -p OOMScoreAdjust -p RestartUSec
journalctl -u victoria-metrics -n 40 --no-pager
grep -E 'CommitLimit|Committed_AS' /proc/meminfo
```

Confirm the VictoriaMetrics startup log now reports a smaller cache budget, that no new OOM entries appear under load, and then trigger a deploy build and watch `fly -t uh workers` and memory usage during the run.

## Notes and out of scope

Security: `/etc/victoria-metrics/env` currently contains `VM_httpAuth_password` in plaintext. When touching the VM role, make sure that file is vault-templated with `no_log: true` and never committed in cleartext.

Stuck builds: builds 35 and 36 are still marked `started` and will need `fly -t uh abort-build` or a rerun. That is separate from the RAM work.

Kernel timing: the OOMs began right after the 16:00 reboot into kernel 6.8.0-138. The memory math explains them on its own, but note the correlation if problems persist after these changes.

Do not apply any of this directly to `soda` as a one-off. Land it in the playbooks and let the pipeline converge the host.
