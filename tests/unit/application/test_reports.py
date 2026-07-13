"""Tests for report renderers."""

from docx import Document

from leggie.application.services.reports import Report


def test_report_to_docx_renders_bold_and_italic(tmp_path):
    """DOCX output preserves basic Markdown bold/italic formatting."""
    report = Report(
        title="Test Report",
        report_type="test",
        metadata={},
        sections=[
            {
                "level": 2,
                "title": "Formatted section",
                "content": "This is **bold** and this is _italic_ text.",
            },
            {
                "level": 2,
                "title": "Bullets",
                "content": [
                    "- **bold** bullet",
                    "- _italic_ bullet",
                    "- plain bullet",
                ],
            },
        ],
    )

    path = tmp_path / "report.docx"
    result = report.to_docx(path)

    assert result == path
    assert path.exists()

    doc = Document(str(path))
    bold_runs = [run.text for p in doc.paragraphs for run in p.runs if run.bold]
    italic_runs = [run.text for p in doc.paragraphs for run in p.runs if run.italic]

    assert "bold" in bold_runs
    assert "italic" in italic_runs
