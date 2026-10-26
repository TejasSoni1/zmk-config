"""Corne keymap sync helper.

Runs in the background (started at login by setup.ps1). When anything in the repo
changes and stays unchanged for a few seconds, a small window pops up with one-click
options: save to the current branch, save as an experiment branch, or merge a branch
into main. After a push it can wait for GitHub to build the firmware and download the
.uf2 files into firmware/.

    pythonw tools/keymap_sync.pyw            watch for changes (normal mode)
    pythonw tools/keymap_sync.pyw --panel    open the window once, right now
"""
import datetime
import os
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = "main"
POLL_MS = 3000
SETTLE_S = 4
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def run(cmd, check=True):
    r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, creationflags=NO_WINDOW)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}\n{(r.stderr or r.stdout).strip()}")
    return r.stdout.strip()


def git(*a, check=True):
    return run(["git", *a], check)


def gh(*a, check=True):
    return run(["gh", *a], check)


def has_gh():
    return shutil.which("gh") is not None


def current_branch():
    return git("rev-parse", "--abbrev-ref", "HEAD")


def pending_changes():
    return git("status", "--porcelain", check=False)


def remote_has(branch):
    r = subprocess.run(["git", "ls-remote", "--exit-code", "--heads", "origin", branch],
                       cwd=REPO, capture_output=True, text=True, creationflags=NO_WINDOW)
    return r.returncode == 0


def default_message(changes):
    files = [line[3:].strip() for line in changes.splitlines() if line.strip()]
    if not files:
        return ""
    names = ", ".join(os.path.basename(f) for f in files[:3])
    more = f" and {len(files) - 3} more" if len(files) > 3 else ""
    return f"Update {names}{more}"


class Panel:
    def __init__(self, root, on_close):
        self.root = root
        self.on_close = on_close
        self.win = tk.Toplevel(root)
        self.win.title("Corne keymap")
        self.win.attributes("-topmost", True)
        self.win.resizable(False, False)
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self.busy = False

        f = ttk.Frame(self.win, padding=16)
        f.grid()
        self.info = ttk.Label(f, justify="left", wraplength=420)
        self.info.grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(f, text="Message").grid(row=1, column=0, sticky="w", pady=(12, 2))
        self.msg = ttk.Entry(f, width=58)
        self.msg.grid(row=2, column=0, columnspan=3, sticky="we")

        self.dl = tk.BooleanVar(value=has_gh())
        cb = ttk.Checkbutton(f, text="Download firmware when GitHub finishes building", variable=self.dl)
        cb.grid(row=3, column=0, columnspan=3, sticky="w", pady=(10, 0))
        if not has_gh():
            cb.state(["disabled"])

        self.btns = ttk.Frame(f)
        self.btns.grid(row=4, column=0, columnspan=3, sticky="we", pady=(14, 0))
        self.status = ttk.Label(f, text="", foreground="#555", wraplength=420, justify="left")
        self.status.grid(row=5, column=0, columnspan=3, sticky="w", pady=(10, 0))
        self.refresh()
        self.win.lift()
        self.win.focus_force()

    # ---------- UI ----------
    def refresh(self):
        for w in self.btns.winfo_children():
            w.destroy()
        self.branch = current_branch()
        self.changes = pending_changes()
        files = [l[3:] for l in self.changes.splitlines() if l.strip()]
        where = f"On branch {self.branch}."
        what = (f" {len(files)} changed: " + ", ".join(files[:6])) if files else " No unsaved changes."
        self.info.config(text=where + what)
        self.msg.delete(0, "end")
        self.msg.insert(0, default_message(self.changes))

        if files:
            ttk.Button(self.btns, text=f"Save to {self.branch}", command=self.save_here).pack(side="left")
            ttk.Button(self.btns, text="Save as experiment", command=self.save_experiment).pack(side="left", padx=6)
        if self.branch != MAIN:
            ttk.Button(self.btns, text=f"Merge into {MAIN}", command=self.merge).pack(side="left", padx=(0, 6))
        ttk.Button(self.btns, text="Not now", command=self.close).pack(side="right")

    def say(self, text):
        self.root.after(0, lambda: self.status.config(text=text))

    def close(self):
        if self.busy:
            return
        self.win.destroy()
        self.on_close()

    def work(self, fn):
        if self.busy:
            return
        self.busy = True
        for w in self.btns.winfo_children():
            w.state(["disabled"])

        def runner():
            try:
                fn()
            except Exception as e:  # show git's own error text
                self.say(f"Stopped: {e}")
                self.busy = False
                self.root.after(0, self.refresh)
                return
            self.busy = False
            self.root.after(1500, self.close)

        threading.Thread(target=runner, daemon=True).start()

    # ---------- actions ----------
    def _commit_push(self, branch):
        msg = self.msg.get().strip() or "Update keymap"
        if pending_changes():
            self.say("Committing...")
            git("add", "-A")
            git("commit", "-m", msg)
        if remote_has(branch):
            self.say("Pulling latest from GitHub...")
            git("pull", "--rebase", "--autostash", "origin", branch)
        self.say("Pushing...")
        git("push", "-u", "origin", branch)
        self.say(f"Pushed {branch}. GitHub is building the firmware.")
        if self.dl.get():
            self._fetch_firmware(branch)

    def save_here(self):
        b = self.branch
        self.work(lambda: self._commit_push(b))

    def save_experiment(self):
        name = "exp/" + datetime.datetime.now().strftime("%Y%m%d-%H%M")

        def go():
            git("switch", "-c", name)
            self._commit_push(name)

        self.work(go)

    def merge(self):
        b = self.branch

        def go():
            if pending_changes():
                self._commit_push(b)
            self.say(f"Merging {b} into {MAIN}...")
            git("switch", MAIN)
            if remote_has(MAIN):
                git("pull", "--rebase", "origin", MAIN)
            git("merge", "--no-ff", b, "-m", f"Merge {b}")
            git("push", "origin", MAIN)
            self.say(f"Merged {b} into {MAIN}. You're on {MAIN} now.")
            if self.dl.get():
                self._fetch_firmware(MAIN)

        self.work(go)

    def _fetch_firmware(self, branch):
        sha = git("rev-parse", "HEAD")
        self.say("Waiting for the build to start...")
        run_id = None
        for _ in range(60):
            out = gh("run", "list", "--commit", sha, "--workflow", "build.yml",
                     "--json", "databaseId", "--jq", ".[0].databaseId", check=False)
            if out:
                run_id = out
                break
            time.sleep(10)
        if not run_id:
            self.say("Build didn't start. Check the Actions tab on GitHub.")
            return
        self.say("Building firmware (usually 2 to 4 minutes)...")
        r = subprocess.run(["gh", "run", "watch", run_id, "--exit-status"], cwd=REPO,
                           capture_output=True, text=True, creationflags=NO_WINDOW)
        if r.returncode != 0:
            self.say("Build failed. Opening the log on GitHub.")
            gh("run", "view", run_id, "--web", check=False)
            return
        dest = os.path.join(REPO, "firmware", f"{branch.replace('/', '-')}-{sha[:7]}")
        os.makedirs(dest, exist_ok=True)
        gh("run", "download", run_id, "-D", dest)
        self.say(f"Firmware saved to firmware/{os.path.basename(dest)}")
        if os.name == "nt":
            os.startfile(dest)


class Watcher:
    def __init__(self, panel_once=False):
        self.root = tk.Tk()
        self.root.withdraw()
        self.panel = None
        self.last = None
        self.since = 0.0
        self.snoozed = None
        if panel_once:
            self.open_panel(exit_after=True)
        else:
            self.root.after(POLL_MS, self.tick)
        self.root.mainloop()

    def open_panel(self, exit_after=False):
        def closed():
            self.snoozed = pending_changes()
            self.panel = None
            if exit_after:
                self.root.destroy()
        self.panel = Panel(self.root, closed)

    def tick(self):
        try:
            if self.panel is None:
                snap = pending_changes()
                if not snap:
                    self.snoozed = None
                elif snap != self.snoozed:
                    if snap != self.last:
                        self.last, self.since = snap, time.time()
                    elif time.time() - self.since >= SETTLE_S:
                        self.open_panel()
        finally:
            self.root.after(POLL_MS, self.tick)


if __name__ == "__main__":
    Watcher(panel_once="--panel" in sys.argv)
