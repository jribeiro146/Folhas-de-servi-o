from copy import deepcopy

import pytest

from src.document_schema import document_missing_required_fields, document_invalid_fields


def complete_document():
    return {
        "customer_name": "Cliente fictício",
        "customer_signer_name": "Maria Santos",
        "customer_signature_date": "2026-09-08",
        "technician_records": [{
            "technician": "Técnico fictício", "start_time": "09:00", "end_time": "10:30",
            "total_hours": "1 h 30 min", "date": "2026-09-08",
        }],
    }


def test_requires_technician_even_when_no_rows_are_provided():
    document = complete_document()
    document["technician_records"] = []
    assert document_missing_required_fields(document) == [
        "Nome do técnico 1", "Hora de início do técnico 1", "Hora de fim do técnico 1",
        "Total de horas do técnico 1", "Data do técnico 1",
    ]


@pytest.mark.parametrize(("key", "label"), [
    ("technician", "Nome"), ("start_time", "Hora de início"),
    ("end_time", "Hora de fim"), ("date", "Data"),
])
def test_every_used_technician_row_must_be_complete(key, label):
    document = complete_document()
    document["technician_records"].append(deepcopy(document["technician_records"][0]))
    document["technician_records"][1][key] = "  "
    assert document_missing_required_fields(document) == [f"{label} do técnico 2"]


def test_calculated_hours_and_unused_blank_rows_are_allowed():
    document = complete_document()
    document["technician_records"][0].pop("total_hours")
    document["technician_records"].append({"total_hours_overridden": False})
    assert document_missing_required_fields(document) == []
    assert document_invalid_fields(document) == []


@pytest.mark.parametrize("key,label", [
    ("customer_signer_name", "Primeiro e último nome"),
    ("customer_signature_date", "Data da assinatura"),
])
def test_signature_details_are_required_only_when_present(key, label):
    document = complete_document()
    document[key] = " "
    assert document_missing_required_fields(document) == [label]
    document["client_not_present"] = True
    assert document_missing_required_fields(document) == []
    document["technician_records"][0]["date"] = ""
    assert document_missing_required_fields(document) == ["Data do técnico 1"]


@pytest.mark.parametrize("name", ["Maria", " Maria  ", "Santos-Silva"])
def test_single_name_is_invalid(name):
    document = complete_document()
    document["customer_signer_name"] = name
    assert document_invalid_fields(document) == ["Primeiro e último nome"]


@pytest.mark.parametrize("name", ["Maria Santos", "  João   de Sá  ", "Ana-Maria D'Ávila"])
def test_first_and_last_name_allow_accents_and_compound_names(name):
    document = complete_document()
    document["customer_signer_name"] = name
    assert document_invalid_fields(document) == []


@pytest.mark.parametrize("key,value,label", [
    ("start_time", "25:00", "Hora de início"),
    ("end_time", "09:75", "Hora de fim"),
    ("date", "2026-02-30", "Data"),
])
def test_invalid_technician_dates_and_times_are_rejected(key, value, label):
    document = complete_document()
    document["technician_records"][0][key] = value
    assert document_invalid_fields(document) == [f"{label} do técnico 1"]


def test_invalid_signature_date_and_name_are_waived_for_absent_client():
    document = complete_document()
    document.update(customer_signer_name="Maria", customer_signature_date="2026-02-30")
    assert document_invalid_fields(document) == ["Primeiro e último nome", "Data da assinatura"]
    document["client_not_present"] = True
    assert document_invalid_fields(document) == []
