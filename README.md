# infrastructure

Single entry point for deploying every machine and service in the fleet. This repo owns the inventory, the Ansible config, the dependency list, the vault, and every playbook. Application repos stay code-only and their CI triggers the matching playbook here.

See `plan.md` in the workspace root for the full evaluation and rationale.

## Required CI secrets

Every app repo that calls the reusable deploy workflow must define two secrets. Set them once at the org level, visible to the calling repos:

| Secret | What it is | How to get it |
| --- | --- | --- |
| `VAULT_PASSWORD` | The ansible-vault password | The password for `$ANSIBLE_VAULT_PASSWORD_FILE` |
| `INFRA_TOKEN` | A token that can read this (private) repo | A fine-grained PAT: resource owner `uhlig-it`, repository access `uhlig-it/infrastructure`, permission **Contents: Read-only** |

```command
$ gh secret set vault_password --org uhlig-it --visibility selected --repos <repos> < ~/.ansible-vault-password
$ gh secret set infra_token --org uhlig-it --visibility selected --repos <repos>
```

`INFRA_TOKEN` is required because the workflow checks out this private repo from the calling repo, and the caller's `GITHUB_TOKEN` cannot read another private repo.

> **Fine-grained PATs expire.** When a deploy job fails at the "Check out the infrastructure repo" step, the token has lapsed — generate a new one and re-run `gh secret set infra_token`.

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
- **From GitHub Actions:** the runner has no key, so the reusable deploy workflow decrypts the vault and writes `github.ssh_key` to `~/.ssh/id_rsa`. The app repos pass `vault_password` and `infra_token` through with `secrets: inherit`; see [Required CI secrets](#required-ci-secrets).
- **From Concourse:** the pipelines pass `((github.ssh_key))` from the vault to `lib/tasks/ssh/identity.yml`, which writes it to `ssh-config/id`.

It is the same key everywhere: one deploy key whose public half is in each host's `~/.ssh/authorized_keys`. It is currently stored in the vault as `github.ssh_key` and is also used to clone the repos — a dedicated deploy key would be cleaner, but the material is identical, which is why a workstation deploy needs no extra setup.
