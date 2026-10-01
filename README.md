# infrastructure

Single entry point for deploying every machine and service in the fleet. This repo owns the inventory, the Ansible config, the dependency list, the vault, and every playbook. Application repos stay code-only and their CI triggers the matching playbook here.

See `plan.md` in the workspace root for the full evaluation and rationale.

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
- **From GitHub Actions:** the runner has no key, so the reusable deploy workflow decrypts the vault and writes `github.ssh_key` to `~/.ssh/id_rsa`. Every app repo that calls the workflow must define only the `vault_password` secret; the workflow passes it through with `secrets: inherit`.
- **From Concourse:** the pipelines pass `((github.ssh_key))` from the vault to `lib/tasks/ssh/identity.yml`, which writes it to `ssh-config/id`.

It is the same key everywhere: one deploy key whose public half is in each host's `~/.ssh/authorized_keys`. It is currently stored in the vault as `github.ssh_key` and is also used to clone the repos — a dedicated deploy key would be cleaner, but the material is identical, which is why a workstation deploy needs no extra setup.
