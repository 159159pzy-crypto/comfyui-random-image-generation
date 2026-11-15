/* One queue for archive thumbnails. Detached requests retain their slot through
 * completion: cancelling a browser request does not cancel shared server work. */
const ArtworkThumbnails = (() => {
  const limit = 12, margin = 400, timeout = 120000, delays = [300, 800, 1500], entries = new Map();
  let active = 0, enabled = true, frame = 0, wakeTimer = 0, initialized = false;
  let visibleObserver = null, nearbyObserver = null;

  function online() { return typeof navigator === "undefined" || navigator.onLine !== false; }
  function schedule() {
    if (!frame) frame = requestAnimationFrame(pump);
  }
  function initialize() {
    if (initialized) return;
    initialized = true;
    if (typeof IntersectionObserver !== "undefined") {
      const observe = field => changes => {
        for (const change of changes) {
          const entry = entries.get(change.target);
          if (entry) entry[field] = change.isIntersecting;
        }
        schedule();
      };
      visibleObserver = new IntersectionObserver(observe("visible"), { rootMargin: "0px" });
      nearbyObserver = new IntersectionObserver(observe("nearby"), { rootMargin: `${margin}px 0px` });
    } else {
      window.addEventListener("scroll", schedule, { passive: true, capture: true });
    }
    window.addEventListener("resize", schedule, { passive: true });
    window.addEventListener("online", schedule);
    window.addEventListener("offline", schedule);
    document.addEventListener("visibilitychange", schedule);
  }
  function paint(entry) {
    const loading = ["queued", "loading", "retry-wait"].includes(entry.state);
    const retrying = entry.state === "retry-wait" || (entry.state === "loading" && entry.retries > 0);
    entry.article.dataset.thumbnailState = entry.state;
    entry.article.dataset.thumbnailRetry = String(entry.retries);
    entry.article.classList.toggle("is-loading", loading);
    entry.article.setAttribute("aria-busy", String(loading));
    entry.image.classList.toggle("image-loaded", entry.state === "loaded");
    entry.image.classList.toggle("image-failed", entry.state === "error");
    entry.failure.classList.toggle("is-retrying", retrying);
    entry.failure.hidden = entry.state !== "error" && !retrying;
    if (retrying) {
      const attempt = entry.state === "retry-wait" ? entry.retries + 1 : entry.retries;
      entry.failure.textContent = !online() ? `等待网络恢复 · 重试 ${attempt}/3`
        : entry.state === "retry-wait" ? `即将重试 ${attempt}/3…` : `正在重试 ${attempt}/3…`;
    } else if (entry.state === "error") {
      entry.failure.textContent = entry.error || "图片加载失败";
    }
    entry.retry.hidden = entry.state !== "error";
    entry.retry.disabled = loading;
  }
  function revoke(entry) {
    if (entry.objectUrl) URL.revokeObjectURL(entry.objectUrl);
    entry.objectUrl = "";
  }
  function position(entry) {
    const article = entry.article;
    if (!article.isConnected || article.closest("[hidden], [inert]") || !article.getClientRects().length) return null;
    const rect = article.getBoundingClientRect(), height = window.innerHeight, width = window.innerWidth;
    const horizontal = rect.right > 0 && rect.left < width;
    const visible = horizontal && rect.bottom > 0 && rect.top < height && (!visibleObserver || entry.visible);
    article.classList.toggle("is-offscreen", !visible);
    if (!horizontal || rect.bottom <= -margin || rect.top >= height + margin || (nearbyObserver && !entry.nearby)) return null;
    return { entry, visible, distance: Math.max(0, -rect.bottom, rect.top - height) };
  }
  function pump() {
    frame = 0;
    clearTimeout(wakeTimer);
    wakeTimer = 0;
    for (const entry of entries.values()) if (entry.state === "retry-wait") paint(entry);
    if (!enabled || document.hidden || !online()) return;
    const queue = [], now = Date.now();
    let nextWake = Infinity;
    for (const entry of entries.values()) {
      const location = position(entry);
      if (!location) continue;
      if (entry.state === "queued") queue.push(location);
      else if (entry.state === "retry-wait" && location.visible) {
        if (entry.retryAt <= now) queue.push(location);
        else nextWake = Math.min(nextWake, entry.retryAt);
      }
    }
    queue.sort((a, b) => Number(b.visible) - Number(a.visible) || a.distance - b.distance
      || (a.entry.article.compareDocumentPosition(b.entry.article) & 4 ? -1 : 1));
    for (const item of queue) {
      if (active >= limit) break;
      start(item.entry, item.visible);
    }
    if (Number.isFinite(nextWake)) {
      wakeTimer = setTimeout(schedule, Math.min(2147483647, Math.max(0, nextWake - Date.now())));
    }
  }
  function failure(message, retryable = true, retryAfter = 0) { return { message, retryable, retryAfter }; }
  function httpFailure(response) {
    const status = response.status;
    const retryable = status < 400 || status === 408 || status === 429 || status >= 500;
    const value = response.headers?.get?.("Retry-After")?.trim();
    let wait = 0;
    if (value) {
      wait = /^\d+(?:\.\d+)?$/.test(value) ? Number(value) * 1000 : Date.parse(value) - Date.now();
      if (!Number.isFinite(wait) || wait < 0) wait = 0;
    }
    const message = status === 404 || status === 410 ? "图片记录不存在"
      : status === 401 || status === 403 ? "无法访问图片"
      : status === 429 ? "图片请求繁忙" : status >= 500 ? "图片服务暂时不可用" : "图片请求失败";
    return failure(`${message}（${status}）`, retryable, wait);
  }
  function start(entry, visible) {
    const image = entry.image;
    const task = { settled: false, decoding: false, objectUrl: "", timer: 0, controller: new AbortController() };
    if (entry.state === "retry-wait") entry.retries++;
    const reload = entry.reload;
    active++;
    entry.task = task;
    entry.state = "loading";
    entry.retryAt = 0;
    revoke(entry);
    image.removeAttribute("src");
    image.loading = "eager";
    image.decoding = "async";
    image.fetchPriority = visible ? "high" : "low";
    paint(entry);
    const current = () => !task.settled && entry.task === task;
    const dimensions = () => image.naturalWidth > 0 && image.naturalHeight > 0;
    const finish = error => {
      if (!current()) return;
      task.settled = true;
      clearTimeout(task.timer);
      image.removeEventListener("load", loaded);
      image.removeEventListener("error", failed);
      active--;
      entry.task = null;
      if (error) task.controller.abort();
      if (entry.disposed || error) {
        if (task.objectUrl) URL.revokeObjectURL(task.objectUrl);
        image.removeAttribute("src");
      } else {
        entry.objectUrl = task.objectUrl;
      }
      if (!entry.disposed) {
        entry.error = error?.message || "";
        if (error?.retryable && entry.retries < delays.length) {
          entry.state = "retry-wait";
          entry.reload = true;
          entry.retryAt = Date.now() + Math.max(error.retryAfter, delays[entry.retries] * (1 + Math.random() * .2));
        } else {
          entry.state = error ? "error" : "loaded";
        }
        paint(entry);
      }
      schedule();
    };
    const loaded = () => {
      if (!current() || task.decoding) return;
      if (!dimensions()) { finish(failure("图片解码失败")); return; }
      task.decoding = true;
      Promise.resolve().then(() => current() ? image.decode?.() : undefined).then(() => {
        if (current()) finish(dimensions() ? null : failure("图片解码失败"));
      }, () => finish(failure("图片解码失败")));
    };
    const failed = () => finish(failure("图片解码失败"));
    const assign = source => {
      image.addEventListener("load", loaded);
      image.addEventListener("error", failed);
      image.src = source;
      // A just-assigned image may still report its previous empty state. Only
      // shortcut cached SUCCESS here; actual errors arrive through the event.
      Promise.resolve().then(() => {
        if (current() && image.complete && dimensions()) loaded();
      });
    };
    task.timer = setTimeout(() => finish(failure("图片加载超时")), timeout);
    void (async () => {
      try {
        const response = await fetch(entry.url, {
          cache: reload ? "reload" : "default", signal: task.controller.signal,
          priority: visible ? "high" : "low",
        });
        if (!current()) return;
        if (!response.ok) { finish(httpFailure(response)); return; }
        const body = await response.blob();
        if (!current()) return;
        if (entry.disposed) { finish(null); return; }
        if (body.size === 0) { finish(failure("图片内容为空")); return; }
        task.objectUrl = URL.createObjectURL(body);
        assign(task.objectUrl);
      } catch {
        finish(failure("图片网络连接失败"));
      }
    })();
  }
  function attach(article, image, retry) {
    initialize();
    if (entries.has(article)) return;
    const entry = { article, image, retry, failure: article.querySelector(".image-failure"),
      url: image.dataset.thumbnailUrl, state: "queued", disposed: false, reload: false,
      objectUrl: "", task: null, retries: 0, retryAt: 0, error: "" };
    entries.set(article, entry);
    entry.failure.setAttribute("role", "status");
    entry.failure.setAttribute("aria-live", "polite");
    retry.addEventListener("click", event => {
      event.stopPropagation();
      if (entry.disposed || entry.state !== "error") return;
      const restoreFocus = document.activeElement === retry;
      entry.reload = true;
      entry.retries = 0;
      entry.retryAt = 0;
      entry.error = "";
      entry.state = "queued";
      paint(entry);
      if (restoreFocus) article.querySelector(".image-card-open")?.focus({ preventScroll: true });
      schedule();
    });
    paint(entry);
    visibleObserver?.observe(article);
    nearbyObserver?.observe(article);
    schedule();
  }
  function remove(article) {
    const entry = entries.get(article);
    if (!entry) return;
    entries.delete(article);
    entry.disposed = true;
    entry.retryAt = 0;
    visibleObserver?.unobserve(article);
    nearbyObserver?.unobserve(article);
    if (!entry.task) {
      revoke(entry);
      entry.image.removeAttribute("src");
    }
    schedule();
  }
  return { attach, remove, refresh: schedule, setActive(value) { enabled = Boolean(value); schedule(); } };
})();
