import importlib.util
import unittest
from datetime import date
from pathlib import Path

from daily_procurements import ProcurementSummaryItem, build_daily_summary_html_document, build_daily_summary_message


ROOT = Path(__file__).resolve().parents[3]
OLD_MODULE = ROOT / "who-is-the-winner" / "daily_procurements.py"


def _load_old_module():
    spec = importlib.util.spec_from_file_location("old_daily_procurements", OLD_MODULE)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class DailySummaryRegressionTests(unittest.TestCase):
    def test_message_matches_old_logic_empty(self):
        old = _load_old_module()
        target_date = date(2026, 6, 19)
        self.assertEqual(
            build_daily_summary_message([], target_date),
            old.build_daily_summary_message([], target_date),
        )

    def test_html_matches_old_logic_empty(self):
        old = _load_old_module()
        target_date = date(2026, 6, 19)
        self.assertEqual(
            build_daily_summary_html_document([], target_date),
            old.build_daily_summary_html_document([], target_date),
        )

    def test_message_matches_old_logic_with_items(self):
        old = _load_old_module()
        target_date = date(2026, 6, 19)
        items = [
            ProcurementSummaryItem(
                reference="123",
                title="Sample procurement",
                category="Travaux",
                estimated_price=120000.0,
                caution_amount=5000.0,
                has_documents=True,
                location="Safi",
                due_date="20/06/2026 10:00",
                published_date="19/06/2026",
                consultation_url="https://example.com/consultation",
            )
        ]
        self.assertEqual(
            build_daily_summary_message(items, target_date),
            old.build_daily_summary_message(items, target_date),
        )

    def test_html_matches_old_logic_with_items(self):
        old = _load_old_module()
        target_date = date(2026, 6, 19)
        items = [
            ProcurementSummaryItem(
                reference="123",
                title="Sample procurement",
                category="Travaux",
                estimated_price=120000.0,
                caution_amount=5000.0,
                has_documents=True,
                location="Safi",
                due_date="20/06/2026 10:00",
                published_date="19/06/2026",
                consultation_url="https://example.com/consultation",
            )
        ]
        self.assertEqual(
            build_daily_summary_html_document(items, target_date),
            old.build_daily_summary_html_document(items, target_date),
        )


if __name__ == "__main__":
    unittest.main()
