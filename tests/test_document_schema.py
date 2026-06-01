from src.document_schema import normalize_document_payload


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
