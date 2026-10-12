"""Regression test for the LlmExtractionResult -> ExpenseRecord conversion
used in telegram/handlers.py::_extract_and_confirm. A previous version
passed LlmExpenseItem instances straight into ExpenseRecord(items=...),
which pydantic rejects because the two are unrelated sibling models."""

from __future__ import annotations

from expensebot.llm.schema import LlmExpenseItem, LlmExtractionResult
from expensebot.models import ExpenseItem, ExpenseRecord


def test_llm_items_convert_cleanly_into_expense_record():
    # Arrange
    llm_result = LlmExtractionResult(
        valid=True,
        merchant="Warung Bu Siti",
        kategori="Makan & Minum",
        items=[
            LlmExpenseItem(
                nama="Nasi Goreng", qty=2, harga_satuan=20000, subtotal=40000
            )
        ],
        total=40000,
    )

    # Act — mirrors the conversion in telegram/handlers.py::_extract_and_confirm
    record = ExpenseRecord(
        tanggal=llm_result.tanggal,
        merchant=llm_result.merchant,
        kategori=llm_result.kategori,
        items=[ExpenseItem(**item.model_dump()) for item in llm_result.items],
        total=llm_result.total,
    )

    # Assert
    assert record.items[0].nama == "Nasi Goreng"
    assert record.items[0].subtotal == 40000
