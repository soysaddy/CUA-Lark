import json
from datetime import datetime
from pathlib import Path

from utils.privacy import safe_filename


class ReportGenerator:
    def __init__(self, base_dir: str = "runs") -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def generate(self, trace) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_name = safe_filename(f"{trace.task_name}_{timestamp}") + ".html"
        report_path = self.base_dir / report_name
        rows = "".join(
            f"<tr><td>{step.state_name}</td><td>{step.status}</td><td>{step.duration:.2f}s</td><td>{step.retry_count}</td></tr>"
            for step in trace.steps
        )
        html = f"""
        <html>
        <head><meta charset="utf-8"><title>CUA-Lark Report</title></head>
        <body>
        <h1>{trace.task_name}</h1>
        <p>success: {trace.success}</p>
        <pre>{json.dumps(trace.params, ensure_ascii=False, indent=2)}</pre>
        <table border="1" cellspacing="0" cellpadding="6">
            <tr><th>state</th><th>status</th><th>duration</th><th>retry</th></tr>
            {rows}
        </table>
        <p>error: {trace.error}</p>
        </body>
        </html>
        """
        report_path.write_text(html, encoding="utf-8")
        return str(report_path)
