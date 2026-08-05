import { element, replace } from "./dom.js";

export function canonicalLoraFilename(value) {
  return String(value || "").trim().replaceAll("\\", "/").replace(/^\.\/+/, "");
}

function loraIdentity(value) {
  return canonicalLoraFilename(value).toLocaleLowerCase();
}

export function normalizeLoras(items = []) {
  const seen = new Set();
  return (Array.isArray(items) ? items : []).map((item, index) => ({
    filename: canonicalLoraFilename(item.filename || item.name || item.path),
    enabled: item.enabled !== false,
    strength: Number.isFinite(Number(item.strength)) ? Number(item.strength) : 0.8,
    role: ["style", "character", "detail", "concept", "utility", "other"].includes(item.role) ? item.role : "style",
    order: Number.isFinite(Number(item.order)) ? Number(item.order) : index,
  })).filter((item) => item.filename).sort((a, b) => a.order - b.order).filter((item) => {
    const identity = loraIdentity(item.filename);
    if (seen.has(identity)) return false;
    seen.add(identity);
    return true;
  }).map((item, order) => ({ ...item, order }));
}

export function normalizeLoraInventory(items = []) {
  const seen = new Set();
  return (Array.isArray(items) ? items : []).flatMap((item) => {
    const source = typeof item === "string" ? { filename: item } : item || {};
    const filename = canonicalLoraFilename(source.filename || source.name || source.path);
    const identity = loraIdentity(filename);
    if (!filename || seen.has(identity)) return [];
    seen.add(identity);
    return [{ ...source, filename }];
  });
}

function loraLabel(item) {
  return item.display_name || item.filename || item.name || "未命名 LoRA";
}

export class MultiLoraControl extends EventTarget {
  constructor({ list, addButton, catalog, empty, summary, initial = [] }) {
    super();
    this.list = list;
    this.addButton = addButton;
    this.catalog = catalog;
    this.empty = empty;
    this.summary = summary;
    this.inventory = [];
    this.value = normalizeLoras(initial);
    addButton?.addEventListener("click", () => this.addFirstAvailable());
    this.render();
  }

  setInventory(items) {
    this.inventory = normalizeLoraInventory(items);
    this.render();
  }

  setValue(items, { silent = false } = {}) {
    this.value = normalizeLoras(items);
    this.render();
    if (!silent) this.#changed();
  }

  getValue() {
    return normalizeLoras(this.value);
  }

  addFirstAvailable() {
    const used = new Set(this.value.map((item) => loraIdentity(item.filename)));
    const item = this.inventory.find((candidate) => !used.has(loraIdentity(candidate.filename)));
    if (!item) return;
    this.value.push({ filename: item.filename, enabled: true, strength: 0.8, role: "style", order: this.value.length });
    this.render();
    this.#changed();
  }

  #changed() {
    this.dispatchEvent(new CustomEvent("change", { detail: this.getValue() }));
  }

  render() {
    if (!this.list) return;
    const rows = this.value.map((item, index) => {
      const select = element("select", { attrs: { "aria-label": `LoRA ${index + 1}` } }, [
        ...this.inventory.map((candidate) => element("option", {
          text: loraLabel(candidate), attrs: { value: candidate.filename },
        })),
      ]);
      const inventoryItem = this.inventory.find(
        (candidate) => loraIdentity(candidate.filename) === loraIdentity(item.filename),
      );
      if (!inventoryItem)
        select.prepend(element("option", { text: `当前缺失 · ${item.filename}`, attrs: { value: item.filename } }));
      select.value = inventoryItem?.filename || item.filename;
      const enabled = element("input", { attrs: { type: "checkbox", title: "启用", "aria-label": "启用 LoRA" } });
      enabled.checked = item.enabled;
      const strength = element("input", { attrs: { type: "number", min: -10, max: 10, step: 0.05, value: item.strength, "aria-label": "LoRA 强度" } });
      const role = element("select", { attrs: { "aria-label": "LoRA 角色" } }, [
        element("option", { text: "风格", attrs: { value: "style" } }),
        element("option", { text: "角色", attrs: { value: "character" } }),
        element("option", { text: "细节", attrs: { value: "detail" } }),
        element("option", { text: "概念", attrs: { value: "concept" } }),
        element("option", { text: "工具", attrs: { value: "utility" } }),
        element("option", { text: "其他", attrs: { value: "other" } }),
      ]);
      role.value = item.role;
      const action = (label, title, handler, disabled = false) => {
        const button = element("button", { text: label, attrs: { type: "button", title, "aria-label": title } });
        button.disabled = disabled;
        button.addEventListener("click", handler);
        return button;
      };
      const row = element("div", { className: "shared-lora-row" }, [
        enabled, select, strength, role,
        element("div", { className: "shared-lora-actions" }, [
          action("↑", "上移", () => this.#move(index, -1), index === 0),
          action("↓", "下移", () => this.#move(index, 1), index === this.value.length - 1),
          action("×", "移除", () => { this.value.splice(index, 1); this.render(); this.#changed(); }),
        ]),
      ]);
      select.addEventListener("change", () => { item.filename = canonicalLoraFilename(select.value); this.#changed(); });
      enabled.addEventListener("change", () => { item.enabled = enabled.checked; this.#changed(); });
      strength.addEventListener("input", () => { item.strength = Number(strength.value); this.#changed(); });
      role.addEventListener("change", () => { item.role = role.value; this.#changed(); });
      return row;
    });
    replace(this.list, rows);
    if (this.empty) this.empty.hidden = rows.length > 0;
    if (this.summary) this.summary.textContent = `${this.value.filter((item) => item.enabled).length} 启用 / ${this.value.length} 配置`;
  }

  #move(index, offset) {
    const target = index + offset;
    if (target < 0 || target >= this.value.length) return;
    [this.value[index], this.value[target]] = [this.value[target], this.value[index]];
    this.value.forEach((item, order) => { item.order = order; });
    this.render();
    this.#changed();
  }
}

export function populateModels(select, models, selected = "") {
  if (!select) return;
  const items = (Array.isArray(models) ? models : []).map((item) => typeof item === "string" ? item : item.filename || item.name).filter(Boolean);
  replace(select, items.map((name) => element("option", { text: name, attrs: { value: name } })));
  if (selected && !items.includes(selected)) select.prepend(element("option", { text: `当前缺失 · ${selected}`, attrs: { value: selected } }));
  if (!select.options.length) select.append(element("option", { text: "没有可用模型", attrs: { value: "" } }));
  select.value = selected && [...select.options].some((option) => option.value === selected) ? selected : select.options[0]?.value || "";
}

export function populatePresets(select, presets, selected = "") {
  if (!select) return;
  replace(select, [
    element("option", { text: "不应用预设", attrs: { value: "" } }),
    ...(Array.isArray(presets) ? presets : []).map((preset) => element("option", {
      text: `${preset.favorite ? "★ " : ""}${preset.name}`, attrs: { value: preset.id },
    })),
  ]);
  select.value = selected;
}
