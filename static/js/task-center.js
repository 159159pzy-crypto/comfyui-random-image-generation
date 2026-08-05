import { api } from "./api.js";
import { collectIds, element, formatTime, replace } from "./dom.js";
import { v7State } from "./v7-state.js";

const ACTIVE_STATES = new Set(["planning", "queued", "running", "cancelling", "paused"]);
const TERMINAL_STATES = new Set(["completed", "failed", "cancelled", "interrupted"]);

export function normalizeJob(job) {
  const workspace = job.workspace || job.source_workspace || job.metadata?.source_workspace || "studio";
  const rawState = job.state || job.status || job.stage || "queued";
  const state = rawState === "succeeded" ? "completed" : ["timed_out", "partial"].includes(rawState) ? "failed" : rawState;
  const fallbackMessage = {
    completed: "任务已完成",
    failed: "任务失败",
    cancelled: "任务已取消",
    interrupted: "任务已中断",
    queued: "等待执行",
    running: "任务执行中",
  }[state] || "任务状态已更新";
  const message = TERMINAL_STATES.has(state)
    ? (state === "failed" ? job.error_summary || job.message : fallbackMessage)
    : job.message || job.error_summary || job.stage || job.task_type || job.job_type || fallbackMessage;
  return {
    ...job,
    id: job.id || job.run_id,
    workspace: ["random", "natural", "studio"].includes(workspace) ? workspace : "studio",
    state,
    message,
    progress: job.progress || { completed: job.completed_items || 0, total: job.total_items || 0 },
    createdAt: job.created_at || job.updated_at,
    rawState,
    canRetry: Boolean(job.can_retry ?? ["failed", "cancelled", "interrupted", "timed_out", "partial"].includes(rawState)),
  };
}

export function createTaskCenter() {
  const ui = collectIds([
    "taskCenterButton", "taskCenterCount", "taskCenterDialog", "taskCenterClose", "taskCenterRefresh",
    "taskCenterList", "taskCenterEmpty", "taskCenterSource", "taskCenterWorkspace", "taskCenterState",
  ]);
  let jobs = [];
  let timer = null;

  async function fetchJobs() {
    await v7State.init();
    const payload = await api("/api/v7/jobs?limit=100");
    ui.taskCenterSource.textContent = "V7 全局持久化队列";
    return (payload?.items || payload?.jobs || []).map(normalizeJob);
  }

  async function jobAction(job, action) {
    const button = ui.taskCenterList.querySelector(`[data-job-id="${CSS.escape(String(job.id))}"] [data-action="${action}"]`);
    if (button) button.disabled = true;
    try {
      await api(`/api/v7/jobs/${encodeURIComponent(job.id)}/${action}`, { method: "POST", body: "{}" });
      await refresh();
    } catch (error) {
      window.toast?.(error.message);
      if (button) button.disabled = false;
    }
  }

  async function showEvents(job, article) {
    let panel = article.querySelector(".task-event-list");
    if (panel) { panel.remove(); return; }
    panel = element("div", { className: "task-event-list", text: "正在读取事件…" });
    article.append(panel);
    try {
      const payload = await api(`/api/v7/jobs/${encodeURIComponent(job.id)}/events`);
      const events = Array.isArray(payload) ? payload : payload.items || payload.events || [];
      replace(panel, events.length ? events.slice(-20).reverse().map((event) => element("div", {}, [
        element("strong", { text: event.message || event.stage || event.event || event.state || "任务事件" }),
        element("small", { text: formatTime(event.created_at || event.timestamp) }),
      ])) : [element("span", { text: "暂无事件" })]);
    } catch (error) {
      panel.textContent = error.message;
    }
  }

  function renderJob(job) {
    const completed = Number(job.progress?.completed || 0);
    const total = Number(job.progress?.total || 0);
    const meta = [job.workspace === "natural" ? "自然语言" : job.workspace === "random" ? "随机工作台" : "Studio"];
    if (total > 0) meta.push(`${completed} / ${total}`);
    meta.push(formatTime(job.createdAt));
    const actions = element("div", { className: "task-item-actions" }, [
      element("button", { text: "打开", attrs: { type: "button", "data-action": "open" } }),
      element("button", { text: "事件", attrs: { type: "button", "data-action": "events" } }),
      ...(ACTIVE_STATES.has(job.state) ? [element("button", { text: "取消", attrs: { type: "button", "data-action": "cancel" } })] : []),
      ...(job.canRetry && TERMINAL_STATES.has(job.state) ? [element("button", { text: "重试", attrs: { type: "button", "data-action": "retry" } })] : []),
    ]);
    const article = element("article", { className: "task-center-item", dataset: { state: job.state, jobId: job.id } }, [
      element("div", { className: "task-center-copy" }, [
        element("strong", { text: job.message || job.id }),
        element("small", { text: meta.join(" · ") }),
      ]),
      element("span", { className: "task-state", text: job.state }),
      actions,
    ]);
    actions.querySelector('[data-action="open"]')?.addEventListener("click", () =>
      window.dispatchEvent(new CustomEvent("studio:open-workspace", { detail: { workspace: job.workspace, job } })),
    );
    actions.querySelector('[data-action="events"]')?.addEventListener("click", () => showEvents(job, article));
    actions.querySelector('[data-action="cancel"]')?.addEventListener("click", () => jobAction(job, "cancel"));
    actions.querySelector('[data-action="retry"]')?.addEventListener("click", () => jobAction(job, "retry"));
    return article;
  }

  function render() {
    const workspace = ui.taskCenterWorkspace?.value || "";
    const state = ui.taskCenterState?.value || "";
    const visible = jobs.filter((job) => (!workspace || job.workspace === workspace) && (!state || job.state === state));
    replace(ui.taskCenterList, visible.map(renderJob));
    ui.taskCenterEmpty.hidden = visible.length > 0;
    const activeCount = jobs.filter((job) => ACTIVE_STATES.has(job.state)).length;
    ui.taskCenterCount.textContent = String(activeCount);
    ui.taskCenterCount.hidden = activeCount === 0;
    ui.taskCenterButton.setAttribute("aria-label", activeCount ? `任务中心，${activeCount} 个进行中任务` : "任务中心");
  }

  async function refresh() {
    ui.taskCenterRefresh.disabled = true;
    try {
      jobs = await fetchJobs();
      jobs.sort((left, right) => new Date(right.createdAt || 0) - new Date(left.createdAt || 0));
      render();
    } catch (error) {
      replace(ui.taskCenterList, [element("p", { className: "task-center-error", text: error.message })]);
      ui.taskCenterEmpty.hidden = true;
    } finally {
      ui.taskCenterRefresh.disabled = false;
    }
  }

  function open() {
    refresh();
    ui.taskCenterDialog.showModal();
  }

  function close() {
    window.clearInterval(timer);
    timer = null;
    ui.taskCenterDialog.close();
  }

  ui.taskCenterButton.addEventListener("click", open);
  ui.taskCenterClose.addEventListener("click", close);
  ui.taskCenterRefresh.addEventListener("click", refresh);
  ui.taskCenterWorkspace?.addEventListener("change", render);
  ui.taskCenterState?.addEventListener("change", render);
  ui.taskCenterDialog.addEventListener("close", () => { window.clearInterval(timer); timer = null; });
  window.addEventListener("studio:jobs-changed", refresh);
  refresh();
  return { refresh };
}
