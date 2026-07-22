from pathlib import Path

from anima_webui.workflow import prepare_templates, read_json, write_json


APP_DIR = Path(__file__).resolve().parent
SOURCE_DIR = APP_DIR / "sources"


def main() -> None:
    api_source = read_json(SOURCE_DIR / "AnimaBasicV7 (1).json")
    ui_source = read_json(SOURCE_DIR / "AnimaBasicV7 (3).json")
    api_template, ui_template = prepare_templates(api_source, ui_source)
    write_json(APP_DIR / "templates" / "workflow_api.json", api_template)
    write_json(APP_DIR / "templates" / "workflow_ui.json", ui_template)
    write_json(APP_DIR / "AnimaBasicV7-Random-WebUI.json", ui_template)


if __name__ == "__main__":
    main()
