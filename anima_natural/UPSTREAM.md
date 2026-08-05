# Upstream provenance

- Repository: https://github.com/yenn001/astrbot_plugin_comfy_anima
- Historical audit snapshot: `9220b1cbcb3026c14554331fdbccd7d08314cb35`
- V7 capability acceptance baseline: `8202024084c6115b41c2a012bf226c0c245f2c66`
- Snapshot imported on: 2026-08-04
- Authorization: the project owner confirmed direct source reuse was authorized.

## V7 boundary

The `upstream/` directory is a non-runtime historical audit and test-fixture
boundary. It preserves authorized algorithms, prompt references, manifests, and
workflow JSON for provenance and parity checks. V7 runtime modules must not import
from it.

Native V7 implementations live in `anima_studio/`, `anima_webui/`, and the
top-level `anima_natural/` modules. They provide the Provider, planner, prompt,
LoRA, Danbooru, workflow, queue, history, and Studio contracts without constructing
AstrBot Context/Event compatibility objects.

QQ, NapCat, OneBot, group permissions, command dispatch, message reply transport,
and the upstream duplicate WebUI are outside the V7 product boundary. The release
gate in `tools/check_v7_native.py` rejects upstream imports and transport markers in
active runtime code.
