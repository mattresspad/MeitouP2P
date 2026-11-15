# MeitouP2P

A C# session connector for **one host and one guest on the same Windows PC**. Both players use the same stock MeitouClient build, either **0.20.0 or 0.20.1**. The connector starts offline. A mode can register a request handler, open a loopback TCP session, and exchange messages through Meitou's API 2 dependency system. The first mode is MeitouP2P-Coop. [Co-op source and Stoat walk replays](https://github.com/mattresspad/MeitouP2P-Coop)

## Build just the connector

Install Git, PowerShell, Python 3 and the .NET 11 SDK. Extract either supported Windows x64 release and set `MEITOU_DIR` to the directory containing `MeitouClient.dll`. Use that directory for the build and for launching the windows. The visible game also requires your own Kenshi installation. [MeitouClient 0.20.1](https://github.com/VlcekM/MeitouClient/releases/tag/v0.20.1) · [MeitouClient 0.20.0](https://github.com/VlcekM/MeitouClient/releases/tag/v0.20.0)

```powershell
git clone https://github.com/mattresspad/MeitouP2P.git
cd MeitouP2P
.\build.ps1 -MeitouDir $env:MEITOU_DIR
python tools/smoke.py --connector-only
```

The build stages `artifacts/mods/MeitouP2P` and stamps the game release into its manifest. Build again when switching game releases. Copy the staged directory into the Kenshi `mods` folder used by each test client and enable it in each launcher. In disposable games, open F10. Choose **P2P Host** in one window and **P2P Join** in the other. Both use port 42870 by default. **P2P Ping** followed by **P2P Status** shows the reply. **P2P Leave** closes the session. Rejoining establishes a new peer identity.

To check the build boundary, set `MEITOU_OTHER_DIR` to the other extracted release. Build the same connector revision in a second checkout. Both peers then load their own release's DLLs with the same fixture and mode list. The test checks that the host refuses the mixed pair and then connects a matching guest and pings.

```powershell
git worktree add --detach ../MeitouP2P-other HEAD
../MeitouP2P-other/build.ps1 -MeitouDir $env:MEITOU_OTHER_DIR
python tools/smoke.py --connector-only --other-build-checkout ../MeitouP2P-other
```

## Build and test with Co-op

From the connector checkout, clone Co-op as a sibling. The combined test uses two processes, the released loader, a synthetic flat world, and the game's movement system. It checks refusal of incompatible modes and stale grants, disconnect/rejoin and replacement of the host world.

```powershell
git clone https://github.com/mattresspad/MeitouP2P-Coop.git ../MeitouP2P-Coop
.\build.ps1 -MeitouDir $env:MEITOU_DIR -CoopDir ../MeitouP2P-Coop
python tools/smoke.py
```

The stage now contains both installable mod directories. If using a specific .NET 11 executable, pass `-Dotnet <path>` to the PowerShell scripts and `--dotnet <path>` to the smoke test. See the Co-op provider notes for the optional tagged Maestro export check. [Maestro provider and test instructions](https://github.com/mattresspad/MeitouP2P-Coop/blob/main/docs/maestro.md)

## Walk Stoat Scout in two windows

After the combined build, set `KENSHI_PATH` to your installed Kenshi folder. Start two stock clients on the same Windows desktop with this command.

```powershell
.\tools\stoat-windows.ps1 -Meitou $env:MEITOU_DIR -Kenshi $env:KENSHI_PATH
```

The launcher creates disposable working directories under `artifacts`, disables autosave and shares game data through a directory junction. Pick **Host session** on the left and **Join host** on the right. Once the guest shows a host tick, select the Scout by her body and right-click walkable ground. The host can move the Guide at the same time. In the guest window an empty-ground click clears selection.

To replay the automated check, start a fresh pair and run the following once both games and their control channels are ready. It places the windows side by side, uses screen-coordinate clicks and A/D camera input, and retains captures and transcripts in the trial folder.

```powershell
python tools/stoat-native-walk.py (Get-Content artifacts/stoat-current.txt)
```

## Register a mode

Declare `meitou.p2p` in the mode's `meitou.json` dependencies and reference the connector assembly with copying disabled. During initialization, obtain `context.Dependency<ISessionApi>("meitou.p2p")` and call `Register(context.Id, revision, handler)`. On the guest, `RequestAsync(context.Id, message)` returns the host's reply. The host's handler receives a connection-scoped peer ID and runs on a network worker. Read an immutable published value or queue work for a serial simulation tick. [Session interface](src/Session.cs)

The handshake compares the protocol, game build, ordered data filenames and registered mode IDs and revisions. It supports one outstanding request per guest. File-content verification, LAN and internet play are future work.

When reporting a problem, include the exact build, mods in load order, what each player did and the two status lines. A standalone ping tests the connector. Co-op's grant and host-tick logs trace gameplay orders. [Stoat test results](https://github.com/mattresspad/MeitouP2P-Coop/blob/main/docs/stoat-results.md) · [Licensing](LICENSING.md)
