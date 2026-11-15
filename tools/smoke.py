"""Drive release mod-loader callbacks in two OS processes using an isolated Kenshi data fixture."""
import argparse
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import zipfile
from contextlib import ExitStack, contextmanager

root = Path(__file__).resolve().parents[1]
args = argparse.ArgumentParser(description=__doc__)
args.add_argument("--dotnet", default="dotnet")
args.add_argument("--other-build-checkout", type=Path, metavar="DIR",
                  help="Test refusal of a guest from a connector checkout built against the other Meitou release")
suite = args.add_mutually_exclusive_group()
suite.add_argument("--connector-only", action="store_true", help="Run the standalone connector handshake, ping and leave")
suite.add_argument("--maestro", nargs="?", const="", metavar="AUTHOR_PROBE_DLL",
                   help="Export a role with a Maestro-enabled Probe; optionally use a separately built authoring probe")
options = args.parse_args()
probe_dll = Path("tools/Probe/bin/Release/net11.0/Probe.dll")
probe = [options.dotnet, str(root / probe_dll)]
run = Path(tempfile.mkdtemp(prefix="loopback-", dir=root / "artifacts"))
data = run / "data"
data.mkdir()
(run / "resources.cfg").write_text("", encoding="utf-8")
shutil.copytree(root / "artifacts/mods", run / "mods")
cfg = data / "mods.cfg"
cfg.write_text("MeitouP2P.mod\nMeitouP2P-Coop.mod\n", encoding="utf-8")
env = dict(os.environ, KENSHI_PATH=str(run), MEITOU_MODS_CFG=str(cfg))
subprocess.run([*probe, "fixture", data], env=env, check=True)
role = "Guest"
if options.maestro is not None:
    project = run / "author" / "Stoat Roles"
    project.mkdir(parents=True)
    archive = run / "Stoat Roles.zip"
    author_probe = [options.dotnet, str(Path(options.maestro).resolve())] if options.maestro else probe
    subprocess.run([*author_probe, "author", run, project, archive], env=env, check=True)
    with zipfile.ZipFile(archive) as pack:
        assert set(pack.namelist()) == {"Stoat Roles/Stoat Roles.mod", "Stoat Roles/coop-roles.json"}
        # The author probe checked the FCS dependency on this saved file; verify those bytes reached the ZIP.
        assert pack.read("Stoat Roles/Stoat Roles.mod") == (project / "Stoat Roles.mod").read_bytes()
        assert json.loads(pack.read("Stoat Roles/coop-roles.json")) == {
            "schemaVersion": 1, "packId": "stoat",
            "roles": [{"id": "stoat.scout", "label": "Stoat Scout", "characters": ["1-p2p-test.base"]}],
        }
        pack.extractall(run / "mods")
    with cfg.open("a", encoding="utf-8") as f:
        f.write("Stoat Roles.mod\n")
    role = "Stoat Scout"


def connector_profile(name, checkout):
    install = run / name
    (install / "data").mkdir(parents=True)
    shutil.copy(data / "gamedata.base", install / "data")
    config = install / "data/mods.cfg"
    config.write_text("MeitouP2P.mod\n", encoding="utf-8")
    shutil.copytree(checkout / "artifacts/mods/MeitouP2P", install / "mods/MeitouP2P")
    return dict(env, KENSHI_PATH=str(install), MEITOU_MODS_CFG=str(config))


@contextmanager
def peer(mode, name, profile=env, peer_probe=probe):
    with (subprocess.Popen([*peer_probe, "peer", mode], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, env=profile, bufsize=1) as proc,
          (run / (name + ".log")).open("w", encoding="utf-8", buffering=1) as log):
        responses = queue.Queue()
        output_lock = threading.Lock()

        def read():
            for line in proc.stdout:
                with output_lock:
                    log.write(line)
                if line.startswith("PROBE "):
                    responses.put(line[6:].strip())
                else:
                    print(line.rstrip())
            responses.put(None)

        reader = threading.Thread(target=read, daemon=True)
        reader.start()

        def receive():
            value = responses.get(timeout=12)
            if value is None:
                raise RuntimeError(f"{name} exited with status {proc.wait(timeout=4)}. See {log.name}")
            return value

        def call(command):
            with output_lock:
                log.write("> " + command + "\n")
            proc.stdin.write(command + "\n")
            proc.stdin.flush()
            return receive()

        try:
            assert receive() == "ready"
            yield call
        finally:
            if proc.poll() is None:
                proc.stdin.write("exit\n")
                proc.stdin.flush()
                try:
                    proc.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            reader.join(timeout=2)


def wait_for(call, command, fragment):
    until = time.monotonic() + 7
    while True:
        result = call(command)
        if fragment in result:
            return result
        if time.monotonic() >= until:
            raise AssertionError(f"{command}: expected {fragment!r}, received {result!r}")
        time.sleep(.02)


def connect(host, guest, port):
    guest("P2P Join|" + port)
    wait_for(guest, "P2P Status", "Joined host")
    wait_for(host, "P2P Status", "Hosting a connected guest")


def start(host):
    return host("P2P Host|0").rsplit(":", 1)[1]


def observe(guest, fragment):
    guest("Co-op Observe")
    return wait_for(guest, "Co-op Reply", fragment)


with ExitStack() as peers:
    standalone = connector_profile("connector-only", root)
    connector = peers.enter_context(peer("connector", "connector-host", standalone))
    other = peers.enter_context(peer("connector", "connector-guest", standalone))
    connector_port = start(connector)
    if options.other_build_checkout:
        checkout = options.other_build_checkout.resolve(strict=True)
        other_probe = [options.dotnet, str((checkout / probe_dll).resolve(strict=True))]
        with peer("connector", "other-build-guest", connector_profile("other-build", checkout), other_probe) as different:
            different("P2P Join|" + connector_port)
            wait_for(different, "P2P Status", "Incompatible")
            wait_for(connector, "P2P Status", "Incompatible")
            print("PASS mixed game builds refused with matching data and mode lists")
    connect(connector, other, connector_port)
    other("P2P Ping")
    wait_for(other, "P2P Status", "Pong")
    other("P2P Leave")
    wait_for(connector, "P2P Status", "Listening")
    connector("P2P Leave")
    print("PASS connector-only handshake, ping and leave")
    if options.connector_only:
        print("Trial files:", run)
        raise SystemExit(0)

    host = peers.enter_context(peer("both", "coop-host"))
    guest = peers.enter_context(peer("both", "coop-guest"))
    port = start(host)
    connector("P2P Join|" + port)
    wait_for(connector, "P2P Status", "Incompatible")
    connect(host, guest, port)
    observe(guest, "must offer")
    assert "queued" in host("Co-op Offer selected")
    host("ticks|3")
    print(observe(guest, f"Guest [{role}] at 30.00"))
    guest("Co-op Move to camera")
    wait_for(guest, "Co-op Reply", "Move received")
    host("ticks|450")
    x_host, x_guest = json.loads(host("positions"))
    assert x_host == 0 and abs(400 - x_guest) < 10, (x_host, x_guest)
    print(observe(guest, f"Guest [{role}] at {x_guest:.2f}"))
    assert json.loads(guest("positions")) == [0, 30]
    print("PASS host-resolved movement, stationary host character, guest reads the host position")

    # A different assignment invalidates the previous guest's observed token even on the same connection.
    host("Co-op Offer selected||first")
    host("ticks|3")
    guest("Co-op Move to camera")
    wait_for(guest, "Co-op Reply", "stale grant")
    host("ticks|20")
    assert json.loads(host("positions"))[0] == 0

    guest("P2P Leave")
    wait_for(host, "P2P Status", "Listening")
    connect(host, guest, port)
    observe(guest, "must offer")
    host("Co-op Offer selected")
    host("ticks|3")
    observe(guest, f"Guest [{role}]")
    host("reset")
    guest("Co-op Move to camera")
    wait_for(guest, "Co-op Reply", "must offer")
    print("PASS incompatible mode, stale order, reconnect and replacement-world checks")
print("Trial files:", run)
