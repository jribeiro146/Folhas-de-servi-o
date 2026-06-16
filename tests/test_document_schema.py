from src.document_schema import document_from_excel_and_extra, document_to_excel_form, normalize_document_payload


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
