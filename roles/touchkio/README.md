# touchkio

Installs and auto-updates [TouchKio](https://github.com/leukipp/touchkio), the Home Assistant wall kiosk, and manages its systemd **user** service.

TouchKio ships as a `.deb` per release (`touchkio_<version>_arm64.deb` / `_x64.deb`) and is installed by downloading the release asset and running `apt install` on it — the same approach as the project's `install.sh`. This role mirrors that: on each run it resolves the target version, compares it with the installed one, and only downloads and installs when they differ.

## Variables

| Variable | Default | Description |
| --- | --- | --- |
| `touchkio_version` | `latest` | Version to install. `latest` follows the newest stable GitHub release; pin a tag (e.g. `1.6.0`) to hold the kiosk on one release. |
| `touchkio_user` | `{{ ansible_user_id }}` | Login user that runs the graphical kiosk session and owns the user service. |
| `touchkio_deb_dir` | `/var/tmp/touchkio` | Staging directory for the downloaded `.deb`. |

`touchkio_arch` is derived from `ansible_facts['architecture']` (`aarch64` → `arm64`, `x86_64` → `x64`).

## Notes

- The service unit is written to `~/.config/systemd/user/touchkio.service` (the `.deb` does **not** ship it; `install.sh` creates it). It is enabled and started in the **user** scope, so it starts with the kiosk user's graphical session.
- `~/.config/touchkio/Arguments.json` is **not** managed here. Its MQTT password is AES-256-CBC encrypted with a key derived from `/etc/machine-id`, so it cannot be reproduced from the vault; it is set once by `touchkio --setup`.
- The kiosk needs a graphical session (lightdm autologin into labwc on `ha-kiosk`); this role does not manage the desktop.

## Example

```yaml
- hosts: ha-kiosk
  roles:
    - role: touchkio
```
