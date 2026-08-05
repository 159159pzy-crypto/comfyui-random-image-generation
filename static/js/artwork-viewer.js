import { api } from "./api.js";

const clamp = (value, minimum, maximum) => Math.max(minimum, Math.min(maximum, value));

function recordPrompt(record) {
  return record.positive_prompt || record.resolved_prompt || record.intent_json?.positive_prompt || record.intent?.positive_prompt || "";
}

export function recordSeed(record) {
  return record.sample_seed_text
    ?? record.sample_seed
    ?? record.intent_json?.sampling?.seed
    ?? record.intent?.sampling?.seed
    ?? record.intent_json?.seed
    ?? record.intent?.seed
    ?? "";
}

export function recordPromptSeed(record) {
  return record.prompt_seed_text
    ?? record.prompt_seed
    ?? record.intent_json?.sampling?.prompt_seed
    ?? record.intent?.sampling?.prompt_seed
    ?? "";
}

export class ArtworkViewer {
  constructor({ onRestore, onReproduce, onVariant, onWorkspace, onDeleted, notify } = {}) {
    this.callbacks = { onRestore, onReproduce, onVariant, onWorkspace, onDeleted, notify };
    this.records = [];
    this.index = -1;
    this.scale = 1;
    this.x = 0;
    this.y = 0;
    this.dragging = null;
    this.#build();
  }

  #build() {
    this.dialog = document.createElement("dialog");
    this.dialog.className = "artwork-viewer";
    this.dialog.innerHTML = `
      <section class="artwork-stage">
        <div class="artwork-toolbar">
          <button type="button" data-action="previous" title="上一张" aria-label="上一张">←</button>
          <button type="button" data-action="next" title="下一张" aria-label="下一张">→</button>
          <span data-role="position">1 / 1</span>
          <button type="button" data-action="zoom-out" title="缩小" aria-label="缩小">−</button>
          <button type="button" data-action="fit" title="适应窗口" aria-label="适应窗口">⊡</button>
          <button type="button" data-action="zoom-in" title="放大" aria-label="放大">＋</button>
          <button type="button" data-action="close" title="关闭" aria-label="关闭">×</button>
        </div>
        <figure><img data-role="image" alt="生成结果" draggable="false"></figure>
      </section>
      <aside class="artwork-inspector">
        <div><span class="kicker">SHARED ARCHIVE</span><h2 data-role="title">作品详情</h2><p data-role="meta"></p></div>
        <label>完整提示词<textarea data-role="prompt" rows="8" readonly></textarea></label>
        <label>Seed<input data-role="seed" readonly></label>
        <div class="artwork-copy-actions">
          <button class="button ghost" type="button" data-action="copy-prompt">复制 Prompt</button>
          <button class="button ghost" type="button" data-action="copy-seed">复制 Seed</button>
          <a class="button ghost" data-role="download" download>下载</a>
        </div>
        <div class="artwork-generation-actions">
          <button class="button primary" type="button" data-action="restore">载入设置</button>
          <button class="button" type="button" data-action="reproduce">复现</button>
          <button class="button" type="button" data-action="variant">变体</button>
          <button class="button ghost" type="button" data-action="workspace">打开来源工作台</button>
        </div>
        <button class="button danger" type="button" data-action="delete">删除记录</button>
      </aside>`;
    document.body.append(this.dialog);
    this.image = this.dialog.querySelector('[data-role="image"]');
    this.stage = this.dialog.querySelector("figure");
    this.dialog.querySelectorAll("[data-action]").forEach((button) =>
      button.addEventListener("click", () => this.#action(button.dataset.action)),
    );
    this.dialog.addEventListener("click", (event) => { if (event.target === this.dialog) this.dialog.close(); });
    this.dialog.addEventListener("keydown", (event) => {
      if (event.key === "ArrowLeft") this.previous();
      if (event.key === "ArrowRight") this.next();
      if (event.key === "+" || event.key === "=") this.zoom(0.2);
      if (event.key === "-") this.zoom(-0.2);
    });
    this.stage.addEventListener("wheel", (event) => { event.preventDefault(); this.zoom(event.deltaY < 0 ? 0.15 : -0.15); }, { passive: false });
    this.stage.addEventListener("pointerdown", (event) => {
      this.dragging = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, originX: this.x, originY: this.y };
      this.stage.setPointerCapture(event.pointerId);
      this.stage.classList.add("dragging");
    });
    this.stage.addEventListener("pointermove", (event) => {
      if (!this.dragging || event.pointerId !== this.dragging.pointerId || this.scale <= 1) return;
      this.x = this.dragging.originX + event.clientX - this.dragging.x;
      this.y = this.dragging.originY + event.clientY - this.dragging.y;
      this.#transform();
    });
    for (const type of ["pointerup", "pointercancel"])
      this.stage.addEventListener(type, () => { this.dragging = null; this.stage.classList.remove("dragging"); });
    let touchStart = null;
    this.stage.addEventListener("touchstart", (event) => {
      if (event.touches.length === 1) touchStart = { x: event.touches[0].clientX, y: event.touches[0].clientY };
    }, { passive: true });
    this.stage.addEventListener("touchend", (event) => {
      const start = touchStart;
      touchStart = null;
      if (!start || this.scale > 1 || !event.changedTouches.length) return;
      const dx = event.changedTouches[0].clientX - start.x;
      const dy = event.changedTouches[0].clientY - start.y;
      if (Math.abs(dx) > 56 && Math.abs(dx) > Math.abs(dy)) dx > 0 ? this.previous() : this.next();
    }, { passive: true });
  }

  open(record, records = []) {
    this.records = records.length ? records : [record];
    this.index = Math.max(0, this.records.findIndex((item) => String(item.id) === String(record.id)));
    this.#render();
    if (!this.dialog.open) this.dialog.showModal();
  }

  current() { return this.records[this.index] || null; }
  previous() { if (this.records.length) { this.index = (this.index - 1 + this.records.length) % this.records.length; this.#render(); } }
  next() { if (this.records.length) { this.index = (this.index + 1) % this.records.length; this.#render(); } }
  zoom(delta) { this.scale = clamp(this.scale + delta, 0.5, 5); if (this.scale <= 1) { this.x = 0; this.y = 0; } this.#transform(); }
  fit() { this.scale = 1; this.x = 0; this.y = 0; this.#transform(); }

  #transform() {
    this.stage.classList.toggle("zoomed", this.scale > 1);
    this.image.style.transform = `translate3d(${this.x}px, ${this.y}px, 0) scale(${this.scale})`;
  }

  #render() {
    const record = this.current();
    if (!record) return;
    this.fit();
    const imageUrl = `/api/v7/images/${encodeURIComponent(record.id)}`;
    this.image.src = imageUrl;
    this.dialog.querySelector('[data-role="title"]').textContent = record.filename || `作品 #${record.id}`;
    this.dialog.querySelector('[data-role="meta"]').textContent = `${record.source_workspace === "natural" ? "自然语言" : "随机工作台"} · ${record.created_at || ""}`;
    this.dialog.querySelector('[data-role="prompt"]').value = recordPrompt(record);
    this.dialog.querySelector('[data-role="seed"]').value = String(recordSeed(record));
    this.dialog.querySelector('[data-role="position"]').textContent = `${this.index + 1} / ${this.records.length}`;
    this.dialog.querySelector('[data-role="download"]').href = imageUrl;
    this.dialog.querySelector('[data-role="download"]').download = record.filename || `anima-${record.id}.png`;
  }

  async #copy(value, label) {
    await navigator.clipboard.writeText(String(value || ""));
    this.callbacks.notify?.(`${label} 已复制`);
  }

  async #delete() {
    const record = this.current();
    if (!record || !confirm("删除这条作品记录？原始图片文件是否保留由服务端策略决定。")) return;
    await api(`/api/v7/history/${encodeURIComponent(record.id)}`, { method: "DELETE" });
    this.records.splice(this.index, 1);
    this.callbacks.onDeleted?.(record);
    window.dispatchEvent(new CustomEvent("studio:history-changed"));
    if (!this.records.length) this.dialog.close();
    else { this.index = Math.min(this.index, this.records.length - 1); this.#render(); }
  }

  async #action(action) {
    const record = this.current();
    try {
      if (action === "close") this.dialog.close();
      else if (action === "previous") this.previous();
      else if (action === "next") this.next();
      else if (action === "zoom-in") this.zoom(0.2);
      else if (action === "zoom-out") this.zoom(-0.2);
      else if (action === "fit") this.fit();
      else if (action === "copy-prompt") await this.#copy(recordPrompt(record), "Prompt");
      else if (action === "copy-seed") await this.#copy(recordSeed(record), "Seed");
      else if (action === "delete") await this.#delete();
      else if (action === "restore") await this.callbacks.onRestore?.(record);
      else if (action === "reproduce") await this.callbacks.onReproduce?.(record);
      else if (action === "variant") await this.callbacks.onVariant?.(record);
      else if (action === "workspace") this.callbacks.onWorkspace?.(record.source_workspace || "random", record);
    } catch (error) {
      this.callbacks.notify?.(error.message || String(error));
    }
  }
}
