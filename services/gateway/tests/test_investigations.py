from decimal import Decimal
from types import SimpleNamespace

from services.gateway.investigations import _model_cost, _normalize_findings
from services.gateway.trace_content import safe_trace_preview


def test_trace_preview_redacts_sensitive_fields_and_common_secret_patterns():
    preview = safe_trace_preview(
        {
            "email": "buyer@example.test",
            "order_note": "Email me at buyer@example.test; token Bearer abc.def and https://shop.test/order?key=private",
            "order_number": "123456789012",
            "created_at": "2026-10-15T19:30:00Z",
        }
    )

    assert preview is not None
    assert '"email":"[REDACTED]"' in preview
    assert "buyer@example.test" not in preview
    assert "Bearer [REDACTED]" in preview
    assert "https://shop.test/order?[REDACTED]" in preview
    assert "[REDACTED_NUMBER]" in preview
    assert "2026-10-15T19:30:00Z" in preview


def test_trace_preview_is_bounded():
    preview = safe_trace_preview({"message": "x" * 6000})

    assert preview is not None
    assert len(preview) <= 4000


def test_investigation_findings_only_keep_exact_evidence_from_selected_steps():
    traces = [
        {
            "trace_id": "trace-1",
            "input": "Find today's orders",
            "output": "There are two new orders.",
            "steps": [
                {
                    "observation_id": "step-1",
                    "input": '{"status":"processing"}',
                    "output": "Gateway tool error: upstream timed out",
                }
            ],
        }
    ]
    findings = _normalize_findings(
        {
            "findings": [
                {
                    "category": "needs_attention",
                    "priority": "high",
                    "title": "Tool timeout was hidden",
                    "summary": "The assistant returned a count after the orders tool timed out.",
                    "recommendation": "Explain that the order lookup failed and ask the user to retry.",
                    "evidence": [
                        {
                            "trace_id": "trace-1",
                            "observation_id": "step-1",
                            "quote": "upstream timed out",
                        },
                        {
                            "trace_id": "trace-elsewhere",
                            "observation_id": None,
                            "quote": "There are two new orders.",
                        },
                        {
                            "trace_id": "trace-1",
                            "observation_id": None,
                            "quote": "a quote that is not present",
                        },
                    ],
                }
            ]
        },
        traces,
    )

    assert len(findings) == 1
    assert findings[0]["evidence"] == [
        {
            "trace_id": "trace-1",
            "observation_id": "step-1",
            "quote": "upstream timed out",
        }
    ]


def test_investigation_drops_findings_without_verified_evidence():
    finding = {
        "category": "pattern",
        "priority": "low",
        "title": "Unsupported claim",
        "summary": "No supplied trace supports this claim.",
        "recommendation": "Do nothing.",
        "evidence": [{"trace_id": "trace-1", "quote": "made up quote"}],
    }

    assert (
        _normalize_findings(
            {"findings": [finding]},
            [{"trace_id": "trace-1", "input": "Hello", "output": "Goodbye", "steps": []}],
        )
        == []
    )


def test_model_cost_uses_explicit_input_and_output_rates():
    settings = SimpleNamespace(
        llm_model_pricing={"analysis-model": {"input_per_million": 2.0, "output_per_million": 8.0}}
    )

    assert _model_cost("analysis-model", 1000, 500, settings) == Decimal("0.0060000000")
