using System.Numerics;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using Meitou.Content;
using Meitou.Data;
using Meitou.Data.Fcs;
using Meitou.Modding;
using Meitou.Simulation;
using Meitou.Simulation.Movement;

// Prepare or drive the released loader and its registered F10 callbacks without a GPU.
const string template = "1-p2p-test.base";
var build = typeof(IMod).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion;
if (build is null || !CodeModVersion.TryParse(build, out var version))
    throw new InvalidDataException($"Cannot determine the Meitou loader version from '{build}'.");
if (args is ["target-manifest", var path, var expectedBuild])
{
    if (build != expectedBuild) throw new InvalidOperationException($"Probe loads {build}, expected {expectedBuild}.");
    var manifest = JsonNode.Parse(File.ReadAllText(path))!.AsObject();
    if (manifest["gameVersion"]?.GetValue<string>() is { } range && !CodeModVersionRange.Parse(range).Contains(version))
        throw new InvalidDataException($"{path} declares '{range}', which excludes Meitou {version.Release}.");
    manifest["gameVersion"] = $"={version.Release}";
    Console.WriteLine(manifest.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
    return;
}
if (args is ["stoat-start", var output])
{
    var file = new FcsFile { Author = "MeitouP2P-Coop", NextId = 4 };
    file.Dependencies.Add("MeitouP2P-Coop.mod");
    FcsRecord Add(int id, FcsRecordType type, string name)
    {
        var rec = new FcsRecord { RecordType = type, StringId = $"{id}-Stoat Walk.mod", Name = name, ByteSize = 1 };
        file.Records.Add(rec);
        return rec;
    }
    var scout = Add(1, FcsRecordType.Character, "Stoat Scout");
    var keeper = Add(2, FcsRecordType.Character, "Stoat Guide");
    foreach (var actor in new[] { scout, keeper })
    {
        actor.Ints["female chance"] = actor == scout ? 100 : 0;
        actor.References["race"] = [new("18019-gamedata.base", 100, 0, 0)];
        actor.References["clothing"] = [new("51663-Newwworld.mod", 1, 100, 0), new("51662-Newwworld.mod", 1, 100, 0)];
    }
    var start = Add(3, FcsRecordType.NewGameStartoff, "Stoat Scout Walk");
    start.Bools["force start pos"] = true;
    start.Ints["start pos X"] = 34595;
    start.Ints["start pos Z"] = -70128;
    start.References["squad"] = [new(scout.StringId,1,0,0), new(keeper.StringId,1,0,0)];
    Directory.CreateDirectory(output);
    FcsWriter.WriteFile(file, Path.Combine(output, "Stoat Walk.mod"));
    File.WriteAllText(Path.Combine(output,"coop-roles.json"),
        """{"schemaVersion":1,"packId":"stoat","roles":[{"id":"stoat.scout","label":"Stoat Scout","characters":["1-Stoat Walk.mod"]}]}""");
    return;
}
if (args is ["fixture", var directory])
{
    var file = new FcsFile { Author = "MeitouP2P test", NextId = 2 };
    file.Records.Add(new FcsRecord { RecordType = FcsRecordType.Character, StringId = template, Name = "Fixture wanderer", ByteSize = 1 });
    FcsWriter.WriteFile(file, Path.Combine(directory, "gamedata.base"));
    return;
}
#if MAESTRO
if (args is ["author", var installPath, var project, var zip])
{
    var lib = new Maestro.Core.Library(installPath, project);
    var tool = lib.Actions.Single(t => t.Action.Id == "guest-role");
    var edits = new Maestro.Core.Editing(lib);
    edits.Review(tool, [lib.Records.Single(r => r.Id == template)], new() {
        ["pack"] = "stoat", ["role"] = "stoat.scout", ["label"] = "Stoat Scout" });
    edits.Save();
    Maestro.Core.ProjectFiles.Export(project, zip, lib.Actions);
    if (!FcsReader.ReadFile(lib.FcsPath).Dependencies.Contains("MeitouP2P-Coop.mod")) throw new Exception("Missing dependency.");
    Console.WriteLine("Maestro reviewed, saved and exported the guest role.");
    return;
}
#endif
if (args is not ["peer", var mode]) throw new ArgumentException("Expected peer, fixture, stoat-start, author or target-manifest.");
var install = GameInstall.Open(Environment.GetEnvironmentVariable("KENSHI_PATH")!);
var data = GameDatabase.Load(LoadOrder.FromInstall(install));
var mods = ModHost.EnabledCodeMods(install);
Console.WriteLine($"Loading mods with Meitou {version}.");
World? world = null;
var loaded = ModHost.Load(new ModHostOptions(mods, data, null!, () => world, GameVersion: version));
if (loaded.Mods.Count != mods.Count || mods.Count != (mode == "connector" ? 1 : 2))
    throw new Exception("Loader result: " + string.Join(" | ", loaded.Notices));
var first = default(CharacterId);
var second = default(CharacterId);

void ResetWorld()
{
    world?.Dispose();
    var ground = new OpenGroundWalkability((x,z) => 300);
    var systems = new List<ITickSystem> { new MovementSystem(new PathService(ground, true)) };
    if (loaded.Simulation != null)
        foreach (var factory in loaded.Simulation.Systems)
            systems.Add(factory.Create(new SystemBuildContext(null!, systems))!);
    world = new World(new WorldSettings { Threads = 1, PublishSnapshots = false }, ground, systems);
    first = Spawn("Host", 0);
    second = Spawn("Guest", 30);
}

CharacterId Spawn(string name, float x) => world!.Characters.Spawn(
    new CharacterHot { Position = new(x,300,0), Health = 100, MaxSpeed = 80, WalkSpeed = 15, Mode = SpeedMode.Free },
    new CharacterCold { IsPlayer = true, Name = name, RecordId = template }, 0);

ResetWorld();
Console.WriteLine("PROBE ready");
try
{
    while (Console.ReadLine() is { } line)
    {
        var parts = line.Split('|');
        string result;
        switch (parts[0])
        {
            case "exit": return;
            case "ticks": world!.RunTicks(int.Parse(parts[1])); result = "OK"; break;
            case "reset": ResetWorld(); result = "OK"; break;
            case "positions":
                result = JsonSerializer.Serialize(new[] { world!.Characters.Previous[first.Slot].Position.X, world.Characters.Previous[second.Slot].Position.X });
                break;
            default:
                var row = loaded.DebugCommands.Single(c => c.Label == parts[0]);
                var selected = parts.ElementAtOrDefault(2) == "first" ? first : second;
                result = row.Run(new DebugCommandContext(parts.ElementAtOrDefault(1) ?? "", [selected],
                    new Vector3(400,300,0), world!.Tick, world.Commands.Enqueue));
                break;
        }
        Console.WriteLine("PROBE " + result);
    }
}
finally { world!.Dispose(); }
