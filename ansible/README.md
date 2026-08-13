---

# Ansible: deploy COTBridge

Two modes (set `cotbridge_deploy`):

| Variable | Meaning |
|---------|---------|
| `docker` | Install Docker (+ Compose plugin on Debian targets), clone or sync sources, templated systemd unit wraps `docker compose --profile … up`. |
| `native` | Install cockpit + pip install cotbridge + plugin + systemd `cotbridge.service`; best fit for full OS gateways (e.g. AryaOS-style bare metal). |

```sh
cd ansible

ansible-playbook -i inventory/example.yml site.yml -e cotbridge_deploy=docker
ansible-playbook -i inventory/example.yml site.yml -e cotbridge_deploy=native
```

Targets are listed under **`inventory/`** (see `inventory/example.yml`). Sources are cloned from **`https://github.com/snstac/cotbridge.git`** by default; override **`cotbridge_git_repo`** with **`git@github.com:snstac/cotbridge.git`** when the gateway uses SSH deploy keys.

### Ansible collections

Uses `ansible.posix.synchronize` when `cotbridge_sync_from_controller` is true. Install on the controller:

```sh
ansible-galaxy collection install ansible.posix
```

### Config

Edit `templates/cotbridge.ini.j2` via variables in `roles/cotbridge/defaults/main.yml` or inventory `vars:`.

Wrap secrets with Ansible Vault (`ansible-vault create group_vars/gateways/vault.yml`) and merge TLS paths there.

### AryaOS / RPM bases

Tune `roles/cotbridge/vars/` or `ansible_os_family` conditionals inside `tasks/native.yml` when package names diverge.
