using System.Net;
using System.Net.Http.Headers;
using System.Security.Cryptography;

namespace Anima.Launcher.Core;

public sealed record DownloadProgress(string Name, long Received, long Total, string Stage);
public sealed record ResumeInfo(string Sha256, long Size, string Url, string? ETag);

public sealed class Downloads : IDisposable
{
    private readonly HttpClient client;
    private readonly Func<string, string?> credential;
    public Downloads(Func<string, string?>? credential = null, HttpMessageHandler? handler = null)
    {
        client = new HttpClient(handler ?? new HttpClientHandler { AllowAutoRedirect = false }) { Timeout = Timeout.InfiniteTimeSpan };
        client.DefaultRequestHeaders.UserAgent.ParseAdd("AnimaRandomStudio/0.3");
        this.credential = credential ?? (_ => null);
    }
    public static async Task<bool> Verify(string file, long size, string sha, CancellationToken ct)
    {
        if (!File.Exists(file) || new FileInfo(file).Length != size) return false;
        await using var stream = File.OpenRead(file);
        return Convert.ToHexString(await SHA256.HashDataAsync(stream, ct)).Equals(sha, StringComparison.OrdinalIgnoreCase);
    }
    public async Task Fetch(string name, string url, string destination, long size, string sha, IProgress<DownloadProgress>? progress, CancellationToken ct)
    {
        var part = destination + ".part";
        for (var attempt = 0; ; attempt++)
        {
            try { await FetchAttempt(name, url, destination, size, sha, progress, ct); return; }
            // A partial violating the server's range contract once gets one clean restart;
            // retrying the same offset would fail identically forever.
            catch (InvalidDataException) when (attempt == 0 && File.Exists(part) && !File.Exists(destination))
            {
                File.Delete(part); File.Delete(part + ".json");
                progress?.Report(new(name, 0, size, "断点与来源不符，重新下载"));
            }
            catch (Exception ex) when (attempt < 2 && !ct.IsCancellationRequested && (ex is HttpRequestException { StatusCode: null } or HttpRequestException { StatusCode: >= HttpStatusCode.InternalServerError } or OperationCanceledException || ex is IOException && ex is not InsufficientSpaceException && File.Exists(part) && !File.Exists(destination)))
            {
                progress?.Report(new(name, File.Exists(part) ? new FileInfo(part).Length : 0, size, "连接中断，自动续传"));
                await Task.Delay(TimeSpan.FromSeconds(attempt + 1), ct);
            }
        }
    }
    private async Task FetchAttempt(string name, string url, string destination, long size, string sha, IProgress<DownloadProgress>? progress, CancellationToken ct)
    {
        Safety.Https(url);
        if (size <= 0 || sha.Length != 64) throw new InvalidDataException("下载需要确定的大小和 SHA-256。");
        if (await Verify(destination, size, sha, ct)) { progress?.Report(new(name, size, size, "已校验，跳过")); return; }
        if (File.Exists(destination)) throw new IOException($"已有文件校验不符，请先手动备份后重试：{destination}");
        Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
        var part = destination + ".part";
        var state = part + ".json";
        ResumeInfo? resume = null;
        try { if (File.Exists(state)) resume = JsonFile.Read<ResumeInfo>(state); } catch { /* Restart an invalid partial download. */ }
        if (resume?.Sha256 != sha || resume.Size != size || !File.Exists(part) || new FileInfo(part).Length > size)
        {
            File.Delete(part); File.Delete(state); resume = null;
        }
        var offset = File.Exists(part) ? new FileInfo(part).Length : 0;
        Safety.FreeSpace(destination, size - offset);
        while (offset != size)
        {
            // Bounded requests avoid multi-GB gateway responses and retain restartable chunks.
            long? end = size > 64L * 1024 * 1024 ? Math.Min(size - 1, offset + 32L * 1024 * 1024 - 1) : null;
            using var response = await Request(url, offset, end, resume?.Url == url ? resume.ETag : null, ct);
            if (response.StatusCode is HttpStatusCode.Unauthorized or HttpStatusCode.Forbidden) throw new HttpRequestException("来源需要访问权限；在设置中保存该官方站点令牌，或手动导入文件。", null, response.StatusCode);
            response.EnsureSuccessStatusCode();
            if (offset > 0 && response.StatusCode == HttpStatusCode.OK) offset = 0;
            if (response.StatusCode == HttpStatusCode.PartialContent && (response.Content.Headers.ContentRange?.From != offset || response.Content.Headers.ContentRange?.Length != size)) throw new InvalidDataException("服务器断点范围与清单不符。");
            var expectedEnd = response.StatusCode == HttpStatusCode.PartialContent ? response.Content.Headers.ContentRange!.To!.Value : size - 1;
            if (expectedEnd < offset || expectedEnd >= size) throw new InvalidDataException("下载范围不合法。");
            if (response.Content.Headers.ContentLength is long length && length != expectedEnd + 1 - offset) throw new InvalidDataException("下载大小与锁定文件不符；可能返回了登录页或来源已更改。");
            JsonFile.Write(state, new ResumeInfo(sha, size, url, response.Headers.ETag?.ToString()));
            await using var input = await response.Content.ReadAsStreamAsync(ct);
            await using (var output = new FileStream(part, offset == 0 ? FileMode.Create : FileMode.Append, FileAccess.Write, FileShare.Read, 131072, true))
            {
                var buffer = new byte[131072];
                long received = offset;
                var lastProgress = Environment.TickCount64;
                while (true)
                {
                    using var idle = CancellationTokenSource.CreateLinkedTokenSource(ct);
                    idle.CancelAfter(TimeSpan.FromSeconds(60));
                    var count = await input.ReadAsync(buffer, idle.Token);
                    if (count == 0) break;
                    received += count;
                    if (received > expectedEnd + 1) throw new InvalidDataException("下载超过声明大小。");
                    await output.WriteAsync(buffer.AsMemory(0, count), ct);
                    if (Environment.TickCount64 - lastProgress > 200) { progress?.Report(new(name, received, size, "下载中")); lastProgress = Environment.TickCount64; }
                }
                await output.FlushAsync(ct);
                if (received < expectedEnd + 1) throw new IOException("连接提前结束，将从已下载位置继续。");
                offset = received;
            }
        }
        progress?.Report(new(name, size, size, "SHA-256 校验中"));
        if (!await Verify(part, size, sha, ct))
        {
            File.Delete(part); File.Delete(state);
            throw new InvalidDataException("下载不完整或 SHA-256 不符，已删除无效临时文件；可切换来源重试。");
        }
        File.Move(part, destination, false);
        File.Delete(state);
        progress?.Report(new(name, size, size, "完成"));
    }
    private async Task<HttpResponseMessage> Request(string url, long offset, long? end, string? etag, CancellationToken ct)
    {
        var original = Safety.Https(url);
        var uri = original;
        for (int redirects = 0; redirects < 10; redirects++)
        {
            using var request = new HttpRequestMessage(HttpMethod.Get, uri);
            if (offset > 0 || end.HasValue)
            {
                request.Headers.Range = new RangeHeaderValue(offset, end);
                if (!string.IsNullOrEmpty(etag)) request.Headers.TryAddWithoutValidation("If-Range", etag);
            }
            // Credentials never cross hosts or reach a user-selected mirror.
            if (uri.Host == original.Host && uri.Host is "civitai.com" or "huggingface.co")
            {
                var token = credential(uri.Host);
                if (!string.IsNullOrWhiteSpace(token)) request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
            }
            using var deadline = CancellationTokenSource.CreateLinkedTokenSource(ct);
            deadline.CancelAfter(TimeSpan.FromSeconds(45));
            var response = await client.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, deadline.Token);
            if ((int)response.StatusCode is not (301 or 302 or 303 or 307 or 308)) return response;
            var location = response.Headers.Location;
            response.Dispose();
            if (location is null) throw new HttpRequestException("下载重定向缺少目标。");
            uri = location.IsAbsoluteUri ? location : new Uri(uri, location);
            // Signed CDN queries are allowed only as server redirects, never persisted or logged.
            if (uri.Scheme != "https" || uri.UserInfo.Length > 0) throw new InvalidDataException("拒绝不安全的下载重定向。");
        }
        throw new HttpRequestException("下载重定向次数过多。");
    }
    public static void CancelPartial(string destination)
    {
        File.Delete(destination + ".part"); File.Delete(destination + ".part.json");
    }
    public void Dispose() => client.Dispose();
}
