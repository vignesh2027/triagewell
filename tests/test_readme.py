import re
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_readme_feature_table_matches_the_code():
    module = runpy.run_path(str(ROOT / "dev" / "feature_table.py"))
    readme = (ROOT / "README.md").read_text()
    assert module["table"]() in readme, "README table is stale: run `uv run python dev/feature_table.py --write`"


def test_docs_have_no_em_dashes_or_arrows():
    for path in [ROOT / "README.md", *ROOT.glob("*.md")]:
        text = re.sub(r"<!--.*?-->", "", path.read_text(), flags=re.DOTALL)
        assert "—" not in text, f"em dash in {path.name}"
        assert "->" not in text and "→" not in text, f"arrow in {path.name}"
