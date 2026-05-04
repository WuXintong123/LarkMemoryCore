from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CONSOLE_ROOT = REPO_ROOT / "competition" / "feishu_office" / "demo_console"


def test_demo_console_does_not_render_assistant_outputs_or_browser_api_key():
    source_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (CONSOLE_ROOT / "src").glob("*.tsx")
    )
    assert 'samples"' not in source_text
    assert 'output"' not in source_text
    assert "response.output" not in source_text
    assert "LARK_MEMORY_CORE_API_KEY" not in source_text


def test_demo_console_documents_linear_design_source():
    readme = (CONSOLE_ROOT / "README.md").read_text(encoding="utf-8")
    assert "awesome-design-md/design-md/linear.app/DESIGN.md" in readme
    assert "does not render assistant answer bodies" in readme
