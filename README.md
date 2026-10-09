# infrastructure

Single entry point for deploying every machine and service in the fleet. This repo owns the inventory, the Ansible config, the dependency list, the vault, and every playbook. Application repos stay code-only and their CI triggers the matching playbook here.

See `plan.md` in the workspace root for the full evaluation and rationale.

## Required CI secrets

Every app repo that calls the reusable deploy workflow must define three secrets. Set them once at the org level, visible to the calling repos:

| Secret | What it is | How to get it |
| --- | --- | --- |
| `vault_password` | The ansible-vault password | The password for `$ANSIBLE_VAULT_PASSWORD_FILE` |
| `ts_oauth_client_id` | Tailscale OAuth client ID | An OAuth client with the `auth_keys` scope and `tag:ci` (admin console → Trust credentials); see the `CI identity` note in `docs/consolidation-plan.md` |
| `ts_oauth_secret` | Tailscale OAuth client secret | Shown once, when that OAuth client is created |

```command
$ gh secret set vault_password --org uhlig-it --visibility selected --repos <repos> < ~/.ansible-vault-password
$ gh secret set ts_oauth_client_id --org uhlig-it --visibility selected --repos <repos>
$ gh secret set ts_oauth_secret    --org uhlig-it --visibility selected --repos <repos>
```

The two Tailscale secrets let the runner join the tailnet as an ephemeral `tag:ci` node, so it can resolve the MagicDNS names (`opus`, `shop`, …) used in the inventory; those names only resolve inside the tailnet. An OAuth client is used instead of an auth key because auth keys expire (at most every 90 days) while the OAuth client secret does not.

This repo is public, so the workflow can check it out from the calling repo with the default `GITHUB_TOKEN` — no extra token is needed. (A reusable workflow in a *private* repo cannot be called from another repo at all, which is why this one is public.)

## Layout

- `inventory/hosts.yml` — the single source of truth for machines and groups.
- `inventory/group_vars/`, `inventory/host_vars/` — variables, including the one vault.
- `playbooks/machines/` — host provisioning, one file per host or machine group.
- `playbooks/fleet/` — cross-host concerns (`tailscale`, `metrics`).
- `playbooks/services/` — application deployments, one file per service.
- `playbooks/site.yml` — runs everything.
- `roles/` — host-specific roles that are not shared library.

## Usage

```command
$ ansible-galaxy install -r requirements.yml
$ ansible-playbook playbooks/site.yml --check
$ ansible-playbook playbooks/machines/opus.yml
$ ansible-playbook playbooks/services/kehrkraft.yml
```

## Secrets

One vaulted file, `inventory/group_vars/all/secrets.yml`, holds every secret. Copy `secrets.yml.example` and encrypt it:

```command
$ cp inventory/group_vars/all/secrets.yml.example inventory/group_vars/all/secrets.yml
$ ansible-vault encrypt inventory/group_vars/all/secrets.yml
```

The vault password is read from the file named by `$ANSIBLE_VAULT_PASSWORD_FILE`. Locally:

```command
$ echo 'the-password' > ~/.ansible-vault-password
$ chmod 600 ~/.ansible-vault-password
$ export ANSIBLE_VAULT_PASSWORD_FILE=~/.ansible-vault-password
```

In CI, the reusable deploy workflow writes the `vault_password` secret to a file and points `$ANSIBLE_VAULT_PASSWORD_FILE` at it.

## SSH access

How Ansible reaches the hosts depends on where it runs:

- **From your workstation:** nothing to configure. Ansible uses your SSH agent and `~/.ssh/config`, exactly as `ssh <host>` would. No key is stored in this repo.
- **From GitHub Actions:** the runner has no key, so the reusable deploy workflow decrypts the vault and writes `github.ssh_key` to `~/.ssh/id_rsa`. The app repos pass `vault_password` through with `secrets: inherit`; see [Required CI secrets](#required-ci-secrets).
- **From Concourse:** the pipelines pass `((github.ssh_key))` from the vault to `lib/tasks/ssh/identity.yml`, which writes it to `ssh-config/id`.

It is the same key everywhere: one deploy key whose public half is in each host's `~/.ssh/authorized_keys`. It is currently stored in the vault as `github.ssh_key` and is also used to clone the repos — a dedicated deploy key would be cleaner, but the material is identical, which is why a workstation deploy needs no extra setup.
