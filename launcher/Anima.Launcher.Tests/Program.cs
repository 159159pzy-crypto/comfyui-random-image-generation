using System.IO.Compression;
using System.Net;
using System.Net.Http.Headers;
using System.Security.Cryptography;
using System.Text.Json;
using Anima.Launcher.Core;

var root = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "../../../../.."));
var manifestDir = Path.Combine(root, "launcher", "manifests");
var manifest = JsonFile.Read<RuntimeManifest>(Path.Combine(manifestDir, "launcher-manifest.json"));
var models = JsonFile.Read<ModelManifest>(Path.Combine(manifestDir, "model-manifest.json"));
if (args.Contains("--install-new"))
{
    var targetRoot = Path.GetFullPath(args[Array.IndexOf(args, "--install-new") + 1]);
    var state = targetRoot + ".launcher-state";
    Directory.CreateDirectory(state);
    var configuration = new LauncherConfig { ComfyRoot = targetRoot, ComfyPort = 18888, WebPort = 18990, AppRoot = root, DataDir = Path.Combine(state, "data") };
    using var dl = new Downloads();
    var logLock = new object();
    var installer = new Installer(state, manifest, dl, line => { lock (logLock) File.AppendAllText(Path.Combine(state, "install.log"), line + Environment.NewLine); }, null);
    try { await installer.Install(configuration, true, default); JsonFile.Write(Path.Combine(state, "launcher.json"), configuration); Console.WriteLine("CLEAN INSTALL OK " + state); }
    catch (Exception ex) { Console.WriteLine(ex); Environment.ExitCode = 1; }
    return;
}
var work = Path.Combine(root, "output", "launcher-tests", Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(work);
var count = 0;
void Check(bool condition, string label) { if (!condition) throw new Exception("FAIL: " + label); count++; Console.WriteLine("PASS: " + label); }
async Task Throws(Func<Task> operation, string label) { try { await operation(); } catch { Check(true, label); return; } throw new Exception("Expected failure: " + label); }
Safety.Validate(manifest, models); Check(true, "shipped manifests validate");
await Throws(() => Task.FromResult(Safety.Under(work, "../outside")), "path traversal rejected");
await Throws(() => Task.FromResult(Safety.Under(work, @"C:\outside")), "absolute destination rejected");
await Throws(() => Task.FromResult(Safety.Https("http://example.com/model")), "HTTP rejected");
await Throws(() => Task.FromResult(Safety.Https("https://user:secret@example.com/model")), "URL credentials rejected");
await Throws(() => Task.FromResult(Safety.Https("https://example.com/?token=secret")), "query tokens rejected");
await Throws(() => { Safety.Commit("main"); return Task.CompletedTask; }, "unlocked Git ref rejected");
await Throws(() => { Safety.FreeSpace(work, long.MaxValue - 268435456); return Task.CompletedTask; }, "disk space checked before download");

var data = Enumerable.Range(0, 4096).Select(i => (byte)(i % 251)).ToArray();
var hash = Convert.ToHexString(SHA256.HashData(data));
var target = Path.Combine(work, "model.bin");
var handler = new FakeHandler();
handler.Callback = request => Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new ByteArrayContent(data) });
using var download = new Downloads(_ => "private-token", handler);
await download.Fetch("model", "https://huggingface.co/a", target, data.Length, hash, null, default);
Check(File.ReadAllBytes(target).SequenceEqual(data), "verified download atomically committed");
Check(handler.Seen.Single().Auth == "Bearer private-token", "credential only sent to official host");
await download.Fetch("model", "https://huggingface.co/a", target, data.Length, hash, null, default);
Check(handler.Seen.Count == 1, "existing valid file avoids network");
await Throws(() => download.Fetch("model", "https://huggingface.co/a", target, data.Length, new string('0', 64), null, default), "existing conflicting file preserved");

var resumed = Path.Combine(work, "resumed.bin");
File.WriteAllBytes(resumed + ".part", data[..1000]);
JsonFile.Write(resumed + ".part.json", new ResumeInfo(hash, data.Length, "https://huggingface.co/a", "\"v1\""));
handler.Callback = request =>
{
    Check(request.Headers.Range?.Ranges.Single().From == 1000, "resume sends correct Range");
    var response = new HttpResponseMessage(HttpStatusCode.PartialContent) { Content = new ByteArrayContent(data[1000..]) };
    response.Content.Headers.ContentRange = new ContentRangeHeaderValue(1000, data.Length - 1, data.Length);
    return Task.FromResult(response);
};
await download.Fetch("model", "https://hf-mirror.com/a", resumed, data.Length, hash, null, default);
Check(File.ReadAllBytes(resumed).SequenceEqual(data), "resume across mirror keeps content identity");
Check(handler.Seen.Last().Auth is null, "mirror never receives official credential");

var ignored = Path.Combine(work, "ignored-range.bin");
File.WriteAllBytes(ignored + ".part", data[..1000]);
JsonFile.Write(ignored + ".part.json", new ResumeInfo(hash, data.Length, "https://example.com/a", null));
handler.Callback = _ => Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new ByteArrayContent(data) });
await download.Fetch("model", "https://example.com/a", ignored, data.Length, hash, null, default);
Check(File.ReadAllBytes(ignored).SequenceEqual(data), "server ignoring Range restarts rather than appends");

handler.Callback = _ => Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new ByteArrayContent(new byte[data.Length]) });
var bad = Path.Combine(work, "bad.bin");
await Throws(() => download.Fetch("bad", "https://example.com/a", bad, data.Length, hash, null, default), "bad SHA rejected");
Check(!File.Exists(bad) && !File.Exists(bad + ".part"), "bad content never becomes final model");
handler.Callback = request => Task.FromResult(request.RequestUri!.Host == "huggingface.co" ? new HttpResponseMessage(HttpStatusCode.Redirect) { Headers = { Location = new Uri("https://cdn.example.com/a?signature=abc") } } : new HttpResponseMessage(HttpStatusCode.OK) { Content = new ByteArrayContent(data) });
await download.Fetch("redirect", "https://huggingface.co/a", Path.Combine(work, "redirect.bin"), data.Length, hash, null, default);
Check(handler.Seen.Last().Auth is null, "credential stripped on cross-host redirect");
handler.Callback = _ => Task.FromResult(new HttpResponseMessage(HttpStatusCode.Redirect) { Headers = { Location = new Uri("http://example.com/a") } });
await Throws(() => download.Fetch("redirect", "https://example.com/a", Path.Combine(work, "http.bin"), data.Length, hash, null, default), "HTTPS downgrade redirect rejected");
handler.Callback = _ => Task.FromResult(new HttpResponseMessage(HttpStatusCode.Unauthorized));
await Throws(() => download.Fetch("auth", "https://civitai.com/a", Path.Combine(work, "auth.bin"), data.Length, hash, null, default), "authentication failure actionable");

var legacy = Path.Combine(work, "clip"); Directory.CreateDirectory(legacy); File.WriteAllBytes(Path.Combine(legacy, "encoder.safetensors"), data);
var item = new ModelItem("enc", "enc", "base", "text_encoders", "encoder.safetensors", [], data.Length, hash, ["https://example.com/a"], "test", "");
var probe = new ProbeResult(work, "", new() { ["text_encoders"] = [Path.Combine(work, "text_encoders"), legacy] });
var scan = await Inventory.Scan(probe, new(1, [item]), true, default);
Check(scan.Single().State == "已校验" && scan.Single().FoundPath == Path.Combine(legacy, "encoder.safetensors"), "legacy clip recognized without duplicate download");
probe.Paths["text_encoders"] = [legacy, Path.Combine(work, "text_encoders")];
scan = await Inventory.Scan(probe, new(1, [item]), false, default);
Check(scan.Single().Destination.StartsWith(legacy), "configured default path honored");
Directory.CreateDirectory(Path.Combine(legacy, "sub")); File.Move(Path.Combine(legacy, "encoder.safetensors"), Path.Combine(legacy, "sub", "encoder.safetensors"));
scan = await Inventory.Scan(probe, new(1, [item]), true, default);
Check(!scan.Single().Missing, "subdirectory model found");
var detectorDir = Path.Combine(work, "external-detectors"); Directory.CreateDirectory(detectorDir);
File.WriteAllBytes(Path.Combine(detectorDir, "face.pt"), data);
probe.Paths["ultralytics_bbox"] = [detectorDir];
scan = await Inventory.Scan(probe, new(1, [item with { Category = "ultralytics", RelativePath = "bbox/face.pt" }]), true, default);
Check(scan.Single().State == "已校验" && scan.Single().Destination == Path.Combine(detectorDir, "face.pt"), "Impact extra detector roots recognized without duplicated bbox prefix");
var portable = Path.Combine(work, "portable"); Directory.CreateDirectory(Path.Combine(portable, "ComfyUI")); Directory.CreateDirectory(Path.Combine(portable, "python_embeded"));
File.WriteAllText(Path.Combine(portable, "ComfyUI", "main.py"), ""); File.WriteAllText(Path.Combine(portable, "ComfyUI", "folder_paths.py"), ""); File.WriteAllText(Path.Combine(portable, "python_embeded", "python.exe"), "");
Check(Inventory.NormalizeRoot(portable) == Path.Combine(portable, "ComfyUI") && Inventory.FindPython(Path.Combine(portable, "ComfyUI"), "").Contains("python_embeded"), "portable parent and bundled Python detected");
await Throws(() => { new LauncherConfig { SchemaVersion = 9 }.Validate(); return Task.CompletedTask; }, "future config schema rejected");

var interrupted = Path.Combine(work, "interrupted.bin");
var retryAttempt = 0;
handler.Callback = request =>
{
    if (retryAttempt++ == 0)
    {
        var content = new StreamContent(new MemoryStream(data[..1000]));
        content.Headers.ContentLength = data.Length;
        return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = content });
    }
    Check(request.Headers.Range?.Ranges.Single().From == 1000, "early EOF retries from retained bytes");
    var response = new HttpResponseMessage(HttpStatusCode.PartialContent) { Content = new ByteArrayContent(data[1000..]) };
    response.Content.Headers.ContentRange = new ContentRangeHeaderValue(1000, data.Length - 1, data.Length);
    return Task.FromResult(response);
};
await download.Fetch("retry", "https://example.com/a", interrupted, data.Length, hash, null, default);
Check(File.ReadAllBytes(interrupted).SequenceEqual(data), "retry completes and validates interrupted download");
var paused = Path.Combine(work, "paused.bin");
using (var cancellation = new CancellationTokenSource())
{
    handler.Callback = _ => Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new StreamContent(new PausingStream(data, cancellation)) });
    await Throws(() => download.Fetch("pause", "https://example.com/a", paused, data.Length, hash, null, cancellation.Token), "pause interrupts active transfer");
}
Check(!File.Exists(paused) && new FileInfo(paused + ".part").Length == 1000, "pause retains a resumable partial without publishing final file");
Downloads.CancelPartial(paused);
Check(!File.Exists(paused + ".part") && !File.Exists(paused + ".part.json"), "cancel removes only partial state");
var bigData = new byte[65 * 1024 * 1024]; Array.Fill(bigData, (byte)37);
var bigHash = Convert.ToHexString(SHA256.HashData(bigData));
var ranges = new List<(long, long)>();
handler.Callback = request =>
{
    var range = request.Headers.Range!.Ranges.Single(); var from = range.From!.Value; var to = range.To!.Value; ranges.Add((from, to));
    var response = new HttpResponseMessage(HttpStatusCode.PartialContent) { Content = new ByteArrayContent(bigData, (int)from, (int)(to - from + 1)) };
    response.Content.Headers.ContentRange = new ContentRangeHeaderValue(from, to, bigData.Length);
    return Task.FromResult(response);
};
await download.Fetch("large", "https://example.com/large", Path.Combine(work, "large.bin"), bigData.Length, bigHash, null, default);
Check(ranges.Count == 3 && ranges[1].Item1 == 32 * 1024 * 1024 && ranges[2].Item2 == bigData.Length - 1, "large artifacts use continuous bounded ranges");
var zip = Path.Combine(work, "evil.zip");
using (var archive = ZipFile.Open(zip, ZipArchiveMode.Create)) { using var writer = new StreamWriter(archive.CreateEntry("../escaped.txt").Open()); writer.Write("bad"); }
await Throws(() => { Installer.ExtractZip(zip, Path.Combine(work, "extract")); return Task.CompletedTask; }, "zip traversal rejected");

// Deterministic failures must not loop forever behind a retained .part file.
var stale = Path.Combine(work, "stale-range.bin");
File.WriteAllBytes(stale + ".part", data[..1000]);
JsonFile.Write(stale + ".part.json", new ResumeInfo(hash, data.Length, "https://example.com/s", null));
var staleCalls = 0;
handler.Callback = _ =>
{
    staleCalls++;
    if (staleCalls == 1)
    {
        var wrong = new HttpResponseMessage(HttpStatusCode.PartialContent) { Content = new ByteArrayContent(data) };
        wrong.Content.Headers.ContentRange = new ContentRangeHeaderValue(0, data.Length - 1, data.Length); // From=0 ≠ requested 1000
        return Task.FromResult(wrong);
    }
    // After self-heal the partial is gone; offset 0 issues no Range and gets a full response.
    return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new ByteArrayContent(data) });
};
await download.Fetch("stale", "https://example.com/s", stale, data.Length, hash, null, default);
Check(staleCalls == 2 && File.ReadAllBytes(stale).SequenceEqual(data), "range mismatch self-heals with one fresh retry");

var managed = Path.Combine(work, "managed-root"); Directory.CreateDirectory(managed);
File.WriteAllText(Path.Combine(managed, ".anima-revision.json"), "{}");
Check(Installer.ConstraintsFor(true, manifest, "torch==9.9.9").SequenceEqual(manifest.PythonConstraints), "managed root uses full lock");
Check(Installer.ConstraintsFor(false, manifest, "torch==9.9.9\n").SequenceEqual(new[] { "torch==9.9.9" }), "imported root freezes only torch pins");

var sam2 = "numpy\n git+https://github.com/facebookresearch/sam2 \nsam2 @ git+https://github.com/facebookresearch/sam2@aaaaaaaa\ngit+https://github.com/facebookresearch/sam2.git@v1.0\nfoo==1";
var pinned = Installer.PinSam2(sam2, manifest.Sam2Commit);
Check(pinned is not null && pinned.Split('\n').Count(line => line == "git+https://github.com/facebookresearch/sam2@" + manifest.Sam2Commit) == 3, "sam2 variants all pinned to locked commit");
Check(Installer.PinSam2("numpy==1\nfoo==2", manifest.Sam2Commit) is null, "requirements without sam2 untouched");

var fakeConfig = new LauncherConfig { AppRoot = Path.Combine(work, "app"), DataDir = Path.Combine(work, "data"), ComfyPort = 18188 };
var goodHealth = JsonDocument.Parse("{\"app_dir\":\"" + fakeConfig.AppRoot.Replace("\\", "\\\\") + "\",\"data_dir\":\"" + fakeConfig.DataDir.Replace("\\", "\\\\") + "\",\"comfy_url\":\"http://127.0.0.1:18188\"}").RootElement.Clone();
Check(ServiceManager.IsOurWebUi(goodHealth, fakeConfig), "matching launcher-health reused");
Check(!ServiceManager.IsOurWebUi(null, fakeConfig), "missing health rejected");
Check(!ServiceManager.IsOurWebUi(JsonDocument.Parse("{}").RootElement, fakeConfig), "empty health object rejected without throwing");
Check(!ServiceManager.IsOurWebUi(JsonDocument.Parse("{\"app_dir\":null,\"comfy_url\":null}").RootElement, fakeConfig), "null fields rejected without throwing");
Check(!ServiceManager.IsOurWebUi(JsonDocument.Parse("[]").RootElement, fakeConfig), "non-object health rejected");

var cmd = Commands.Find("cmd.exe");
if (cmd is not null) await Throws(async () => { await Commands.Run(cmd, ["/c", "ping", "-n", "30", "127.0.0.1"], work, null, CancellationToken.None, timeout: TimeSpan.FromMilliseconds(500)); }, "command timeout kills hung process");

if (args.Contains("--live"))
{
    var comfy = args[Array.IndexOf(args, "--live") + 1];
    var python = Inventory.FindPython(comfy, "");
    var actual = await Inventory.Probe(comfy, python, Path.Combine(root, "launcher", "scripts", "probe_comfy.py"), default);
    var found = await Inventory.Scan(actual, models, false, default);
    JsonFile.Write(Path.Combine(work, "live-inventory.json"), found);
    Check(found.All(i => !i.Missing), "live imported ComfyUI model inventory complete");
    Check(found.Single(i => i.Model.Id == "qwen-text").FoundPath!.Contains("clip"), "live legacy encoder recognized");
    var config = new LauncherConfig { ComfyRoot = comfy, Python = python, AppRoot = root, DataDir = Path.Combine(work, "data"), WebPort = 18990, ComfyPort = args.Contains("--owned") ? 18888 : 8188 };
    var manager = new ServiceManager(work, Console.WriteLine);
    try
    {
    await manager.Start(config, manifest, default);
    Check(await ServiceManager.Json(config.WebUrl + "/api/status") is { } live && live.GetProperty("online").GetBoolean(), "real WebUI sees imported ComfyUI");
    if (args.Contains("--generate"))
    {
        var queue = (await ServiceManager.Json(config.ComfyUrl + "/queue"))!.Value;
        Check(queue.GetProperty("queue_running").GetArrayLength() == 0 && queue.GetProperty("queue_pending").GetArrayLength() == 0, "GPU queue idle before acceptance image");
        using var client = new HttpClient(new HttpClientHandler { UseProxy = false });
        using var response = await client.PostAsync(config.WebUrl + "/api/batches", new StringContent("{\"count\":1,\"width\":512,\"height\":512,\"steps\":4,\"extra_prompt\":\"1girl, portrait, gentle smile, simple background, safe\",\"hires\":{\"enabled\":false}}", System.Text.Encoding.UTF8, "application/json"));
        var text = await response.Content.ReadAsStringAsync();
        Check(response.IsSuccessStatusCode, "acceptance image submitted: " + text);
        var complete = false;
        for (var attempt = 0; attempt < 180; attempt++)
        {
            await Task.Delay(2000);
            var current = (await ServiceManager.Json(config.WebUrl + "/api/batches/current"))!.Value;
            var batch = current.GetProperty("batch");
            var state = batch.GetProperty("status").GetString();
            if (state == "completed") { JsonFile.Write(Path.Combine(work, "generation-result.json"), current); complete = true; break; }
            if (state is "failed" or "error") throw new Exception(current.ToString());
        }
        Check(complete, "real GPU generation completed");
    }
    }
    finally { var recovered = new ServiceManager(work, Console.WriteLine); recovered.RecoverOwned(); recovered.StopOwned(); }
    Check(!await ServiceManager.PortOpen(config.WebPort), "recovered owned WebUI stopped");
    Check(await ServiceManager.PortOpen(config.ComfyPort) == !args.Contains("--owned"), "ComfyUI ownership respected on shutdown");
}
Console.WriteLine($"{count} checks passed; artifacts: {work}");

sealed class FakeHandler : HttpMessageHandler
{
    public Func<HttpRequestMessage, Task<HttpResponseMessage>> Callback = null!;
    public List<(string Url, string? Auth)> Seen = [];
    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
    {
        Seen.Add((request.RequestUri!.ToString(), request.Headers.Authorization?.ToString()));
        return Callback(request);
    }
}

sealed class PausingStream(byte[] bytes, CancellationTokenSource source) : MemoryStream(bytes)
{
    public override ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken cancellationToken = default)
    {
        if (Position >= 1000) source.Cancel();
        cancellationToken.ThrowIfCancellationRequested();
        return base.ReadAsync(buffer[..Math.Min(buffer.Length, 1000)], cancellationToken);
    }
}
