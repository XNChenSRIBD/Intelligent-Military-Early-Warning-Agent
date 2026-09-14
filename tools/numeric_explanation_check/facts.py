"""Pure arithmetic and declared metadata; no task-specific answer branches."""

from decimal import Decimal


def number(value):
    return Decimal(str(value))


def serialized(value):
    return float(value) if value is not None else None


def difference(before, after):
    delta = number(after) - number(before)
    percent = None if number(before) == 0 else delta / number(before) * 100
    return {
        "difference": serialized(delta),
        "relative_change_percent": serialized(percent),
        "relative_change_note": "分母为零，无法计算" if percent is None else None,
    }


def compute_facts(rows):
    """Derive only relationships supported by each row's declared fields."""
    facts = []
    for row in rows:
        kind = row["kind"]
        fact = {"input_row_ids": [row["row_id"]], "kind": kind}
        if kind == "bounds":
            fact.update(object=row["object"], window=row["window"], unit=row["unit"])
            if not row.get("comparison_allowed") or not row.get("unit"):
                fact.update(comparisons=[], comparison_note="未声明可比单位或比较许可")
            else:
                comparisons = []
                for bound in ("lower", "upper"):
                    reference = row.get(bound)
                    if reference is None:
                        continue
                    delta = number(row["value"]) - number(reference)
                    comparisons.append({
                        "boundary": bound, "reference": reference,
                        "value": row["value"], "difference": serialized(delta),
                        "relation": "低于" if delta < 0 else "高于" if delta > 0 else "等于",
                    })
                fact.update(comparisons=comparisons,
                            unprovided_boundaries=[b for b in ("lower", "upper") if row.get(b) is None])
        elif kind == "series":
            values = row["observations"]
            fact.update(field=row["field"], unit=row.get("unit"), adjacent_changes=[], final_vs_references=[])
            if row.get("comparison_allowed"):
                for previous, current in zip(values, values[1:]):
                    fact["adjacent_changes"].append({
                        "from": previous["label"], "to": current["label"],
                        "before": previous["value"], "after": current["value"],
                        **difference(previous["value"], current["value"]),
                    })
                for reference in row.get("references", []):
                    delta = number(values[-1]["value"]) - number(reference["value"])
                    fact["final_vs_references"].append({
                        "reference_label": reference["label"], "reference": reference["value"],
                        "final_value": values[-1]["value"], "difference": serialized(delta),
                        "relation": "低于" if delta < 0 else "高于" if delta > 0 else "等于",
                    })
        elif kind == "ratio":
            denominator = Decimal(1)
            for factor in row["denominator_factors"]:
                denominator *= number(factor)
            ratio = None if denominator == 0 else number(row["numerator"]) / denominator
            fact.update(numerator=row["numerator"], denominator=serialized(denominator),
                        ratio=serialized(ratio), percent=serialized(ratio * 100) if ratio is not None else None,
                        definition=row["definition"], window_seconds=row["window_seconds"],
                        epoch_count=row["epoch_count"], epoch_coverage_ratio=row["epoch_coverage_ratio"],
                        epoch_coverage_definition=row["epoch_coverage_definition"])
        else:
            # Summaries, definitions and missing fields remain declared data, not new observations.
            fact.update(origin="输入声明，非程序逐窗重算",
                        declared={key: value for key, value in row.items() if key not in {"row_id", "kind"}})
        facts.append(fact)
    return facts
