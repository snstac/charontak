---

# Ansible: deploy Charontak

Two modes (set `charontak_deploy`):

| Variable | Meaning |
|---------|---------|
| `docker` | Install Docker (+ Compose plugin on Debian targets), clone or sync sources, templated systemd unit wraps `docker compose --profile … up`. |
| `native` | Install cockpit + pip install charontak + plugin + systemd `charontak.service`; best fit for full OS gateways (e.g. AryaOS-style bare metal). |

```sh
cd ansible

ansible-playbook -i inventory/example.yml site.yml -e charontak_deploy=docker
ansible-playbook -i inventory/example.yml site.yml -e charontak_deploy=native
```

Targets are listed under **`inventory/`** (see `inventory/example.yml`). Sources are cloned from **`https://github.com/snstac/charontak.git`** by default; override **`charontak_git_repo`** with **`git@github.com:snstac/charontak.git`** when the gateway uses SSH deploy keys.

### Ansible collections

Uses `ansible.posix.synchronize` when `charontak_sync_from_controller` is true. Install on the controller:

```sh
ansible-galaxy collection install ansible.posix
```

### Config

Edit `templates/charontak.ini.j2` via variables in `roles/charontak/defaults/main.yml` or inventory `vars:`.

Wrap secrets with Ansible Vault (`ansible-vault create group_vars/gateways/vault.yml`) and merge TLS paths there.

### AryaOS / RPM bases

Tune `roles/charontak/vars/` or `ansible_os_family` conditionals inside `tasks/native.yml` when package names diverge.
