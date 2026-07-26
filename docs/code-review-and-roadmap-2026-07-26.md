# Anima Random Studio 代码质量审查与路线图

审查日期:2026-07-26
审查范围:`anima_webui/` 全部 10 个模块、`static/` 前端三件套、`tests/` 全部 8 个测试文件、模板与启动脚本
审查方式:干净 Linux 容器(Python 3.11 / Node 22)实际运行测试套件与静态检查 + 三路独立深度代码审查,所有 P0/P1 发现均已逐条对照源码核实

---

## 一、总体评价

| 领域 | 评级 | 一句话 |
| --- | --- | --- |
| Python 后端 | B | 校验与持久化是 A 级水准,并发处理与鲁棒性拉低分数 |
| 前端 | C+ | 功能完成度和细节很高,但有真实 XSS 和严重可维护性问题 |
| 测试 | B+ | 66 个测试、断言质量普遍很高,但有 1 个非密闭测试和大片盲区 |
| 工程化 | D | 无依赖清单、无 CI、无 LICENSE、无 lint,全靠 README 手工清单 |
| **综合** | **B-** | 个人项目里算相当扎实,但离"可公开、可协作"还差一个工程化底座 |

**验证结果**(干净环境实测):

- `pytest`:**65 通过,1 失败**(`test_favorite_crud_preserves_other_sections`,原因见 P1-5,非密闭测试,在你的 Windows 机器上会通过)
- `node --check static/app.js`:通过
- `python -m compileall`:通过
- 两个模板 JSON:合法

**做得好的地方**(实名表扬,保持住):

- JSON 持久化统一 `mkstemp → fsync → os.replace` 原子写,断电不留半截文件(custom_prompts.py:442-449、style_presets.py:156-163)
- 启动时把遗留 `running` 批次标为 `interrupted`(history.py:63-66)
- 输入校验极严:bool 冒充 int、NaN/Inf、未知键、LoRA 路径穿越全部拒绝(workflow.py:109-111、183、131-141)
- SQL 全程参数化;批次任务异常全兜底并落库(runner.py:145-153)
- 前端 `request()` 统一封装、池加载竞态守卫(app.js:356)、原生 `<dialog>` 模态、`replaceChildren` 无监听器泄漏、主题防闪烁
- `test_catalog.py:18-53` 的假 tools 目录构造是全仓库密闭测试的范本
- README 宣称的 `prefers-reduced-motion / reduced-transparency / contrast`、主题记忆均已核实为真实现

---

## 二、问题清单

### P0 — 现在就修(共 3 个,合计约 1 小时工作量)

**P0-1|每张输出图内嵌的工作流元数据被写坏(100% 复现)**
`anima_webui/workflow.py:841-842`

```python
for node_id, value in ((23, settings["width"]), (31, settings["height"]), (35, 1), (39, settings["steps"]), (41, settings["cfg"]), (12, filename_prefix)):
    _set_ui_widget(ui, node_id, value)   # ← 漏传 index
```

`_set_ui_widget` 不传 `index` 时走 `node["widgets_values"] = value` 分支(workflow.py:604-609),把模板中的数组(`[832]`、`['Anima']`,已实测确认)整体替换成裸标量。这份 UI 工作流经 `build_submission` 嵌入每张 PNG 的 `extra_pnginfo.workflow`——把生成的图拖回 ComfyUI 还原时,宽/高/步数/CFG 全部变 undefined。同函数其余调用都正确传了 index,属笔误。
**修复**:改为 `_set_ui_widget(ui, node_id, value, 0)`。

**P0-2|`escapeHtml` 不转义引号 → 属性注入型存储 XSS**
`static/app.js:121-125`

```js
function escapeHtml(value) {
  const span = document.createElement("span");
  span.textContent = value == null ? "" : String(value);
  return span.innerHTML;   // 只转义 & < >,引号原样通过
}
```

约 13 处把它用在 HTML 属性里,其中用户可控的至少有:固定提示词 `value="${escapeHtml(draft.fixed[section])}"`(L279)、自定义条目标题 `title="${escapeHtml(displayTitle(item))}"`(L475)。输入 `x" onfocus="alert(1)" autofocus x="` 即可注入事件属性执行脚本。
威胁模型说明:WebUI 只监听本机,但**批量导入的提示词包是现实攻击面**——导入别人分享的 JSON/CSV 词库时,恶意标题会持久化并在浏览时执行,进而可调用本机 API 篡改数据、发起生成任务。
**修复**:`escapeHtml` 末尾追加 `.replaceAll('"', "&quot;").replaceAll("'", "&#39;")`;长期方案是属性值一律走 `setAttribute`。

**P0-3|姿势冲突提示完全未转义 → 存储型 XSS(悬停都不用)**
`static/app.js:488-490`

```js
groups.get(slot).push(item.title);            // 用户可控标题
ui.poseConflict.innerHTML = `...${names.join("、")}...`;  // 零转义直接注入
```

自定义姿势条目标题写成 `<img src=x onerror=...>`,与另一冲突项同时选中即执行,纯元素内容注入。
**修复**:`names.map(escapeHtml)`(配合 P0-2 的修复)。

### P1 — 本周内修(影响正确性/可用性)

**P1-1|开始批次存在检查-后-启动竞态**(runner.py:40-69)
`if self.active()` 检查之后要连续 `await` 两次数 MB 的 `/object_info` 拉取(窗口可达秒级),期间第二个 `POST /api/batches` 也能通过检查——两个 `_run` 并发跑、共享状态、ComfyUI 收双份任务,`self.task` 只记后者。快速双击"开始"即可触发。
**修复**:用 `asyncio.Lock` 包住整个 `start`,或进入后立即占位。

**P1-2|收藏全部是无锁"读-改-写整体覆盖"**(favorites.py:180-206 及同模式 5 处)
每次操作是"读 ComfyUI 全量收藏 → 内存改 → 整体写回"。连续快速点星标时后写者覆盖前写者,收藏静默丢失。全代码库经 grep 确认没有任何 `asyncio.Lock`。
**修复**:`FavoritesService` 加一把 `asyncio.Lock` 串行化所有变更。

**P1-3|`wait_for_history` 无限轮询,批次可能永久卡死**(comfy.py:160-170 + runner.py:91-92)
`while True` 无总超时、无停止钩子。若 prompt 从 ComfyUI history 消失(用户清空历史 / ComfyUI 快速重启丢队列),批次永远 running,`active()` 恒真 → 再也无法开新批次,只能重启 WebUI。"停止"也只在每张图之间生效,从不调 ComfyUI `/interrupt`。
**修复**:轮询循环内检查停止回调 + 无进展超时;停止时调用 `/interrupt`。

**P1-4|同步 sqlite 与 fsync 直接跑在事件循环上**(history.py 各处、custom_prompts.py:448、style_presets.py:162)
批次运行中每张图落库、每次保存预设都会短暂冻结整个事件循环(机械盘 fsync 可达上百毫秒)。单用户影响有限,但确属 async 误用,会放大 P1-1 竞态窗口。
**修复**:包进 `asyncio.to_thread`(sqlite 连接需 `check_same_thread=False`)。

**P1-5|那个失败的测试:非密闭,依赖你本机的 Anima Tools**(tests/test_server.py:79-85)
`asyncSetUp` 调 `create_app` 时唯独漏传 `anima_tools_dir`,触发自动探测链,最后落到硬编码的 `F:/comfyui/custom_nodes/Comfyui-Anima-Tools`(catalog.py:125-126)。在你机器上它吸入**真实词库**跑测试(结果随本机数据漂移),在任何干净环境必挂(pose 池 0 条 → IndexError)。已反向验证:构造假 tools 目录后该测试即通过。
**修复**:照抄隔壁 `test_catalog.py:18-53` 的现成模式,在 tempdir 里生成假数据文件并显式传参。

**P1-6|键盘可达性两处实质缺陷**(styles.css:374-375、418 + app.js:346/351)
关闭的池抽屉只是 `translateX` 移出视口,约 20 个控件仍在 Tab 序列里,键盘用户会 Tab 进不可见区域;收藏树焦点框引用了从未定义的 `var(--focus)`,方向键导航时看不见焦点。README 宣传的键盘导航被这两处削弱。
**修复**:关闭时给抽屉加 `inert`;`--focus` 改用已定义的 `--accent`。

**P1-7|轮询常开且失败刷屏**(app.js:822、329)
`setInterval(pollBatch, 1200)` 永不停、后台标签页也打;服务端一挂,每 1.2 秒弹一个错误 toast。
**修复**:`document.hidden` 时暂停、空闲降频;轮询错误只在状态变化时提示一次。

**P1-8|工程化三缺:依赖清单、CI、LICENSE**
`requirements.txt`/`pyproject.toml` 不存在,依赖只写在 README 散文里,零版本约束(aiohttp 已刷 91 条弃用告警,aiohttp 4 发布后大概率直接坏);无任何 CI——P1-5 那种"本机永远绿"的测试正是无 CI 的直接后果;README 自己承认无 LICENSE,而仓库地址是公开的。
**修复**:见路线图第二阶段。

### P2 — 排期修(健壮性/可维护性)

1. `GET /api/images/abc` 返回 500 而非 400——`int(match_info)` 裸抛 ValueError(server.py:384、390);路由改 `{image_id:\d+}`。
2. 无 Origin/Host 校验——恶意网页可免预检直发 `POST /api/batches`、导入接口(CSRF/DNS-rebinding 面);中间件校验 Host/Origin 为本机即可。
3. 全项目无 logging——损坏的内置数据文件表现为"池是空的"且无任何日志(catalog.py:204-207);收藏昵称同步失败被静默吞掉(server.py:261-262)。
4. `data/` 下任一 JSON/sqlite 损坏 → 启动直接 traceback;应改名 `.corrupt.bak` 后空库启动并在界面告警。
5. workflow.py 魔法节点 ID/widget 下标散布全文件(`COMPOSER_ID=60`、`api["23"]`、`detailer_widgets[26]`…),重导出工作流即全面崩;集中成常量表 + 加载时一次性校验。
6. 硬编码 `F:/comfyui/...`(catalog.py:125-126、启动 bat);bat 应支持环境变量覆盖。
7. 每次开批次拉两遍数 MB 的 `/object_info`(runner.py:48-51);拉一次复用。
8. `pool` 与 `pool_query` 两个 handler 几乎逐行重复(server.py:135-192);抽公共函数。
9. `loadHistory` 无竞态守卫,快速翻页可能显示过期页(app.js:331);仿 `poolRequest` 加序号。
10. LoRA/子分组搜索无去抖,与池搜索(220ms)不一致(app.js:795-797)。
11. app.js 单行最长 2448 字符,`openDeleteGroupDialog`/`confirmGroupDelete` 各自整个函数挤在一行(L508-509),diff 和 review 几乎不可用;先 Prettier 格式化,再拆模块。
12. aiohttp 字符串 app key 弃用告警 91 条(server.py:91-97);迁移 `web.AppKey`。
13. 测试盲区:comfy.py 真实网络错误路径、`validate_comfy_url`、`POST /api/pools/{section}/query` 端点、前端全部——零覆盖。
14. `tests/` 中 2 个文件缺 `sys.path` 引导,单独运行必挂(靠字典序副作用过);加 `tests/conftest.py`。
15. 副本中发现 `__pycache__`/`.pytest_cache` 目录,确认它们不在 git 追踪里(若在,`git rm -r --cached` 清理)。
16. 无版本号、无 CHANGELOG;`sources/AnimaBasicV7 (1).json` 含空格括号的原始文件名被测试直接引用,跨平台脚本易踩坑。

---

## 三、下一步路线图

### 第零步:止血(建议今天,约 1–2 小时)

1. 修 P0-1(补一个 `, 0`)→ 跑 `pytest tests/test_workflow.py`,并生成一张图拖回 ComfyUI 验证还原正常
2. 修 P0-2 + P0-3(escapeHtml 补引号转义 + 冲突提示走转义)
3. 修 P1-5(测试改密闭)→ 从此 `pytest` 在任何机器上全绿,为 CI 铺路

### 第一阶段:并发与卡死(本周,约 1 天)

按顺序做 P1-1 → P1-2 → P1-3 → P1-4:两把 `asyncio.Lock`、轮询超时 + `/interrupt`、阻塞 I/O 挪进 `to_thread`。每项都补一个对应的回归测试(test_runner.py 里已有用 `asyncio.Event` 控制时序的成熟范式可抄)。做完后"快速双击开始""生成中清空 ComfyUI 历史""连点星标"三个场景手工过一遍。

### 第二阶段:工程化底座(下周,约半天)

1. `pyproject.toml`:声明 `aiohttp>=3.9,<4`,dev 依赖 pytest + ruff,定版本号 0.1.0
2. `tests/conftest.py` 统一 sys.path;顺手修 P2-14
3. GitHub Actions:ubuntu + windows 两个 job,跑 `ruff check`、`pytest`、`node --check`、模板 JSON 校验(替代 README 里那五条 PowerShell 手工命令)
4. 补 LICENSE(个人项目常用 MIT;若不想让人商用可选 PolyForm Noncommercial——需要的话我可以帮你比较)
5. 迁移 `web.AppKey` 消掉 91 条告警

### 第三阶段:可维护性(之后 1–2 周,穿插做)

1. app.js:先 Prettier 全量格式化(一次性提交,不混入逻辑改动),再按功能拆成 ES modules(pools / favorites / loras / presets / history / api),浏览器原生支持,无需引入构建
2. workflow.py:节点 ID 常量表 + `WorkflowTemplates.load` 时一次性校验所有必需节点,把"重导出工作流就崩"变成启动时的明确报错
3. 引入 logging(文件轮转到 `data/webui.log`),消灭静默吞错
4. 数据文件损坏降级启动(P2-4)
5. 用你已有的 Playwright 基础加 3–5 条冒烟测试:打开页面、切主题、选条目、开批次(mock ComfyUI)、删除确认面板

### 第四阶段:功能方向(修完债再说,候选项)

以下是审查中看出的自然延伸方向,优先级由你定:批次队列(排队多个批次而不是 409 拒绝)、生成中途实时预览(ComfyUI websocket 进度)、收藏/词库的导出备份与恢复、历史页按提示词/模型筛选、风格预设导入导出分享。

---

## 四、复验清单(每阶段完成后跑)

```powershell
python -m pytest -q          # 期望:全绿(第零步后)
node --check static\app.js
python -m compileall -q anima_webui tests run.py
```

外加手工:生成一张图拖回 ComfyUI(验 P0-1)、导入一个含 `"` 和 `<img>` 标题的测试词条并浏览/触发冲突提示(验 P0-2/3)、快速双击开始(验 P1-1)。
