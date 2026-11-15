/* Local character classification editor. The server owns recognition and previews. */
globalThis.CharacterClassification = (() => {
  const roles = { trigger: "触发词", feature: "特征", unknown: "待确认" };
  const sources = { dictionary: "本地词库", metadata: "名称 / 作品匹配", candidate: "名称候选，需确认", unknown: "未识别", manual: "已人工确认", explicit: "导入文件指定", conflict: "词库分类冲突" };
  const copy = value => JSON.parse(JSON.stringify(value));
  const escape = value => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  const input = value => Object.fromEntries(["prompt", "title", "subtitle", "copyright"].map(key => [key, String(value?.[key] || "")]));
  const key = value => JSON.stringify(input(value));
  const isReady = value => Array.isArray(value?.character_parts) && value.character_parts.length > 0
    && value.character_parts.every(part => part.role === "trigger" || part.role === "feature")
    && (value.character_parts.some(part => part.role === "trigger") || value.character_no_trigger === true);
  const fields = value => ({ character_parts: copy(value.character_parts || []), character_no_trigger: value.character_no_trigger === true });

  function render(root, state, actions) {
    if (!root) return;
    const value = state.value || {}, parts = value.character_parts || [];
    const counts = Object.fromEntries(Object.keys(roles).map(role => [role, parts.filter(part => part.role === role).length]));
    const hasTrigger = counts.trigger > 0;
    root.classList.add("character-classification");
    root.innerHTML = `<div class="character-classification-heading"><strong>触发词与特征</strong><button type="button" class="button ghost compact" data-character-reanalyze ${state.busy || state.disabled ? "disabled" : ""}>重新识别</button></div>
      <p class="character-classification-status" role="status">${state.busy ? "正在本地识别…" : state.dirty ? "提示词已改变，等待识别" : isReady(value) ? "分类已就绪" : "请确认未识别的标签；完整模式仍保留原提示词。"}</p>
      <div class="character-classification-counts">${Object.entries(roles).map(([role, title]) => `<span data-role="${role}">${title} ${counts[role]}</span>`).join("")}</div>
      <div class="character-classification-groups">${Object.entries(roles).map(([role, title]) => {
        const matching = parts.map((part, index) => ({part, index})).filter(entry => entry.part.role === role);
        if (!matching.length) return "";
        return `<section class="character-classification-group" data-role="${role}"><h4>${title}</h4>${matching.map(({part, index}) => `<label class="character-classification-tag"><span><b>${escape(part.text)}</b><small>${escape(sources[part.source] || "本地识别")}${part.suggestion ? ` · 建议：${escape(roles[part.suggestion] || part.suggestion)}` : ""}</small></span><select data-character-part="${index}" aria-label="${escape(part.text)} 的分类" ${state.busy || state.disabled ? "disabled" : ""}>${Object.entries(roles).map(([next, label]) => `<option value="${next}" ${next === role ? "selected" : ""}>${label}</option>`).join("")}</select></label>`).join("")}</section>`;
      }).join("")}</div>
      <label class="character-no-trigger"><input type="checkbox" data-character-no-trigger ${value.character_no_trigger ? "checked" : ""} ${hasTrigger || state.busy || state.disabled ? "disabled" : ""}><span>确认此角色没有独立触发词</span></label>
      <p class="character-classification-error" role="alert" ${state.error ? "" : "hidden"}>${escape(state.error)}</p>
      <details class="character-mode-previews"><summary>查看三种模式的提示词</summary>${[["trigger_tags", "触发词 + 特征"], ["trigger", "仅触发词"], ["trigger_tags_no_clothing", "触发词 + 特征（剔除服装）"]].map(([mode, label]) => {
        const text = state.dirty || state.busy ? "完成识别后显示" : value.previews?.[mode];
        return `<div><strong>${label}</strong><p>${escape(text == null ? "完成分类确认后显示" : text || "此角色不贡献提示词")}</p></div>`;
      }).join("")}</details>`;
    root.querySelector("[data-character-reanalyze]").addEventListener("click", () => actions.refresh(true));
    root.querySelectorAll("[data-character-part]").forEach(select => select.addEventListener("change", () => actions.role(Number(select.dataset.characterPart), select.value)));
    root.querySelector("[data-character-no-trigger]").addEventListener("change", event => actions.noTrigger(event.target.checked));
  }

  function createController(options) {
    let value = options.initial ? copy(options.initial) : null;
    let acceptedKey = value && value.prompt === options.getInput().prompt ? key(options.getInput()) : "";
    let sequence = 0, disposed = false, busy = false, dirty = !acceptedKey, error = "", pending = null;
    const active = () => !disposed && (!options.current || options.current());
    const draw = () => (options.render || render)(options.root, { value, busy, dirty, error, disabled: !!options.disabled?.() }, actions);
    const notify = () => { options.onChange?.(value ? copy(value) : null, { pending: busy || dirty, error }); draw(); };
    const actions = {
      refresh: reanalyze => { void refresh(reanalyze).catch(() => {}); },
      role(index, role) {
        if (!active() || busy || options.disabled?.() || !value?.character_parts[index] || !Object.hasOwn(roles, role)) return;
        value.character_parts[index] = { text: value.character_parts[index].text, role, source: "manual" };
        if (role === "trigger") value.character_no_trigger = false;
        value.character_status = isReady(value) ? "ready" : "needs_review";
        void refresh(false).catch(() => {});
      },
      noTrigger(checked) {
        if (!active() || busy || options.disabled?.() || !value || value.character_parts.some(part => part.role === "trigger")) return;
        value.character_no_trigger = !!checked;
        value.character_status = isReady(value) ? "ready" : "needs_review";
        void refresh(false).catch(() => {});
      },
    };
    async function refresh(reanalyze = false) {
      if (!active()) return null;
      const currentInput = input(options.getInput()), requestedKey = key(currentInput), ticket = ++sequence;
      if (!currentInput.prompt.trim()) { value = null; busy = false; dirty = true; error = "请先填写提示词"; notify(); return null; }
      const body = { ...currentInput };
      if (value) {
        if (!reanalyze && value.prompt === currentInput.prompt) Object.assign(body, fields(value));
        else {
          body.previous = copy(value);
          if (value.prompt === currentInput.prompt) body.character_no_trigger = value.character_no_trigger === true;
        }
      }
      if (reanalyze || (acceptedKey !== requestedKey && !body.character_parts)) body.reanalyze = true;
      busy = true; dirty = acceptedKey !== requestedKey; error = ""; notify();
      const job = (async () => {
        try {
          const result = await options.analyze(body);
          if (!active() || ticket !== sequence || requestedKey !== key(options.getInput())) return null;
          value = copy(result); acceptedKey = requestedKey; dirty = false; return copy(value);
        } catch (cause) {
          if (active() && ticket === sequence && requestedKey === key(options.getInput())) error = cause.message || "本地识别失败，请重试";
          throw cause;
        } finally {
          if (active() && ticket === sequence) { busy = false; pending = null; notify(); }
        }
      })();
      pending = job;
      return job;
    }
    const controller = {
      refresh,
      invalidate() { if (!active()) return; ++sequence; busy = false; pending = null; dirty = true; error = ""; notify(); },
      async ensure() {
        if (!active()) throw new Error("角色编辑已关闭或切换，请重新打开");
        if (pending) await pending;
        if (!active()) throw new Error("角色编辑已关闭或切换，请重新打开");
        if (dirty || error || acceptedKey !== key(options.getInput()) || !value?.previews) {
          const result = await refresh(false);
          if (!result) throw new Error("角色内容已改变，请等待重新识别后保存");
        }
        return copy(value);
      },
      dispose() { disposed = true; ++sequence; pending = null; },
      get value() { return value ? copy(value) : null; },
      get pending() { return busy || dirty; },
      actions,
    };
    draw();
    return controller;
  }
  return { createController, isReady, fields, input, key, render };
})();
