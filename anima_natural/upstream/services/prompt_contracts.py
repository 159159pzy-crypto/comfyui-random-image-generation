"""Versioned, task-scoped prompt contracts for the Anima director.

The LLM is a creative planner and a transport client.  It is not the authority
for local model files, Danbooru identity, character appearance, masks, workflow
availability or final prompt validation.  Those decisions remain deterministic
plugin responsibilities.
"""

from __future__ import annotations

import re
from collections.abc import Iterable


PROMPT_CONTRACT_VERSION = "3.0"

TASK_DRAW = "draw"
TASK_PROMPT_PLAN = "prompt_plan"
TASK_REVERSE_DRAW = "reverse_draw"
TASK_SEMANTIC_REDRAW = "semantic_redraw"
TASK_CONTROL_DRAW = "control_draw"
TASK_MASKED_REDRAW = "masked_redraw"
TASK_CHARACTER_SWAP_EDIT = "character_swap_edit"

CAPABILITY_LORA = "lora"
CAPABILITY_DANBOORU = "danbooru"
CAPABILITY_PROMPT_PLAN = "prompt_plan"

_TASK_KINDS = frozenset(
    {
        TASK_DRAW,
        TASK_PROMPT_PLAN,
        TASK_REVERSE_DRAW,
        TASK_SEMANTIC_REDRAW,
        TASK_CONTROL_DRAW,
        TASK_MASKED_REDRAW,
        TASK_CHARACTER_SWAP_EDIT,
    }
)
_CAPABILITIES = frozenset(
    {CAPABILITY_LORA, CAPABILITY_DANBOORU, CAPABILITY_PROMPT_PLAN}
)

TRANSPORT_PIC = "pic"
TRANSPORT_EDIT = "edit"
TRANSPORT_JSON = "json"
TRANSPORT_FUNCTION = "function"

_TRANSPORT_ALIASES = {
    TRANSPORT_PIC: TRANSPORT_PIC,
    TRANSPORT_EDIT: TRANSPORT_EDIT,
    TRANSPORT_JSON: TRANSPORT_JSON,
    TRANSPORT_FUNCTION: TRANSPORT_FUNCTION,
    "structured": TRANSPORT_FUNCTION,
}
_TASK_TRANSPORTS = {
    task: frozenset({TRANSPORT_PIC, TRANSPORT_JSON, TRANSPORT_FUNCTION})
    for task in _TASK_KINDS
}
_TASK_TRANSPORTS[TASK_MASKED_REDRAW] = frozenset({TRANSPORT_EDIT})


CORE_AUTHORITY_CONTRACT = """
Anima Prompt Contract v3 authority boundary:
- The user request is the highest authority for visible identity, clothing,
  action, relation, camera, scene and lighting. Never add a person, body trait,
  outfit, prop, action result or setting merely to make the prompt richer.
- The LLM may plan one coherent image and declare named-character lookup hints.
  It is not authoritative for Danbooru identity, character appearance, LoRA
  filenames, trigger words, masks, workflow availability or final validation.
- Omit uncertain facts. Never convert a guess into a weighted tag, a negative
  tag, a structured field or confident prose.
- Produce one operation and one internally consistent frame. Resolve conflicts
  in subject count, pose, direction, camera, time and light before output.
""".strip()

PIC_TRANSPORT_CONTRACT = """
Output transport:
- Return exactly one `<pic prompt="...">` and no explanation, roleplay prose,
  Markdown or `<think>` content. `prompt` is required; `negative`, `pipeline`
  and `characters` are optional.
- Use `pipeline="base|rtx|iterative"` only when the user explicitly chooses it;
  otherwise omit it so the plugin keeps the active WebUI default.
- For explicitly named characters add `characters="name|work"`; separate up to
  four subjects with semicolons. This field is a lookup hint only. Omit it for
  original, anonymous or uncertain identities and never invent a work title.
- Keep attribute values single-line and do not place unescaped double quotes
  inside them.
""".strip()

STRUCTURED_TRANSPORT_CONTRACT = """
Output transport:
- Call `emit_anima_plan_v1` exactly once and emit no visible text outside its
  arguments. `positive_tags` is required; `negative_tags`, `pipeline` and
  `characters` are optional.
- `characters` contains only `name|work` lookup hints for explicitly named
  characters, at most four. Omit original, anonymous or uncertain identities.
- Do not invent extra keys or encode reasoning, confidence, appearance facts,
  LoRA selections or tool results in the character hints.
""".strip()

JSON_TRANSPORT_CONTRACT = """
Output transport:
- Return exactly one JSON object and no surrounding prose or Markdown.
  `positive_tags` is required; `negative_tags`, `pipeline` and `characters`
  are optional. Do not add extra keys.
- `characters` contains only `name|work` lookup hints for explicitly named
  characters, at most four. Omit original, anonymous or uncertain identities.
- Do not encode reasoning, confidence, appearance facts, LoRA selections or
  tool results in the character hints.
""".strip()

EDIT_TRANSPORT_CONTRACT = """
Output transport:
- Return exactly one `<edit prompt="English result tags" mode="quick|lanpaint">`
  and no `<pic>`, explanation, Markdown or roleplay prose. `negative` is optional.
- Describe only the final visible state inside the already supplied mask. Never
  invent a mask, refer to here/there/mask, or modify the preserved black region.
- Use quick for small bounded edits and lanpaint for structural, large-area,
  hand/foot or detailed clothing reconstruction.
""".strip()

CONVERSATION_PIC_TRANSPORT_CONTRACT = """
Ordinary-chat output:
- When no image is requested, return normal conversation text and no control tag.
- When an image is explicitly requested, ordinary conversation text is optional,
  but the final control item must be exactly one valid `<pic prompt="...">`.
  A promise, bare prompt string or bare LoRA string does not submit ComfyUI.
- Never output `<edit>` for a new-image request and never call or mention the
  private `emit_anima_plan_v1` schema.
""".strip()

HYBRID_PROMPT_CONTRACT = """
Positive prompt composition:
- Keep all exact tool-returned LoRA controls first and unchanged. Then write
  ordered English Danbooru/Anima hard tags, a few visible relation/material
  phrases when needed, and exactly one present-tense scene sentence after a
  period. The sentence belongs inside the same positive prompt.
- Hard tags own discrete facts. The sentence owns relationships, contact points,
  held objects, garment state, foreground/background interaction and main-light
  direction. Repeat only a few high-value anchors and add no new fact.
- Use natural spaces and comma separators for ordinary tags. Preserve exact
  tool-returned filenames and trigger words. Escape ordinary parentheses in
  rendered Danbooru tags as `\\(` and `\\)`.
- Negative tags are minimal, English and evidence-based. Never put character
  identity, work title, LoRA tags or guessed default appearance in negative.
""".strip()

CHARACTER_EVIDENCE_CONTRACT = """
Named-character contract:
- Declare only the requested name and known work. Do not manufacture hair,
  eye, body, species, ear, face or costume facts from model memory.
- Preserve an explicit user-supplied romanized alias such as `飞鸟马时 (toki)`
  in the `name|work` lookup hint or final identity tag; do not silently discard
  the alias before the plugin can exact-confirm it.
- User-explicit appearance and outfit changes remain in the positive prompt.
  The plugin will independently exact-check Character/Copyright evidence,
  remove conflicting identities and unsupported appearance guesses, and add
  only trusted current LoRA or safe Gallery atomic evidence.
- A missing character LoRA is not permission to guess a file and is not a reason
  to keep an old identity. Never copy a whole trainedWords list into the prompt.
""".strip()

LORA_CAPABILITY_CONTRACT = """
Runtime LoRA capability:
- Query a named saved style with `list_anima_lora_presets`; query an explicitly
  named character asset separately with `list_anima_loras`. Use only exact names
  returned in this request after the plugin's mandatory Manager refresh.
- A style preset is one complete ordered base stack. Copy all returned controls
  unchanged and do not replace, truncate or supplement it with guessed peers.
- Character LoRA is optional and separate from the style stack. Use at most the
  uniquely matched current asset; if no unique result exists, continue with
  verified semantic identity rather than guessing a file.
- A character LoRA must bind to the same exact-confirmed Character canonical.
  File name, display name and activation terms are separate namespaces and never
  authorize identity. A fingerprint-bound `identity_bindings` row plus current
  Character/Copyright exact is the authority. Activation terms may use arbitrary
  training syntax and may coexist with the canonical, but must not be queried or
  emitted as a second Character identity. Armed, Bunny, Dress or other distinct
  indexed identities never substitute for the base identity unless explicitly
  requested and exact.
- Do not claim a lookup or save succeeded unless the corresponding tool returned
  success. Tool metadata aids retrieval but never overrides user-visible facts.
""".strip()

DANBOORU_CAPABILITY_CONTRACT = """
Runtime Danbooru capability:
- Use `search_anima_danbooru_tags` only when it is actually available and only
  for bounded uncertainty. Character, Copyright, Artist and General are separate
  evidence domains.
- Character exact or unique alias may identify a character. Copyright only
  qualifies a work; Artist never identifies a character; General describes
  appearance, clothing, action, composition or scene.
- Prefix, keyword, fuzzy, embedding and rerank results are candidates only.
  Exact-confirm the selected canonical in its proper category before using it.
- For a localized character name, query the name together with its explicit work,
  for example `《鸣潮》的菲比`. The localized layer may return several same-name
  candidates and must never choose by popularity. Continue only when the tool
  returns one verified Character canonical with an exact Copyright qualifier.
- Once a Character query returns verified exact, use that returned canonical in
  both positive tags and the character lookup hint. Do not query the same
  identity again by a localized name or replace the exact canonical with prose.
- After one exact Character, its exact Copyright and at most one unique current
  character LoRA are known, stop asset lookup and emit the terminal. Do not spend
  tool steps rechecking common General tags unless the user explicitly asks for
  tag validation.
""".strip()

PROMPT_PLAN_CAPABILITY_CONTRACT = """
Runtime Prompt Plan capability:
- Resolve the requested plan with `list_anima_prompt_plans`; never invent an ID.
  Read the unique plan in detail, keep its exact positive, negative and pipeline
  baseline, and modify only fields the user explicitly asked to change.
- A stored plan does not bypass current LoRA freshness or character evidence
  validation at final submission.
""".strip()

TASK_CONTRACTS = {
    TASK_DRAW: """
Task: create one new image from text. Select one strongest visual moment and do
not simulate masked edit, semantic character swap or standalone image upscale.
""".strip(),
    TASK_PROMPT_PLAN: """
Task: adapt one already confirmed Prompt Plan. Preserve every baseline layer not
explicitly changed by the user; do not rewrite exact LoRA controls or silently
replace its pipeline.
""".strip(),
    TASK_REVERSE_DRAW: """
Task: turn a verified image description into one new Anima image. Treat observed
facts as the baseline, apply the user's additions, and never claim pixel-level
preservation of the source image.
""".strip(),
    TASK_SEMANTIC_REDRAW: """
Task: whole-image semantic redraw without a mask. Rebuild the full frame from the
observed baseline and explicit edit request. Remove contradicted old clothing,
background, expression, action, weather or time. Submit the rebuilt frame through
the selected transport. Do not apply a default style preset unless the user
explicitly names one.
""".strip(),
    TASK_CONTROL_DRAW: """
Task: image-conditioned Anima generation. The plugin already owns pose, depth,
lineart or reference controls. Describe only the final visible image and never
write control-mode names, ControlNet, preprocessors, node IDs or model files in
the visual prompt.
""".strip(),
    TASK_MASKED_REDRAW: """
Task: local masked redraw. Describe only the requested result in the mask. Do not
plan a whole scene, character replacement, ControlNet generation or standalone
upscale.
""".strip(),
    TASK_CHARACTER_SWAP_EDIT: """
Task: apply only the requested non-identity edit to an intermediate prompt before
a separate deterministic character replacement. Preserve the current identity
for now, keep every unmentioned scene fact, remove only contradicted attributes,
and never add the target identity or any guessed LoRA.
""".strip(),
}

STANDARD_DENSITY_CONTRACT = """
Density: Standard. Prefer obedience and stability. Use roughly 14-32 useful
ordinary tags, at most a few relation/material phrases and one concise 18-45
word scene sentence when the request benefits from it. Simpler images may be
shorter. Weight only a few genuinely fragile anchors.
""".strip()

ULTRA_DENSITY_CONTRACT = """
Density: Ultra. Increase only relevant visible evidence: face/hair detail,
garment construction and material, gesture/contact, motion, spatial layers,
environment interaction, main/rim light and color separation. Roughly 30-65
useful ordinary tags and one 35-80 word scene sentence are upper guidance, not
quotas. Never use synonym repetition, conflicting effects or quality slogans to
fake complexity.
""".strip()


def normalize_task_kind(value: str) -> str:
    """Return a known internal task instead of silently changing its meaning."""

    task = str(value).strip().casefold()
    if task not in _TASK_KINDS:
        raise ValueError(f"unsupported prompt task: {value!r}")
    return task


def normalize_transport(value: str, *, task_kind: str | None = None) -> str:
    """Normalize a transport alias and enforce the task/transport matrix."""

    transport = _TRANSPORT_ALIASES.get(str(value).strip().casefold())
    if transport is None:
        raise ValueError(f"unsupported prompt transport: {value!r}")
    if task_kind is None:
        return transport
    task = normalize_task_kind(task_kind)
    if transport not in _TASK_TRANSPORTS[task]:
        allowed = ", ".join(sorted(_TASK_TRANSPORTS[task]))
        raise ValueError(
            f"transport {transport!r} is invalid for task {task!r}; "
            f"allowed: {allowed}"
        )
    return transport


def normalize_capabilities(values: Iterable[str] | None) -> tuple[str, ...]:
    normalized = {
        str(value or "").strip().casefold()
        for value in (values or ())
        if str(value or "").strip().casefold() in _CAPABILITIES
    }
    return tuple(sorted(normalized))


def density_contract(expansion_mode: str) -> str:
    return (
        ULTRA_DENSITY_CONTRACT
        if str(expansion_mode or "standard").strip().casefold() == "ultra"
        else STANDARD_DENSITY_CONTRACT
    )


def build_director_contract(
    *,
    task_kind: str = TASK_DRAW,
    expansion_mode: str = "standard",
    capabilities: Iterable[str] | None = None,
    transport: str = "pic",
) -> str:
    """Build only the contracts needed by one internal director request."""

    task = normalize_task_kind(task_kind)
    caps = normalize_capabilities(capabilities)
    transport_key = normalize_transport(transport, task_kind=task)
    transport_contract = (
        EDIT_TRANSPORT_CONTRACT
        if transport_key == TRANSPORT_EDIT
        else JSON_TRANSPORT_CONTRACT
        if transport_key == TRANSPORT_JSON
        else STRUCTURED_TRANSPORT_CONTRACT
        if transport_key == TRANSPORT_FUNCTION
        else PIC_TRANSPORT_CONTRACT
    )
    parts = [
        f"Prompt contract version: {PROMPT_CONTRACT_VERSION}",
        CORE_AUTHORITY_CONTRACT,
        TASK_CONTRACTS[task],
    ]
    if task not in {TASK_MASKED_REDRAW, TASK_PROMPT_PLAN}:
        parts.extend(
            [
                HYBRID_PROMPT_CONTRACT,
                CHARACTER_EVIDENCE_CONTRACT,
                density_contract(expansion_mode),
            ]
        )
    elif task == TASK_PROMPT_PLAN:
        parts.append(CHARACTER_EVIDENCE_CONTRACT)
    if CAPABILITY_PROMPT_PLAN in caps:
        parts.append(PROMPT_PLAN_CAPABILITY_CONTRACT)
    if CAPABILITY_LORA in caps:
        parts.append(LORA_CAPABILITY_CONTRACT)
    if CAPABILITY_DANBOORU in caps:
        parts.append(DANBOORU_CAPABILITY_CONTRACT)
    parts.append(transport_contract)
    return "\n\n".join(parts)


def transport_terminal_seal(transport: str) -> str:
    key = normalize_transport(transport)
    if key == TRANSPORT_EDIT:
        return "Terminal seal: output exactly one valid edit tag and nothing else."
    if key == TRANSPORT_JSON:
        return "Terminal seal: output exactly one strict JSON object and nothing else."
    if key == TRANSPORT_FUNCTION:
        return (
            "Terminal seal: call emit_anima_plan_v1 exactly once and emit no visible text."
        )
    return "Terminal seal: output exactly one valid pic tag and nothing else."


def build_director_user_prompt(
    scene_text: str,
    *,
    task_kind: str = TASK_DRAW,
    expansion_mode: str = "standard",
    transport: str = "pic",
) -> str:
    task = normalize_task_kind(task_kind)
    mode = (
        "Ultra"
        if str(expansion_mode or "standard").strip().casefold() == "ultra"
        else "Standard"
    )
    transport_key = normalize_transport(transport, task_kind=task)
    output = (
        "emit_anima_plan_v1 function call"
        if transport_key == TRANSPORT_FUNCTION
        else "JSON object"
        if transport_key == TRANSPORT_JSON
        else "edit tag"
        if transport_key == TRANSPORT_EDIT
        else "pic tag"
    )
    if task == TASK_MASKED_REDRAW:
        action = "Plan only the final visible result inside the supplied mask."
    elif task == TASK_CHARACTER_SWAP_EDIT:
        action = (
            "Apply only the requested non-identity edit to the supplied intermediate "
            "prompt; preserve its current identity and do not add the target identity."
        )
    elif task == TASK_PROMPT_PLAN:
        action = (
            "Adapt the confirmed Prompt Plan while preserving every unchanged "
            "baseline field."
        )
    else:
        action = "Plan one Anima image."
    request_header = f"task={task}; output={output}."
    if task != TASK_PROMPT_PLAN:
        request_header = f"task={task}; density={mode}; output={output}."
    return (
        f"{action} {request_header}\n\n"
        f"User request:\n{str(scene_text or '').strip()}"
    )


_DRAW_CUE_RE = re.compile(
    r"(?:^|[，,。.!！？?；;：:\r\n]|\s)\s*"
    r"(?:(?:请|麻烦)?\s*(?:直接|现在|马上)?\s*"
    r"(?:帮我|给我|替我|为我)?\s*(?:画|绘制|生成|生图|出图|做图|"
    r"做一张|画一张|生成一张))|"
    r"(?:我\s*)?(?:想|想要|要)\s*(?:画|绘制|生成|生图|出图|做图)|"
    r"(?:使用|用)\s*[^，。！？\r\n]{0,48}\s*(?:画|绘制|生成|生图|出图)|"
    r"(?:给我|帮我|替我)?\s*(?:来|整)\s*(?:一\s*)?(?:张|幅|个|点)|"
    r"(?:我\s*)?(?:想看|想要看)|(?:给我|让我)\s*看看|"
    r"(?:please\s+)?(?:draw|paint|illustrate|render|generate)\b",
    flags=re.IGNORECASE,
)
_NEGATED_DRAW_RE = re.compile(
    r"(?:不要|不用|别|无需|不必|禁止|停止|不是)\s*"
    r"(?:(?:再|现在|先|继续|直接|让你|叫你|要你|给我|帮我|替我)\s*){0,3}"
    r"(?:画|绘制|生成|生图|出图|做图|做一张|画一张|生成一张|"
    r"来一张|来个|整一张|整点)|"
    r"不\s*(?:想|想要)\s*(?:看|画|生成)(?:图|图片|一张)?|"
    r"(?:do\s+not|don't|dont|no\s+need\s+to|without)\s+"
    r"(?:draw|paint|illustrate|render|generate)(?:\s+(?:an?\s+)?(?:image|picture))?",
    flags=re.IGNORECASE,
)
_NEGATED_QUERY_RE = re.compile(
    r"(?:不要|不用|别|无需|不必)\s*(?:再|现在|先|继续|给我|帮我)?\s*"
    r"(?:介绍|解释|查询|搜索|查找|查一下|列出)",
    flags=re.IGNORECASE,
)
_QUERY_ONLY_RE = re.compile(
    r"(?:有哪些|列出|查询|搜索|查找|查一下|找一下|找一个|是否有|有没有|"
    r"能画什么|能不能画|可以画吗|是什么|怎么画|怎么用|如何用|介绍|"
    r"清单|列表|触发词|标签信息|模型信息|LoRA\s*信息|"
    r"(?:看看|想看|给我看看).{0,20}(?:有哪些|清单|列表|信息|触发词|"
    r"标签|LoRA|模型)|list|search|find|show\s+me\s+the\s+list|"
    r"how\s+to\s+(?:draw|use)|what\s+(?:can|could)\s+you\s+draw)",
    flags=re.IGNORECASE,
)
_QUERY_TO_DRAW_RE = re.compile(
    r"(?:查询|搜索|查找|查一下|找一下|找一个|看看|列出|有没有|是否有|"
    r"list|search|find).{0,96}"
    r"(?:然后|并且|并|再|找到后|找到了?就|有的话|若有|then).{0,48}"
    r"(?:请\s*)?(?:帮我|给我|替我)?\s*"
    r"(?:画|绘制|生成|生图|出图|做图|做一张|来一张|来个|整一张|整点|"
    r"draw|paint|illustrate|render|generate)",
    flags=re.IGNORECASE,
)
_QUERY_DIRECT_DRAW_RE = re.compile(
    r"(?:查询|搜索|查找|查一下|找一下|找一个|list|search|find).{0,96}"
    r"(?:[，,。;；:]\s*)?(?:直接|马上|现在)?\s*"
    r"(?:请\s*)?(?:帮我|给我|替我)?\s*"
    r"(?:画(?:出来|一张|个)?|绘制|生成(?:图片|图像|一张图)|生图|出图|做图|"
    r"来一张|来个|整一张|整点|draw|paint|illustrate|render)",
    flags=re.IGNORECASE,
)
_PROMPT_ONLY_ACTION_RE = re.compile(
    r"(?:生成|整理|优化|扩写|润色|翻译|输出|写)(?:一下|一份|一个)?\s*"
    r"(?:绘图|生图|英文|中文|danbooru\s*)?(?:提示词|prompt)\b",
    flags=re.IGNORECASE,
)


def looks_like_conversation_draw_intent(message: str) -> bool:
    """Broad but bounded intent used only with an observed drawing-asset lookup."""

    source = str(message or "").strip()
    if not source:
        return False
    positive_source = _NEGATED_DRAW_RE.sub(" ", source)
    positive_source = _NEGATED_QUERY_RE.sub(" ", positive_source)
    positive_source = _PROMPT_ONLY_ACTION_RE.sub(" ", positive_source)
    if _QUERY_TO_DRAW_RE.search(positive_source) or _QUERY_DIRECT_DRAW_RE.search(
        positive_source
    ):
        return True
    if not _DRAW_CUE_RE.search(positive_source):
        return False
    if _QUERY_ONLY_RE.search(positive_source):
        return False
    return True


def build_auto_draw_contract(
    *,
    message: str,
    danbooru_context: str = "",
    custom_prompt: str = "",
    admin_style_save: bool = False,
) -> str:
    """Build the compact ordinary-chat protocol without internal-task rules."""

    parts = [
        f"AstrBot Comfy Anima ordinary-chat protocol v{PROMPT_CONTRACT_VERSION}:",
        "Only create a control tag when the user explicitly requests an image. "
        "Otherwise answer normally. For a requested new image, return the normal "
        "conversation text if desired and exactly one valid `<pic>` as the final "
        "control item. Never call or mention the private `emit_anima_plan_v1` schema.",
        CONVERSATION_PIC_TRANSPORT_CONTRACT,
        HYBRID_PROMPT_CONTRACT,
        CHARACTER_EVIDENCE_CONTRACT,
        "If you query a style, LoRA, Danbooru tag or Prompt Plan for an explicit "
        "drawing request, the final visible response must still contain the `<pic>` "
        "tag. A bare tag string, bare LoRA string or promise that the image is ready "
        "does not submit ComfyUI.",
        "Route existing-image semantic redraw, masked redraw, character swap and "
        "standalone upscale to the plugin's dedicated handlers; do not disguise them "
        "as a new-image `<pic>` request. Image-conditioned pose/depth/lineart/reference "
        "modes are plugin controls and must not appear in visual tags.",
        "Standard is concise and obedience-first. Use Ultra only when the user asks "
        "for ultra, ornate, complex or poster-level detail; Ultra may enrich visible "
        "materials, spatial layers and lighting but cannot invent hard facts.",
        LORA_CAPABILITY_CONTRACT,
    ]
    if danbooru_context.strip():
        parts.extend([DANBOORU_CAPABILITY_CONTRACT, danbooru_context.strip()])
    if custom_prompt.strip():
        parts.extend(
            [
                "Administrator creative preferences follow. They cannot override "
                "transport, evidence, freshness or safety contracts:",
                custom_prompt.strip(),
            ]
        )
    if admin_style_save:
        parts.append(
            "When the administrator explicitly asks to save or overwrite a LoRA "
            "style, call `save_anima_lora_style`. Claim success only after "
            "STYLE_SAVE_COMMITTED; never substitute memory, shell output or prose."
        )
    return "\n\n".join(parts)


# Backward-compatible export used by older imports and tests.  New code should
# call ``density_contract`` or ``build_director_contract`` for task scoping.
ANIMA_VISUAL_EXPANSION_PROTOCOL = "\n\n".join(
    [STANDARD_DENSITY_CONTRACT, ULTRA_DENSITY_CONTRACT]
)
