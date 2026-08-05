export const byId = (id) => document.getElementById(id);

export function collectIds(ids) {
  return Object.fromEntries(ids.map((id) => [id, byId(id)]));
}

export function element(tag, { className = "", text = "", attrs = {}, dataset = {} } = {}, children = []) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== "") node.textContent = String(text);
  for (const [name, value] of Object.entries(attrs)) {
    if (value !== undefined && value !== null) node.setAttribute(name, String(value));
  }
  for (const [name, value] of Object.entries(dataset)) node.dataset[name] = String(value);
  node.append(...children.filter(Boolean));
  return node;
}

export function replace(container, children) {
  container.replaceChildren(...children.filter(Boolean));
}

export function plainValue(value) {
  if (Array.isArray(value)) return value.join("、") || "未设置";
  if (value && typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value ?? "未设置");
}

export function detailRow(key, value) {
  return element("div", {}, [
    element("strong", { text: key }),
    element("span", { text: plainValue(value) }),
  ]);
}

export function replaceDetails(container, items, emptyText) {
  const rows = items.map(([key, value]) => detailRow(key, value));
  replace(container, rows.length ? rows : [element("span", { className: "natural-muted", text: emptyText })]);
}

export function formatTime(value) {
  if (!value) return "--";
  const numeric = Number(value);
  const date = Number.isFinite(numeric)
    ? new Date(numeric < 1e12 ? numeric * 1000 : numeric)
    : new Date(value);
  return Number.isNaN(date.getTime()) ? "--" : date.toLocaleString();
}
