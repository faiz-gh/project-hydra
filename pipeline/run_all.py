#!/usr/bin/env python3
"""Run the whole two-engine experiment as one terminal UI.

For each engine in turn -- CockroachDB, then PostgreSQL/Patroni:

    terraform plan -out plan.out -var=database_engine=<engine>
    terraform apply plan.out                 (any error aborts)
    wait for all six VMs to finish cloud-init
    ./run-experiment.sh --engine <engine> --profile <profile>
    tailscale logout on every VM
    terraform destroy -auto-approve
    delete the VMs' devices from the tailnet (Tailscale API), and verify

and then ``./generate_insights.sh --profile <profile>`` plus the engine
comparison. DB_URI needs no editing between engines: run-experiment.sh derives
it from --engine.

If a step fails, or you press Ctrl-C, while VMs may be up, it asks whether to
destroy them (and clean the tailnet) or leave them up for debugging.

    ./run-experiment.sh                       # this, from a terminal
    pipeline/run_all.py --profile smoke --yes # no setup form
    pipeline/run_all.py --engines postgresql  # resume with the second engine
    pipeline/run_all.py --cleanup --engine cockroachdb
    pipeline/run_all.py --tailscale-list      # which devices would be deleted
    pipeline/run_all.py --dry-run             # the UI, with no cloud calls

A full thesis-extended run takes hours; start it inside tmux so it survives a
closed terminal. Everything is also written to runs/_logs/pipeline-<stamp>.log.
"""

from __future__ import annotations

import argparse
import curses
import locale
import os
import re
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from crdblab.config import load_env_file
from demo.replay import (
    ASCII_GLYPHS,
    GLYPHS,
    Screen,
    _provider,
    segments,
    tf_resources,
)
from pipeline import nodes, tailscale

TF_DIR = REPO / "terraform"
RUNS = REPO / "runs"
LOGS = RUNS / "_logs"
PROFILES = REPO / "profiles"
ENGINES = ("cockroachdb", "postgresql")
ENGINE_NAME = {"cockroachdb": "CockroachDB", "postgresql": "PostgreSQL/Patroni"}

#: cloud-init on the slowest cloud has taken ~8 minutes; this bounds the wait
#: rather than letting a VM that never boots hang the pipeline.
NODE_WAIT_S = 15 * 60

#: How long a child gets to stop after SIGINT before it is terminated. Terraform
#: uses SIGINT to stop gracefully (and to cancel a pending remote run).
STOP_GRACE_S = 90

# Anything that is not an SGR colour sequence (cursor moves, erase-line, ...)
# is dropped; segments() renders only SGR.
NON_SGR = re.compile(r"\x1b\[[0-9;?]*[A-La-ln-z]|\x1b\][^\x07]*\x07|\x1b[()][A-Z0-9]")
ANY_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
TF_DONE = re.compile(r"\.([a-z0-9]+_[a-z0-9_]+)\.[^.:\s]+: (Creation|Destruction) complete")

#: run-experiment.sh's `==> <step>` headers -> substage. Most specific first.
SUBSTAGE_OF = [
    ("Checking the workstation", "checks"), ("Resolving topology", "checks"),
    ("Checking the testbed", "testbed"), ("Working set", "load"),
    ("Phase IV", "p4"), ("Phase III", "p3"), ("Phase II", "p2"), ("Phase I", "p1"),
    ("Confirming", "p4"), ("Restoring", "p4"),
    ("Validating", "end"), ("Analysis", "end"), ("Figures", "end"), ("Done", "end"),
]
SUBSTAGES = [
    ("checks", "workstation & topology"), ("testbed", "testbed health"),
    ("load", "working set"), ("p1", "Phase I   network"),
    ("p2", "Phase II  benchmark"), ("p3", "Phase III partition"),
    ("p4", "Phase IV  process kill"), ("end", "validate & figures"),
]


class StepFailed(Exception):
    pass


class Aborted(Exception):
    pass


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def fmt_dur(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}h{s % 3600 // 60:02d}m" if s >= 3600 else f"{s // 60}m{s % 60:02d}s"


def profiles() -> list[str]:
    names = sorted(p.stem for p in PROFILES.glob("*.yaml"))
    order = {"smoke": 0, "thesis": 1, "thesis-extended": 2}
    return sorted(names, key=lambda n: (order.get(n, 9), n))


# ----------------------------------------------------------------------------
# state shared by the runner thread and the UI
# ----------------------------------------------------------------------------

class Step:
    def __init__(self, key: str, title: str, engine: str = ""):
        self.key, self.title, self.engine = key, title, engine
        self.status = "pending"            # pending | active | done | failed | skipped
        self.started = self.ended = 0.0

    @property
    def elapsed(self) -> float:
        if not self.started:
            return 0.0
        return (self.ended or time.monotonic()) - self.started


class State:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.lock = threading.RLock()
        self.log: list[str] = []
        self.steps: list[Step] = [Step("pre", "preflight")]
        for e in args.engine_list:
            for key, title in (("plan", "terraform plan"), ("apply", "terraform apply"),
                               ("wait", "wait for 6 VMs"), ("run", "./run-experiment.sh"),
                               ("logout", "tailscale logout"), ("destroy", "terraform destroy"),
                               ("purge", "free tailscale names")):
                self.steps.append(Step(f"{e}:{key}", title, e))
        self.steps.append(Step("insights", "insights & comparison"))
        self.substages = [s for s in SUBSTAGES
                          if args.chaos or s[0] not in ("p3", "p4")]
        self.sub_done: set[str] = set()
        self.sub_active = ""
        self.panel: dict = {"kind": "idle", "label": ""}
        self.tf_totals: dict = {}
        for _, rtype in tf_resources():
            self.tf_totals[_provider(rtype)] = self.tf_totals.get(_provider(rtype), 0) + 1
        self.tf_counts = dict.fromkeys(self.tf_totals, 0)
        self.started = time.monotonic()
        self.finished = False
        self.result: dict = {}
        self.abort = threading.Event()
        self.cleaning = False
        # Held open for the pipeline's lifetime; closed at interpreter exit.
        self.logfile = open(LOGS / f"pipeline-{utc_stamp()}.log", "a", buffering=1)  # noqa: SIM115

    def step(self, key: str) -> Step:
        return next(s for s in self.steps if s.key == key)

    @property
    def active(self) -> Step | None:
        return next((s for s in self.steps if s.status == "active"), None)

    def emit(self, text: str) -> None:
        text = NON_SGR.sub("", text.rstrip("\n").split("\r")[-1])
        with self.lock:
            self.log.append(text)
            if len(self.log) > 50_000:
                del self.log[:10_000]
        self.logfile.write(ANY_ANSI.sub("", text) + "\n")


# ----------------------------------------------------------------------------
# the pipeline itself (runs on a worker thread)
# ----------------------------------------------------------------------------

class Runner:
    def __init__(self, state: State, ui: BaseUI):
        self.s = state
        self.ui = ui
        self.args = state.args
        self.proc: subprocess.Popen | None = None
        self.deployed: str | None = None   # engine whose VMs may exist right now
        self.env = dict(os.environ, PYTHONUNBUFFERED="1", CRDBLAB_COLOR="1")

    # -- plumbing -----------------------------------------------------------
    def say(self, text: str, colour: str = "") -> None:
        code = {"green": "32", "yellow": "33", "red": "31", "dim": "2", "bold": "1"}.get(colour)
        self.s.emit(f"\x1b[{code}m{text}\x1b[0m" if code else text)

    def check_abort(self) -> None:
        if self.s.abort.is_set() and not self.s.cleaning:
            raise Aborted()

    def cmd(self, argv: list[str], cwd: Path = REPO, fake: list[str] | None = None) -> None:
        """Run ``argv``, streaming its output into the log; raise StepFailed on non-zero."""
        self.check_abort()
        shown = " ".join(argv)
        self.say(f"\x1b[32m{GLYPHS['prompt']}\x1b[0m \x1b[1m{shown}\x1b[0m")
        if self.args.dry_run:
            self._fake(argv, fake or [])
            return
        # A new session: the terminal's Ctrl-C reaches only this process, which
        # decides what the child gets (SIGINT, so terraform stops gracefully).
        self.proc = subprocess.Popen(argv, cwd=cwd, env=self.env, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     start_new_session=True)
        stopper = threading.Thread(target=self._stop_on_abort, args=(self.proc,), daemon=True)
        stopper.start()
        buf = b""
        assert self.proc.stdout is not None
        while True:
            chunk = self.proc.stdout.read1(65536) if hasattr(self.proc.stdout, "read1") \
                else self.proc.stdout.read(4096)
            if not chunk:
                break
            buf += chunk
            *lines, buf = buf.split(b"\n")
            for line in lines:
                self._line(line.decode(errors="replace"))
        if buf:
            self._line(buf.decode(errors="replace"))
        rc = self.proc.wait()
        self.proc = None
        if self.s.abort.is_set() and not self.s.cleaning:
            raise Aborted()
        if rc != 0:
            raise StepFailed(f"`{shown}` exited with status {rc}")

    def _stop_on_abort(self, proc: subprocess.Popen) -> None:
        while proc.poll() is None:
            if self.s.abort.is_set() and not self.s.cleaning:
                try:
                    os.killpg(proc.pid, signal.SIGINT)
                    self.s.emit("\x1b[33m  !!  interrupt sent; waiting for the command to stop\x1b[0m")
                    proc.wait(STOP_GRACE_S)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                return
            time.sleep(0.2)

    def _line(self, text: str) -> None:
        self.s.emit(text)
        plain = ANY_ANSI.sub("", text)
        m = TF_DONE.search(plain)
        if m:
            prov = _provider(m.group(1))
            with self.s.lock:
                delta = 1 if m.group(2) == "Creation" else -1
                total = self.s.tf_totals.get(prov, 0)
                self.s.tf_counts[prov] = max(0, min(total, self.s.tf_counts.get(prov, 0) + delta))
        if plain.startswith("==> "):
            head = plain[4:]
            for prefix, sub in SUBSTAGE_OF:
                if head.startswith(prefix):
                    with self.s.lock:
                        if self.s.sub_active and self.s.sub_active != sub:
                            self.s.sub_done.add(self.s.sub_active)
                        self.s.sub_active = sub
                    break

    def _fake(self, argv: list[str], lines: list[str]) -> None:
        for line in lines:
            self.check_abort()
            if line.startswith("@fail"):
                raise StepFailed(f"`{' '.join(argv)}` exited with status 1 (dry run)")
            self._line(line)
            time.sleep(self.args.dry_run_delay)

    # -- steps --------------------------------------------------------------
    def begin(self, key: str) -> Step:
        self.check_abort()
        step = self.s.step(key)
        with self.s.lock:
            step.status, step.started = "active", time.monotonic()
        name = ENGINE_NAME.get(step.engine, "")
        self.say("")
        self.say(f"━━ {step.title}" + (f"  ·  {name}" if name else ""), "bold")
        if self.args.dry_run and self.args.dry_run_fail == key:
            raise StepFailed(f"forced failure at {key} (--dry-run-fail)")
        return step

    def end(self, step: Step, status: str = "done") -> None:
        with self.s.lock:
            step.status, step.ended = status, time.monotonic()
        self.say(f"  ok  {step.title} ({fmt_dur(step.elapsed)})", "green")

    def run(self) -> None:
        try:
            self.preflight()
            for engine in self.args.engine_list:
                self.one_engine(engine)
            self.insights()
            self.s.result["ok"] = True
        except (StepFailed, Aborted, tailscale.TailscaleError) as e:
            self.fail(e)
        except Exception as e:  # noqa: BLE001 -- an orchestrator bug must still offer cleanup
            self.fail(StepFailed(f"internal error: {type(e).__name__}: {e}"))
        finally:
            with self.s.lock:
                self.s.finished = True

    def preflight(self) -> None:
        step = self.begin("pre")
        crdblab = REPO / ".venv" / "bin" / "crdblab"
        if not crdblab.exists():
            raise StepFailed(f"{crdblab} not found; run: python3 -m venv .venv && "
                             ".venv/bin/python -m pip install -e \".[dev]\"")
        self.say("  ok  harness installed", "green")
        for tool in ("terraform", "tailscale", "ssh"):
            if not self.args.dry_run and not _which(tool):
                raise StepFailed(f"`{tool}` is not on PATH")
        self.cmd(["tailscale", "status", "--peers=false"],
                 fake=["100.64.0.1   workstation   you@   macOS   -"])
        self.say("  ok  tailscale up", "green")
        if not (TF_DIR / ".terraform").exists():
            self.cmd(["terraform", "init", "-input=false"], cwd=TF_DIR,
                     fake=["Terraform has been successfully initialized!"])
        self.say("  checking TS_API_KEY and clearing stale testbed devices", "dim")
        self.purge()
        self.end(step)

    def one_engine(self, e: str) -> None:
        step = self.begin(f"{e}:plan")
        self.cmd(["terraform", "plan", "-input=false", "-out", "plan.out",
                  f"-var=database_engine={e}"], cwd=TF_DIR, fake=_fake_plan(e))
        self.end(step)

        step = self.begin(f"{e}:apply")
        with self.s.lock:
            self.s.tf_counts = dict.fromkeys(self.s.tf_totals, 0)
            self.s.panel = {"kind": "tf", "label": f"provisioning {ENGINE_NAME[e]}: 5 nodes + client",
                            "word": "created", "engine": e}
        self.deployed = e   # from here on a failure may leave VMs behind
        self.cmd(["terraform", "apply", "-input=false", "plan.out"], cwd=TF_DIR,
                 fake=_fake_tf("apply"))
        self.end(step)

        step = self.begin(f"{e}:wait")
        self.wait_for_vms(e)
        self.end(step)

        step = self.begin(f"{e}:run")
        with self.s.lock:
            self.s.sub_done, self.s.sub_active = set(), ""
            self.s.panel = {"kind": "measure", "engine": e}
        before = {p.name for p in RUNS.glob("*_*") if p.is_dir()}
        argv = ["./run-experiment.sh", "--engine", e, "--profile", self.args.profile]
        if not self.args.chaos:
            argv.append("--no-chaos")
        self.cmd(argv, fake=_fake_run(self.args.chaos))
        with self.s.lock:
            if self.s.sub_active:
                self.s.sub_done.add(self.s.sub_active)
            self.s.sub_active = ""
        new = sorted(p.name for p in RUNS.glob("*_bench_cluster") if p.name not in before)
        if new:
            self.s.result[f"bench:{e}"] = new[-1]
        self.end(step)

        self.teardown(e)

    def teardown(self, e: str) -> None:
        step = self.begin(f"{e}:logout")
        self.logout()
        self.end(step)

        step = self.begin(f"{e}:destroy")
        self.destroy(e)
        self.end(step)

        step = self.begin(f"{e}:purge")
        self.purge()
        self.end(step)

    # -- shared building blocks (also used by cleanup) ------------------------
    def logout(self) -> None:
        if self.args.dry_run:
            for h in nodes.hostnames():
                self.say(f"      {h}: logging out (dry run)", "dim")
            return
        tailscale.logout_vms(lambda t: self.say(f"      {t}", "dim"))

    def destroy(self, e: str) -> None:
        with self.s.lock:
            self.s.tf_counts = dict(self.s.tf_totals)
            self.s.panel = {"kind": "tf", "label": f"tearing down {ENGINE_NAME[e]}",
                            "word": "remaining", "engine": e}
        self.cmd(["terraform", "destroy", "-auto-approve", "-input=false",
                  f"-var=database_engine={e}"], cwd=TF_DIR, fake=_fake_tf("destroy"))
        self.deployed = None

    def purge(self) -> None:
        self.check_abort()
        if self.args.dry_run:
            self.say("      no testbed devices in the tailnet (dry run)", "dim")
            return
        tailscale.purge_devices(lambda t: self.say(f"      {t}", "dim"))

    def wait_for_vms(self, e: str) -> None:
        vms = nodes.vms()
        ready: set[str] = set()
        with self.s.lock:
            self.s.panel = {"kind": "wait", "label": "waiting for SSH and cloud-init on every VM",
                            "done": 0, "total": len(vms), "deadline": time.monotonic() + NODE_WAIT_S,
                            "engine": e}
        if self.args.dry_run:
            for vm in vms:
                time.sleep(self.args.dry_run_delay * 4)
                self.check_abort()
                ready.add(vm.host)
                self.s.panel["done"] = len(ready)
                self.say(f"  ok  {vm.host}: cloud-init done", "green")
            return
        deadline = time.monotonic() + NODE_WAIT_S
        problems: dict[str, str] = {}

        def one(vm: nodes.Vm) -> None:
            while time.monotonic() < deadline and not self.s.abort.is_set():
                if nodes.ssh(vm, "true", timeout=20).returncode == 0:
                    break
                time.sleep(10)
            else:
                problems[vm.host] = "never became reachable over SSH"
                return
            left = max(30, deadline - time.monotonic())
            r = nodes.ssh(vm, "cloud-init status --wait >/dev/null 2>&1; cloud-init status",
                          timeout=left)
            status = (r.stdout or "").strip().splitlines()[-1:] or [r.stderr.strip()]
            if "done" in status[0]:
                self.say(f"  ok  {vm.host}: cloud-init done", "green")
                with self.s.lock:
                    ready.add(vm.host)
                    self.s.panel["done"] = len(ready)
            else:
                problems[vm.host] = f"cloud-init: {status[0] or 'no answer'}"

        with ThreadPoolExecutor(max_workers=len(vms)) as pool:
            list(pool.map(one, vms))
        self.check_abort()
        if problems:
            raise StepFailed("VMs not ready: " + "; ".join(f"{h}: {p}" for h, p in problems.items())
                             + "  (see /var/log/cloud-init-output.log on the node)")

    def insights(self) -> None:
        step = self.begin("insights")
        with self.s.lock:
            self.s.panel = {"kind": "idle", "label": "drawing the insights catalogue"}
        before = {p.name for p in (REPO / "insights").glob("*")}
        self.cmd(["./generate_insights.sh", "--profile", self.args.profile],
                 fake=["==> Drawing the catalogue", "  ok  every chart drawn"])
        new = sorted(p for p in (REPO / "insights").glob(f"*_{self.args.profile}")
                     if p.name not in before)
        if new:
            self.s.result["insights"] = str(new[-1].relative_to(REPO))
        crdb, pg = self.s.result.get("bench:cockroachdb"), self.s.result.get("bench:postgresql")
        if crdb and pg:
            try:
                self.cmd([".venv/bin/crdblab", "analyze", "engine-comparison",
                          "--crdb", crdb, "--pg", pg], fake=["(engine comparison table)"])
            except StepFailed as e:
                # The runs are already measured and charted; a comparison that
                # refuses (e.g. a hardware mismatch) is reported, not fatal.
                self.say(f"  !!  engine comparison did not complete: {e}", "yellow")
        elif not self.args.dry_run:
            self.say("  !!  engine comparison skipped: needs a bench run from each engine "
                     "in this pipeline", "yellow")
        self.end(step)

    # -- failure --------------------------------------------------------------
    def fail(self, err: Exception) -> None:
        active = self.s.active
        with self.s.lock:
            if active:
                active.status, active.ended = "failed", time.monotonic()
        reason = "aborted by you" if isinstance(err, Aborted) else str(err)
        self.say("")
        self.say(f"FAILED: {reason}", "red")
        self.s.result["error"] = reason
        if self.proc is not None and self.proc.poll() is None:
            self.proc.wait()
        engine = self.deployed
        if not engine:
            return
        choice = self.args.on_failure
        if choice == "ask":
            choice = self.ui.ask(
                "Stopped with VMs possibly still running",
                [f"step: {active.title if active else '?'}  ({ENGINE_NAME[engine]})",
                 f"reason: {reason}", "",
                 "d  destroy them now and free their Tailscale names",
                 "l  leave them up for debugging"],
                {"d": "destroy", "l": "leave"})
        if choice == "destroy":
            self.cleanup(engine)
        else:
            cmd = f"pipeline/run_all.py --cleanup --engine {engine}"
            self.s.result["cleanup_cmd"] = cmd
            self.say(f"  !!  VMs left up. When done:  {cmd}", "yellow")

    def cleanup(self, engine: str) -> None:
        self.s.cleaning = True
        self.say("")
        self.say(f"━━ cleanup  ·  {ENGINE_NAME[engine]}", "bold")
        try:
            self.logout()
            self.destroy(engine)
            self.purge()
            self.say("  ok  destroyed and tailnet cleaned", "green")
            self.s.result["cleaned"] = True
        except (StepFailed, tailscale.TailscaleError) as e:
            cmd = f"pipeline/run_all.py --cleanup --engine {engine}"
            self.s.result["cleanup_cmd"] = cmd
            self.say(f"FAILED: cleanup: {e}", "red")
            self.say(f"  !!  retry with:  {cmd}", "yellow")
        finally:
            self.s.cleaning = False


def _which(tool: str) -> bool:
    return any((Path(d) / tool).exists() for d in os.environ.get("PATH", "").split(os.pathsep))


# -- dry-run output ------------------------------------------------------------

def _fake_plan(e: str) -> list[str]:
    n = len(tf_resources())
    return ["Running plan in HCP Terraform. Output will stream here.",
            f"\x1b[2m# var.database_engine = \"{e}\"\x1b[0m",
            f"\x1b[1mPlan: {n} to add, 0 to change, 0 to destroy.\x1b[0m"]


def _fake_tf(action: str) -> list[str]:
    word = "Creation" if action == "apply" else "Destruction"
    out = [f"\x1b[1m{addr}: {word} complete after 12s\x1b[0m" for addr, _ in tf_resources()]
    n = len(out)
    out.append(f"\x1b[1;32mApply complete! Resources: {n} added, 0 changed, 0 destroyed.\x1b[0m"
               if action == "apply" else f"\x1b[1;32mDestroy complete! Resources: {n} destroyed.\x1b[0m")
    return out


def _fake_run(chaos: bool) -> list[str]:
    heads = ["Checking the workstation", "Resolving topology", "Checking the testbed",
             "Working set", "Phase I — network substrate", "Phase II — benchmark, five-node cluster"]
    if chaos:
        heads += ["Phase III — heal-able partition", "Phase IV — process kill"]
    heads += ["Validating every run", "Analysis", "Figures", "Done in 0m 00s"]
    out = []
    for h in heads:
        out += [f"\x1b[1m==> {h}\x1b[0m", "\x1b[32m  ok\x1b[0m  (dry run)", "\x1b[2m      ...\x1b[0m"]
    return out


# ----------------------------------------------------------------------------
# UIs
# ----------------------------------------------------------------------------

class BaseUI:
    def __init__(self, state: State):
        self.s = state

    def ask(self, title: str, lines: list[str], options: dict[str, str]) -> str:
        raise NotImplementedError


class PlainUI(BaseUI):
    """Scrolling output, for a pipe, a small terminal, or --plain."""

    def __init__(self, state: State):
        super().__init__(state)
        self.shown = 0
        self.last_active = ""
        self.colour = sys.stdout.isatty()

    def pump(self) -> None:
        with self.s.lock:
            lines = self.s.log[self.shown:]
            self.shown = len(self.s.log)
        for line in lines:
            print(line if self.colour else ANY_ANSI.sub("", line), flush=True)

    def ask(self, title, lines, options):
        self.pump()
        print(f"\n{title}")
        for line in lines:
            print(f"  {line}")
        if not sys.stdin.isatty():
            print("  (no terminal to ask on: leaving the VMs up)")
            return "leave"
        while True:
            try:
                k = input(f"choice [{'/'.join(options)}]: ").strip().lower()
            except EOFError:
                return "leave"
            if k in options:
                return options[k]


class TUI(BaseUI):
    """The full-screen UI: reuses demo/replay.py's Screen for colours and drawing."""

    def __init__(self, state: State, scr, ascii_only: bool):
        super().__init__(state)
        self.v = _View(scr, ascii_only)
        self.scroll = 0
        self.pending: tuple | None = None
        self.reply = threading.Event()
        self.reply_value = ""

    def ask(self, title, lines, options):
        self.reply.clear()
        self.pending = (title, lines, options)
        self.reply.wait()
        self.pending = None
        return self.reply_value

    def loop(self, runner_thread: threading.Thread) -> None:
        scr = self.v.scr
        scr.nodelay(True)
        while True:
            try:
                self.keys()
                self.v.draw(self.s, self.scroll, self.pending)
                if self.s.finished and not runner_thread.is_alive():
                    break
                time.sleep(0.05)
            except KeyboardInterrupt:
                self.interrupt()
        self.v.draw(self.s, self.scroll, None)
        self.finale()

    def interrupt(self) -> None:
        if self.pending or self.s.cleaning:
            return
        self.s.abort.set()

    def keys(self) -> None:
        while True:
            k = self.v.scr.getch()
            if k == -1:
                return
            if self.pending:
                ch = chr(k).lower() if 0 <= k < 256 else ""
                if ch in self.pending[2]:
                    self.reply_value = self.pending[2][ch]
                    self.reply.set()
                continue
            page = max(1, self.v.scr.getmaxyx()[0] - 12)
            if k in (curses.KEY_UP, ord("k")):
                self.scroll += 1
            elif k in (curses.KEY_DOWN, ord("j")):
                self.scroll = max(0, self.scroll - 1)
            elif k == curses.KEY_PPAGE:
                self.scroll += page
            elif k == curses.KEY_NPAGE:
                self.scroll = max(0, self.scroll - page)
            elif k in (curses.KEY_END, ord("G")):
                self.scroll = 0
            elif k in (ord("q"), ord("Q")) and not self.s.cleaning:
                self.s.abort.set()

    def finale(self) -> None:
        scr = self.v.scr
        scr.nodelay(False)
        dash = self.s.result.get("insights")
        while True:
            self.v.draw(self.s, self.scroll, None, done=True)
            k = scr.getch()
            if k in (ord("q"), ord("Q"), 27, 10, 13):
                return
            if k in (ord("o"), ord("O")) and dash and _which("open"):
                subprocess.run(["open", str(REPO / dash / "dashboard.html")], check=False)
            elif k in (curses.KEY_UP, ord("k")):
                self.scroll += 1
            elif k in (curses.KEY_DOWN, ord("j")):
                self.scroll = max(0, self.scroll - 1)
            elif k == curses.KEY_PPAGE:
                self.scroll += 10
            elif k == curses.KEY_NPAGE:
                self.scroll = max(0, self.scroll - 10)
            elif k in (curses.KEY_END, ord("G")):
                self.scroll = 0


class _View(Screen):
    """Draws live pipeline state with replay.Screen's colours and primitives.

    Only ``put``, ``bar``, ``attr`` and ``_colours`` are inherited; the playback
    machinery of Screen is not used, so its constructor is not either.
    """

    def __init__(self, scr, ascii_only: bool):
        self.scr = scr
        self.G = ASCII_GLYPHS if ascii_only else GLYPHS
        self._colours()

    def draw(self, s: State, scroll: int, question: tuple | None, done: bool = False) -> None:
        self.scr.erase()
        h, w = self.scr.getmaxyx()
        side = 40 if w >= 120 else 34 if w >= 100 else 0
        panel_h = 7
        self._top(s, w)
        if side:
            self._steps(s, 1, 0, side, h - 2)
        self._log(s, 1, side, w - side, h - 2 - panel_h, scroll)
        self._live(s, h - 1 - panel_h, side, w - side, panel_h, done)
        help_ = (" o open dashboard   ↑/↓ PgUp/PgDn scroll   q quit" if done else
                 " ↑/↓ PgUp/PgDn scroll   End follow   q / Ctrl-C abort")
        if scroll and not done:
            help_ += f"   [scrolled back {scroll}]"
        self.put(h - 1, 0, help_.ljust(w - 1), self.C["grey"])
        if question:
            self._modal(question, h, w)
        self.scr.refresh()

    def _top(self, s: State, w: int) -> None:
        self.put(0, 0, " " * w, self.C["bar"])
        dot = self.G["pending"]
        a = s.args
        left = (f"project-hydra  {dot}  {a.profile}  {dot}  "
                + " → ".join(ENGINE_NAME[e].split("/")[0] for e in a.engine_list)
                + ("  (dry run)" if a.dry_run else ""))
        self.put(0, 1, left, self.C["bar"] | curses.A_BOLD)
        n_done = sum(st.status == "done" for st in s.steps)
        right = f"step {n_done}/{len(s.steps)}   elapsed {fmt_dur(time.monotonic() - s.started)} "
        self.put(0, max(0, w - len(right)), right, self.C["bar"])

    def _steps(self, s: State, y0: int, x0: int, width: int, height: int) -> None:
        G, y = self.G, y0 + 1
        groups: list[tuple[str, list[Step]]] = []
        for st in s.steps:
            if groups and groups[-1][0] == st.engine and st.engine:
                groups[-1][1].append(st)
            else:
                groups.append((st.engine, [st]))
        for engine, items in groups:
            if engine:
                colour = self.C["crdb"] if engine == "cockroachdb" else self.C["pg"]
                all_done = all(i.status == "done" for i in items)
                touched = any(i.status != "pending" for i in items)
                head = ENGINE_NAME[engine]
                if all_done:
                    total = sum(i.elapsed for i in items)
                    self.put(y, x0 + 1, G["done"], self.C["green"])
                    self.put(y, x0 + 3, f"{head}  {fmt_dur(total)}"[: width - 4], colour | curses.A_BOLD)
                    y += 2
                    continue
                self.put(y, x0 + 1, head[: width - 2], colour | (curses.A_BOLD if touched else 0))
                y += 1
            for st in items:
                y = self._step_row(s, st, y, x0 + (3 if engine else 1), width)
                if st.key.endswith(":run") and st.status in ("active", "failed"):
                    for sub, label in s.substages:
                        d, a = sub in s.sub_done, sub == s.sub_active
                        mark = G["done"] if d else (G["sub"] if a else " ")
                        attr = self.C["green"] if d else (self.C["yellow"] | curses.A_BOLD if a else self.C["grey"])
                        self.put(y, x0 + 7, f"{mark} {label}"[: width - 8], attr)
                        y += 1
            y += 1
            if y >= y0 + height:
                break
        for yy in range(y0, y0 + height):
            self.put(yy, x0 + width - 1, G["vline"], self.C["grey"])

    def _step_row(self, s: State, st: Step, y: int, x: int, width: int) -> int:
        G = self.G
        mark, attr = {
            "done": (G["done"], self.C["green"]),
            "active": (G["active"], self.C["yellow"] | curses.A_BOLD),
            "failed": ("x", self.C["red"] | curses.A_BOLD),
        }.get(st.status, (G["pending"], self.C["grey"]))
        self.put(y, x, mark, attr)
        text_attr = curses.A_BOLD if st.status == "active" else (
            self.C["red"] if st.status == "failed" else 0 if st.status == "done" else self.C["grey"])
        self.put(y, x + 2, st.title[: width - x - 11], text_attr)
        if st.started:
            t = fmt_dur(st.elapsed)
            self.put(y, width - len(t) - 2, t, self.C["grey"])
        return y + 1

    def _log(self, s: State, y0: int, x0: int, width: int, height: int, scroll: int) -> None:
        width -= 2
        with s.lock:
            source = s.log[-(height + scroll + 400):]
        rows: list[list] = []
        for raw in reversed(source):
            flat, line, used = [], [], 0
            for text, style in segments(raw):
                while text:
                    room = width - used
                    chunk, text = text[:room], text[room:]
                    line.append((chunk, style))
                    used += len(chunk)
                    if used >= width:
                        flat.append(line)
                        line, used = [], 0
            if line or not flat:
                flat.append(line)
            rows = flat + rows
            if len(rows) >= height + scroll:
                break
        end = len(rows) - scroll
        rows = rows[max(0, end - height): max(0, end)]
        for i, line in enumerate(rows):
            x = x0 + 1
            for text, style in line:
                self.put(y0 + i, x, text, self.attr(style))
                x += len(text)

    def _live(self, s: State, y0: int, x0: int, width: int, height: int, done: bool) -> None:
        self.put(y0, x0, self.G["hline"] * width, self.C["grey"])
        inner = width - 4
        if done:
            self._summary(s, y0, x0)
            return
        p, st = s.panel, s.active
        colour = "crdb" if p.get("engine") == "cockroachdb" else "pg"
        if p["kind"] == "tf" and ((st and st.key.endswith((":apply", ":destroy"))) or s.cleaning):
            self.put(y0 + 1, x0 + 2, p["label"], curses.A_BOLD)
            y = y0 + 3
            bar_w = max(10, inner - 26)
            for name in ("Linode", "Azure", "GCP"):
                total = s.tf_totals.get(name, 0) or 1
                count = s.tf_counts.get(name, 0)
                self.put(y, x0 + 2, f"{name:<7}", curses.A_BOLD)
                self.bar(y, x0 + 10, bar_w, count / total, "cyan")
                self.put(y, x0 + 11 + bar_w, f"{count:>2}/{total} {p['word']}", self.C["grey"])
                y += 1
        elif st and p["kind"] == "wait" and st.key.endswith(":wait"):
            self.put(y0 + 1, x0 + 2, p["label"], curses.A_BOLD)
            self.bar(y0 + 3, x0 + 2, inner, p["done"] / p["total"], colour)
            left = max(0, p["deadline"] - time.monotonic())
            self.put(y0 + 4, x0 + 2, f"{p['done']}/{p['total']} VMs ready   gives up in {fmt_dur(left)}",
                     self.C["grey"])
        elif st and p["kind"] == "measure" and st.key.endswith(":run"):
            subs = s.substages
            n = len([x for x, _ in subs if x in s.sub_done])
            label = dict(subs).get(s.sub_active, "starting")
            self.put(y0 + 1, x0 + 2, f"{ENGINE_NAME[p['engine']]}  ·  {label.split('  ')[0].strip()}"
                     f"{'  ' + label.split('  ', 1)[1].strip() if '  ' in label else ''}", curses.A_BOLD)
            self.bar(y0 + 3, x0 + 2, inner, n / max(1, len(subs)), colour)
            self.put(y0 + 4, x0 + 2, f"{n}/{len(subs)} stages   {fmt_dur(st.elapsed)} in this run   "
                     f"log also in runs/_logs/experiment-*.log", self.C["grey"])
        elif st:
            self.put(y0 + 1, x0 + 2, f"{st.title}" + (f"  ·  {ENGINE_NAME[st.engine]}" if st.engine else ""),
                     curses.A_BOLD)
            self.put(y0 + 2, x0 + 2, fmt_dur(st.elapsed), self.C["grey"])
        elif s.cleaning:
            self.put(y0 + 1, x0 + 2, "cleaning up", curses.A_BOLD)
        else:
            self.put(y0 + 1, x0 + 2, p.get("label", ""), self.C["grey"])

    def _summary(self, s: State, y0: int, x0: int) -> None:
        r = s.result
        total = fmt_dur(time.monotonic() - s.started)
        if r.get("ok"):
            self.put(y0 + 1, x0 + 2, f"pipeline complete in {total}", self.C["green"] | curses.A_BOLD)
        else:
            self.put(y0 + 1, x0 + 2, f"pipeline stopped after {total}: {r.get('error', '')}",
                     self.C["red"] | curses.A_BOLD)
        y = y0 + 2
        for e in ENGINES:
            if r.get(f"bench:{e}"):
                self.put(y, x0 + 2, f"{ENGINE_NAME[e]:<20} {r[f'bench:{e}']}", self.C["grey"])
                y += 1
        if r.get("insights"):
            self.put(y, x0 + 2, f"insights             {r['insights']}/dashboard.html", self.C["cyan"])
            y += 1
        if r.get("cleanup_cmd"):
            self.put(y, x0 + 2, f"VMs left up; clean up with: {r['cleanup_cmd']}", self.C["yellow"])

    def _modal(self, q: tuple, h: int, w: int) -> None:
        title, lines, _ = q
        bw = min(w - 4, max(len(title), *(len(line) for line in lines)) + 6)
        bh = len(lines) + 4
        y0, x0 = max(0, (h - bh) // 2), max(0, (w - bw) // 2)
        for yy in range(bh):
            self.put(y0 + yy, x0, " " * bw, self.C["track"])
        self.put(y0 + 1, x0 + 2, title[: bw - 4], self.C["yellow"] | curses.A_BOLD)
        for i, line in enumerate(lines):
            self.put(y0 + 3 + i, x0 + 2, line[: bw - 4], curses.A_BOLD if line[:2] in ("d ", "l ") else 0)


# ----------------------------------------------------------------------------
# setup
# ----------------------------------------------------------------------------

def choose_curses(scr, title: str, options: list[tuple[str, str]], default: int,
                  body: list[str] | None = None) -> int:
    """Arrow-key menu; returns the chosen index. q quits."""
    idx = default
    body = body or []
    while True:
        scr.erase()
        h, w = scr.getmaxyx()
        y = max(1, (h - len(options) - len(body)) // 2 - 3)
        scr.addstr(y, 4, "project-hydra  ·  full pipeline setup", curses.A_BOLD)
        for line in body:
            y += 1
            if y < h - len(options) - 6:
                scr.addstr(y, 4, line[: w - 6], curses.A_DIM)
        scr.addstr(y + 2, 4, title[: w - 6])
        for i, (label, detail) in enumerate(options):
            attr = curses.A_REVERSE if i == idx else 0
            scr.addstr(y + 4 + i, 6, f" {label:<22}"[: w - 8], attr | curses.A_BOLD)
            if detail and 30 + len(detail) < w:
                scr.addstr(y + 4 + i, 30, detail, curses.A_DIM)
        scr.addstr(min(h - 1, y + 6 + len(options)), 4, "↑/↓ choose   enter confirm   q quit", curses.A_DIM)
        scr.refresh()
        k = scr.getch()
        if k in (curses.KEY_UP, ord("k")):
            idx = (idx - 1) % len(options)
        elif k in (curses.KEY_DOWN, ord("j")):
            idx = (idx + 1) % len(options)
        elif k in (10, 13, curses.KEY_ENTER):
            return idx
        elif k in (ord("q"), ord("Q"), 27):
            raise SystemExit(0)


def plan_lines(args) -> list[str]:
    out = ["", "Pipeline:"]
    out.append("  preflight: tools, tailscale, TS_API_KEY, clear stale testbed devices")
    for e in args.engine_list:
        out += [f"  {ENGINE_NAME[e]}:",
                f"    terraform plan -out plan.out -var=database_engine={e}  →  terraform apply plan.out",
                "    wait for SSH + cloud-init on 6 VMs",
                f"    ./run-experiment.sh --engine {e} --profile {args.profile}"
                + ("" if args.chaos else " --no-chaos"),
                "    tailscale logout · terraform destroy -auto-approve · delete tailnet devices"]
    out.append(f"  ./generate_insights.sh --profile {args.profile}  +  engine comparison")
    out.append(f"  on failure: {args.on_failure}")
    return out


def setup_curses(scr, args) -> None:
    curses.curs_set(0)
    names = profiles()
    if not args.profile:
        hints = {"smoke": "harness self-test, ~14 min per engine",
                 "thesis": "standard sweep", "thesis-extended": "long run, hours per engine"}
        opts = [(n, hints.get(n, "")) for n in names]
        default = names.index("thesis-extended") if "thesis-extended" in names else 0
        args.profile = names[choose_curses(scr, "Which profile?", opts, default)]
    if args.chaos is None:
        args.chaos = choose_curses(scr, "Run the chaos phases (III and IV)?",
                                   [("yes", "partition + process kill"), ("no", "phases I-II only")], 0) == 0
    if not args.engines:
        opts = [("both", "cockroachdb, then postgresql"), ("cockroachdb", ""), ("postgresql", "")]
        pick = choose_curses(scr, "Which engines?", opts, 0)
        args.engine_list = list(ENGINES) if pick == 0 else [opts[pick][0]]
    if not args.yes:
        idx = choose_curses(scr, "Start?", [("start", "provision and run all of the above"),
                                            ("quit", "")], 0, body=plan_lines(args))
        if idx == 1:
            raise SystemExit(0)


def setup_plain(args) -> None:
    names = profiles()
    if not args.profile:
        if not sys.stdin.isatty():
            raise SystemExit("--profile is required without a terminal")
        for i, n in enumerate(names, 1):
            print(f"  {i}) {n}")
        c = input(f"profile [1-{len(names)}]: ").strip()
        args.profile = names[int(c) - 1] if c.isdigit() and 1 <= int(c) <= len(names) else "thesis-extended"
    if args.chaos is None:
        args.chaos = not (sys.stdin.isatty() and input("run chaos phases? (Y/n): ").strip().lower().startswith("n"))
    for line in plan_lines(args):
        print(line)
    if (not args.yes and sys.stdin.isatty()
            and not input("\nstart? (y/N): ").strip().lower().startswith("y")):
        raise SystemExit(0)


# ----------------------------------------------------------------------------

def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                epilog=__doc__.split("\n\n", 1)[1],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--profile", help="experiment profile (asks if omitted)")
    p.add_argument("--no-chaos", dest="chaos", action="store_const", const=False, default=None,
                   help="phases I-II only")
    p.add_argument("--chaos", dest="chaos", action="store_const", const=True)
    p.add_argument("--engines", help="comma-separated, in order (default: cockroachdb,postgresql)")
    p.add_argument("--yes", action="store_true", help="skip the confirm screen")
    p.add_argument("--on-failure", choices=["ask", "destroy", "leave"], default="ask",
                   help="what to do with running VMs when a step fails (default ask)")
    p.add_argument("--plain", action="store_true", help="scrolling output, no full-screen UI")
    p.add_argument("--ascii", action="store_true", help="plain-ASCII UI glyphs")
    p.add_argument("--cleanup", action="store_true",
                   help="only: tailscale logout, terraform destroy, delete tailnet devices")
    p.add_argument("--engine", choices=ENGINES, help="engine for --cleanup")
    p.add_argument("--tailscale-list", action="store_true",
                   help="list the tailnet devices a purge would delete, and exit")
    p.add_argument("--dry-run", action="store_true",
                   help="run the UI with every command faked; nothing is provisioned")
    p.add_argument("--dry-run-fail", metavar="STEP", help=argparse.SUPPRESS)
    p.add_argument("--dry-run-delay", type=float, default=0.05, help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    if args.engines:
        args.engine_list = [e.strip() for e in args.engines.split(",") if e.strip()]
        bad = [e for e in args.engine_list if e not in ENGINES]
        if bad:
            p.error(f"unknown engine(s): {', '.join(bad)}")
    else:
        args.engine_list = list(ENGINES)
    if args.cleanup and not args.engine:
        p.error("--cleanup needs --engine")
    if args.profile and args.profile not in profiles():
        p.error(f"unknown profile {args.profile!r}; have: {', '.join(profiles())}")
    return args


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except KeyboardInterrupt:
        print()
        return 130


def _main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_env_file()
    LOGS.mkdir(parents=True, exist_ok=True)

    if args.tailscale_list:
        return tailscale.main(["list"])

    if args.cleanup:
        args.profile, args.chaos, args.engine_list = args.profile or "-", False, [args.engine]
        state = State(args)
        runner = Runner(state, PlainUI(state))
        ui = PlainUI(state)
        t = threading.Thread(target=runner.cleanup, args=(args.engine,), daemon=True)
        t.start()
        while t.is_alive():
            ui.pump()
            t.join(0.2)
        ui.pump()
        return 0 if state.result.get("cleaned") else 1

    use_tui = not args.plain and sys.stdout.isatty() and sys.stdin.isatty()
    if use_tui:
        size = os.get_terminal_size()
        if size.columns < 90 or size.lines < 28:
            print(f"terminal is {size.columns}x{size.lines}; the TUI needs 90x28, using --plain")
            use_tui = False

    if not use_tui:
        setup_plain(args)
        state = State(args)
        ui = PlainUI(state)
        runner = Runner(state, ui)
        t = threading.Thread(target=runner.run, daemon=True)
        t.start()
        while t.is_alive():
            try:
                ui.pump()
                t.join(0.2)
            except KeyboardInterrupt:
                if not state.cleaning:
                    state.abort.set()
        ui.pump()
        return 0 if state.result.get("ok") else 1

    locale.setlocale(locale.LC_ALL, "")
    result: dict = {}

    def app(scr):
        setup_curses(scr, args)
        state = State(args)
        ui = TUI(state, scr, args.ascii)
        runner = Runner(state, ui)
        t = threading.Thread(target=runner.run, daemon=True)
        t.start()
        ui.loop(t)
        result.update(state.result, log=state.logfile.name)

    try:
        curses.wrapper(app)
    except KeyboardInterrupt:
        # Only reachable outside the pipeline loop (setup form or the final
        # screen); the loop itself turns Ctrl-C into an orderly abort.
        pass
    if result:
        print(f"pipeline {'complete' if result.get('ok') else 'stopped: ' + str(result.get('error'))}")
        if result.get("insights"):
            print(f"insights  {result['insights']}/dashboard.html")
        if result.get("cleanup_cmd"):
            print(f"VMs left up; clean up with: {result['cleanup_cmd']}")
        print(f"log       {result['log']}")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
