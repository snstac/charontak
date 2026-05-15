/*
 * Charontak Cockpit application — manage charontak.service and /etc/charontak.ini
 * Pattern inspired by https://github.com/snstac/cockpit-adsbcot
 */
/* global cockpit */

const UNIT = "charontak.service";
const CFG_PATH = "/etc/charontak.ini";

function notify(message, priority) {
    const p = priority || "info";
    if (typeof cockpit.notify === "function") {
        cockpit.notify({ message, priority: p });
    } else if (typeof window.alert !== "undefined") {
        window.alert(message);
    }
}

function setStatus(text) {
    document.querySelector("#out-status").textContent = text;
}

function spawn(args, opts) {
    const o = opts || {};
    o.superuser = o.superuser || "try";
    o.err = o.err || "message";
    return cockpit.spawn(args, o);
}

function refreshStatus() {
    setStatus("Loading…");
    spawn(["systemctl", "status", UNIT, "-l", "--no-pager"])
        .then((out) => setStatus(out))
        .catch((ex) => setStatus(ex.toString()));
}

function restartService() {
    setStatus("Restarting…");
    spawn(["systemctl", "restart", UNIT], { superuser: "require" })
        .then(() => refreshStatus())
        .catch((ex) => setStatus(ex.toString()));
}

function recentJournal() {
    setStatus("Loading journal…");
    spawn(["journalctl", "-u", UNIT, "-n", "80", "--no-pager"])
        .then((out) => setStatus(out))
        .catch((ex) => setStatus(ex.toString()));
}

function loadConfig() {
    const ta = document.querySelector("#cfg");
    cockpit
        .file(CFG_PATH)
        .read()
        .then((content) => {
            ta.value = content || "";
        })
        .catch((ex) => {
            ta.value = "";
            notify(String(ex), "danger");
        });
}

function saveConfig() {
    const ta = document.querySelector("#cfg");
    const text = ta.value;
    cockpit
        .file(CFG_PATH)
        .replace(text)
        .then(() => notify("Saved " + CFG_PATH, "info"))
        .catch((ex) => notify(String(ex), "danger"));
}

document.addEventListener("DOMContentLoaded", () => {
    document.querySelector("#btn-status").addEventListener("click", refreshStatus);
    document.querySelector("#btn-restart").addEventListener("click", restartService);
    document.querySelector("#btn-journal").addEventListener("click", recentJournal);
    document.querySelector("#btn-load-cfg").addEventListener("click", loadConfig);
    document.querySelector("#btn-save-cfg").addEventListener("click", saveConfig);
    refreshStatus();
    loadConfig();
});
