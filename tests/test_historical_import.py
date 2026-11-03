from io import BytesIO

from openpyxl import Workbook

from whistapp.historical_import import normalize_workbook, validate


PLAYERS = ["Anne", "Bo", "Clara", "Dan"]


def workbook_bytes(sheets):
    workbook = Workbook()
    workbook.remove(workbook.active)
    for name, rows in sheets:
        sheet = workbook.create_sheet(name)
        for row in rows:
            sheet.append(row)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def structured_rows(hands):
    headers = ["Hånd", "Melder", "Melding", "Meldt", "Stik", "Makker"]
    for name in PLAYERS:
        headers.extend([f"{name} Δ", f"{name} total"])
    headers.extend(["Melder 2", "Melder 3"])
    rows = [headers]
    for number, bidder, bid, target, scores in hands:
        row = [number, bidder, bid, target, "", ""]
        for name in PLAYERS:
            row.extend([scores.get(name, ""), ""])
        row.extend(["", ""])
        rows.append(row)
    return rows


def test_structured_haender_accepts_decimal_values_and_optional_declarers():
    content = workbook_bytes(
        [
            (
                "Hænder",
                structured_rows(
                    [
                        (
                            1,
                            "Anne",
                            "almindelige",
                            "8",
                            {"Anne": 2, "Bo": 2, "Clara": -2, "Dan": -2},
                        ),
                        (
                            2,
                            "Anne",
                            "sol",
                            "",
                            {"Anne": "0.5?", "Bo": "-0.5", "Clara": "0", "Dan": "0"},
                        ),
                    ]
                ),
            )
        ]
    )

    data = normalize_workbook(content)

    assert data["source_kind"] == "structured"
    assert data["players"] == PLAYERS
    assert data["hands"][0]["scores"] == {
        "Anne": "2",
        "Bo": "2",
        "Clara": "-2",
        "Dan": "-2",
    }
    assert data["hands"][1]["scores"]["Anne"] == "0.5?"
    assert data["hands"][1]["scores"]["Bo"] == "-0.5"
    issues = validate(data)
    assert not issues[0]
    assert issues[1]
    assert data["source_sheets"][0]["name"] == "Hænder"


def test_raw_multisheet_transcription_preserves_uncertainty_and_arithmetic_warnings():
    headers = ["Hånd"]
    for name in PLAYERS:
        headers.extend([f"{name} Melding", f"{name} Δ", f"{name} Total"])
    page_one = [
        headers,
        [1, "Almindelige", "2", "999", "", "2", "2", "", "-2", "-2", "", "-2", "-2"],
        [2, "SOL?", "0.5?", "999", "", "-0.5", "-0.5", "", "0", "0", "", "0", "0"],
    ]
    page_two = [
        headers,
        [3, "VIP2", "2", "2", "", "-2", "-2", "", "2", "2", "", "-2", "-2"],
    ]
    content = workbook_bytes([("Ark 1", page_one), ("Ark 2", page_two)])

    data = normalize_workbook(content)

    assert data["source_kind"] == "raw_transcription"
    assert data["players"] == PLAYERS
    assert len(data["hands"]) == 3
    assert data["hands"][0]["bid"] == ""
    assert data["hands"][0]["source_notes"] == ["Anne: Almindelige"]
    assert data["hands"][1]["scores"]["Anne"] == "0.5?"
    assert any(
        "løbende total for Anne" in warning["message"]
        for warning in data["source_warnings"]
    )
    assert data["source_sheets"][0]["flags"][2][2] == "uncertain"
    assert any(issues for issues in validate(data))


def test_unbalanced_and_missing_structured_scores_reach_review_data():
    content = workbook_bytes(
        [
            (
                "Hænder",
                structured_rows(
                    [
                        (
                            1,
                            "Anne",
                            "almindelige",
                            "8",
                            {"Anne": "2.5", "Bo": "-2", "Clara": "", "Dan": "0"},
                        ),
                        (
                            2,
                            "Anne",
                            "almindelige",
                            "8",
                            {"Anne": "2.5", "Bo": "-2", "Clara": "0", "Dan": "0"},
                        ),
                    ]
                ),
            )
        ]
    )

    data = normalize_workbook(content)
    all_issues = validate(data)

    assert data["hands"][0]["scores"]["Anne"] == "2.5"
    assert data["hands"][0]["scores"]["Clara"] == ""
    assert any("Clara" in issue for issue in all_issues[0])
    assert any("summerer ikke til nul" in issue for issue in all_issues[1])
