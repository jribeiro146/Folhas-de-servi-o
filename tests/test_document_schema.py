import pytest

from src.document_schema import (
    EQUIPMENT_OPTIONS,
    TECHNICIAN_OPTIONS,
    document_from_excel_and_extra,
    document_invalid_fields,
    document_to_excel_form,
    get_technician_initials,
    normalize_document_payload,
    normalize_work_number,
)


def test_technician_options_use_luis_california():
    assert "Luís Califórnia" in TECHNICIAN_OPTIONS
    assert "José Califórnia" not in TECHNICIAN_OPTIONS


@pytest.mark.parametrize(
    ("technician", "expected_initials"),
    [
        ("Armando Correia", "ARC"),
        ("Artur Carvalho", "AC"),
    ],
)
def test_technician_initials_distinguish_armando_from_artur(
    technician,
    expected_initials,
):
    assert get_technician_initials(technician) == expected_initials


def test_normalize_document_payload_keeps_customer_signer_name():
    document = normalize_document_payload(
        {
            "customer_signer_name": "Maria Santos",
            "customer_signature_date": "2026-08-06",
        }
    )

    assert document["customer_signer_name"] == "Maria Santos"
    assert document["customer_signature_date"] == "2026-08-06"


def test_legacy_work_number_is_persisted_without_excel_mapping():
    document = document_from_excel_and_extra({}, {"work_number": "42"})
    excel_form = document_to_excel_form(document)

    assert document["work_number"] == "0042"
    assert "N.º de obra" not in excel_form


@pytest.mark.parametrize("value", [22, 22.0, "22", "0022"])
def test_normalize_work_number_uses_exactly_four_digits(value):
    assert normalize_work_number(value) == "0022"


@pytest.mark.parametrize(
    "value",
    [None, "", "22A", "12345", "٠٠٢٢", -22, -22.0, "-22", 1.5, True],
)
def test_normalize_work_number_rejects_invalid_values(value):
    assert normalize_work_number(value) == ""


def test_excel_work_number_has_priority_over_legacy_json():
    document = document_from_excel_and_extra(
        {"N.º de obra": 22},
        {"work_number": "0036"},
    )
    assert document["work_number"] == "0022"


def test_legacy_json_is_used_when_excel_work_number_is_empty():
    document = document_from_excel_and_extra(
        {"N.º de obra": ""},
        {"work_number": "36"},
    )
    assert document["work_number"] == "0036"


def test_invalid_non_empty_excel_work_number_disables_legacy_link():
    document = document_from_excel_and_extra(
        {"N.º de obra": "OBRA 22"},
        {"work_number": "0036"},
    )
    assert document["work_number"] == ""


def test_normalize_document_payload_keeps_four_technicians():
    document = normalize_document_payload(
        {
            "technician_records": [
                {"technician": "Tecnico 1"},
                {"technician": "Tecnico 2"},
                {"technician": "Tecnico 3"},
                {"technician": "Tecnico 4"},
                {"technician": "Tecnico 5"},
            ]
        }
    )

    assert [row["technician"] for row in document["technician_records"]] == [
        "Tecnico 1",
        "Tecnico 2",
        "Tecnico 3",
        "Tecnico 4",
    ]


def test_address_composition_does_not_duplicate_cp_zone():
    document = document_from_excel_and_extra(
        {
            "Morada": "R. Mario Botas - Parque das Nacoes",
            "Cód. Postal": "1998-018 Lisboa",
            "CP": "19",
        },
        {},
    )

    assert document["address"] == "R. Mario Botas - Parque das Nacoes, 1998-018 Lisboa"


def test_address_composition_removes_repeated_postal_code_and_cp_zone():
    document = document_from_excel_and_extra(
        {
            "Morada": "R. Mario Botas - Parque das Nacoes, 1998-018 Lisboa, 19, 1998-018 Lisboa, 19",
            "Cód. Postal": "1998-018 Lisboa",
            "CP": "19",
        },
        {},
    )

    assert document["address"] == "R. Mario Botas - Parque das Nacoes, 1998-018 Lisboa"


def test_normalize_document_payload_removes_repeated_postal_code_from_saved_address():
    document = normalize_document_payload(
        {
            "address": "R. Mario Botas - Parque das Nacoes, 1998-018 Lisboa, 19, 1998-018 Lisboa, 19",
        }
    )

    assert document["address"] == "R. Mario Botas - Parque das Nacoes, 1998-018 Lisboa"


def test_document_to_excel_form_splits_address_parts():
    excel_form = document_to_excel_form(
        {
            "address": "Av. Sanches de Miranda, 30, 7006-805 Evora",
        }
    )

    assert excel_form["Morada"] == "Av. Sanches de Miranda, 30"
    assert excel_form["Cód. Postal"] == "7006-805 Evora"
    assert excel_form["CP"] == "70"


def test_equipment_options_match_approved_names_and_order():
    assert [option["label"] for option in EQUIPMENT_OPTIONS] == [
        "SADI",
        "VSS",
        "SADCO",
        "SADIR",
        "SADEI",
        "SCA",
        "EAS",
        "SADG",
        "SCH",
        "OTHER",
    ]


def test_legacy_equipment_keys_are_migrated_without_losing_selection():
    document = normalize_document_payload(
        {"equipments": {"adco": True, "ther": True}}
    )

    assert document["equipments"]["sadco"] is True
    assert document["equipments"]["other"] is True
    assert "adco" not in document["equipments"]
    assert "ther" not in document["equipments"]


def test_legacy_excel_system_columns_map_to_approved_names_bidirectionally():
    legacy_columns = {
        "SADI": True,
        "CCTV": True,
        "PA/VA": True,
        "SAI": True,
        "EXT": True,
        "SCA": True,
        "EAS": True,
        "SADG": True,
        "SCH": True,
        "OTHER": True,
    }

    document = document_from_excel_and_extra(legacy_columns, {})
    excel_form = document_to_excel_form(document)

    assert document["equipments"] == {
        "sadi": True,
        "vss": True,
        "sadco": True,
        "sadir": True,
        "sadei": True,
        "sca": True,
        "eas": True,
        "sadg": True,
        "sch": True,
        "other": True,
    }
    assert {column: excel_form[column] for column in legacy_columns} == legacy_columns


def test_materials_used_is_derived_for_old_documents_and_explicit_false_clears_rows():
    legacy = normalize_document_payload(
        {"materials": [{"ref": "MAT-1", "description": "Bateria", "qty": "1"}]}
    )
    disabled = normalize_document_payload(
        {
            "materials_used": False,
            "materials": [{"ref": "MAT-1", "description": "Bateria", "qty": "1"}],
        }
    )

    assert legacy["materials_used"] is True
    assert legacy["materials"][0]["ref"] == "MAT-1"
    assert disabled["materials_used"] is False
    assert disabled["materials"] == [{"ref": "", "description": "", "qty": ""}]


def test_total_hours_defaults_to_calculation_but_preserves_manual_override():
    automatic = normalize_document_payload(
        {
            "technician_records": [
                {"technician": "João Freire", "start_time": "09:00", "end_time": "18:00"}
            ]
        }
    )
    adjusted = normalize_document_payload(
        {
            "technician_records": [
                {
                    "technician": "João Freire",
                    "start_time": "09:00",
                    "end_time": "18:00",
                    "total_hours": "8",
                }
            ]
        }
    )
    excel_form = document_to_excel_form(adjusted)

    assert automatic["technician_records"][0]["total_hours"] == "9 h"
    assert automatic["technician_records"][0]["total_hours_overridden"] is False
    assert adjusted["technician_records"][0]["total_hours"] == "8 h"
    assert adjusted["technician_records"][0]["total_hours_overridden"] is True
    assert excel_form["T.Total trabalho"] == "8"
    assert excel_form["T.Total h"] == 8
    assert excel_form["T.Total m"] == 0


def test_invalid_manual_total_is_reported():
    for invalid_value in ("oito horas", "8:75", "-2"):
        assert document_invalid_fields(
            {
                "technician_records": [
                    {"technician": "Técnico", "total_hours": invalid_value}
                ]
            }
        ) == ["Total de horas do técnico 1"]


def test_client_absence_is_persisted_in_excel_observations():
    document = normalize_document_payload({"client_not_present": True})
    excel_form = document_to_excel_form(document)

    assert document["client_not_present"] is True
    assert "Cliente não presente na obra" in excel_form["Observações"]
