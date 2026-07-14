"""Schema and mapping helpers for the final service document."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any
import re
import unicodedata

from src.services.signature_service import (
    CLIENT_SIGNATURE_LABEL,
    TECHNICIAN_SIGNATURE_LABEL,
)

SERVICE_TYPE_OPTIONS: list[dict[str, str | None]] = [
    {"key": "piquete", "label": "Piquete", "excel_label": "PIQ"},
    {"key": "assistencia", "label": "Assistência", "excel_label": "ASSIST"},
    {"key": "manutencao", "label": "Manutenção", "excel_label": "MAN"},
    {"key": "formacao", "label": "Formação", "excel_label": "FORM"},
    {"key": "colocacao_servico", "label": "Colocação de serviço", "excel_label": "COL.SERV"},
    {"key": "reparacao_oficina", "label": "Reparação oficina", "excel_label": "REP.OF"},
    {"key": "garantia", "label": "Garantia", "excel_label": "GAR"},
    {"key": "instalacao", "label": "Instalação", "excel_label": "INST"},
]

EQUIPMENT_OPTIONS: list[dict[str, str | None]] = [
    {"key": "sadi", "label": "SADI", "excel_label": "SADI"},
    {"key": "vss", "label": "VSS", "excel_label": "CCTV"},
    {"key": "sadco", "label": "SADCO", "excel_label": None},
    {"key": "sadir", "label": "SADIR", "excel_label": None},
    {"key": "sadei", "label": "SADEI", "excel_label": None},
    {"key": "sca", "label": "SCA", "excel_label": "SCA"},
    {"key": "eas", "label": "EAS", "excel_label": "EAS"},
    {"key": "sadg", "label": "SADG", "excel_label": "SADG"},
    {"key": "sch", "label": "SCH", "excel_label": "SCH"},
    {"key": "other", "label": "OTHER", "excel_label": None},
]

TECHNICIAN_OPTIONS = [
    "João Freire",
    "Paulo Gomes",
    "Armando Correia",
    "Artur Carvalho",
    "Nuno Duarte",
    "Pedro Lopes",
    "Luciano Pereira",
    "Luis Henrique",
    "Valdecir Junior",
    "Luis Duarte",
    "José Califórnia",
]

DOCUMENT_REQUIRED_FIELDS: list[dict[str, str]] = [
    {"key": "customer_name", "label": "Cliente / Customer"},
]

DOCUMENT_SIMPLE_FIELDS = (
    "document_language",
    "service_number",
    "customer_name",
    "requested_by",
    "request_date",
    "customer_number",
    "nif_number",
    "vat_number",
    "customer_email",
    "customer_phone",
    "site_contact",
    "site_phone",
    "local_store",
    "contract_number",
    "address",
    "store_number",
    "requested_tasks",
    "intervention_report",
    "customer_signature_date",
)

DOCUMENT_MAX_MATERIALS = 12
DOCUMENT_MAX_TECHNICIANS = 4


def create_empty_material() -> dict[str, str]:
    return {
        "ref": "",
        "description": "",
        "qty": "",
    }


def create_empty_technician_record() -> dict[str, Any]:
    return {
        "technician": "",
        "start_time": "",
        "end_time": "",
        "total_hours": "",
        "total_hours_overridden": False,
        "date": "",
    }


def create_empty_document() -> dict[str, Any]:
    return {
        "document_language": "pt",
        "service_number": "",
        "customer_name": "",
        "requested_by": "",
        "request_date": "",
        "customer_number": "",
        "nif_number": "",
        "vat_number": "",
        "customer_email": "",
        "customer_phone": "",
        "site_contact": "",
        "site_phone": "",
        "local_store": "",
        "contract_number": "",
        "address": "",
        "store_number": "",
        "service_types": {option["key"]: False for option in SERVICE_TYPE_OPTIONS},
        "equipments": {option["key"]: False for option in EQUIPMENT_OPTIONS},
        "requested_tasks": "",
        "intervention_report": "",
        "materials_used": False,
        "materials": [create_empty_material()],
        "technician_records": [create_empty_technician_record()],
        "customer_signature_date": "",
        "client_not_present": False,
    }


def normalize_document_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    document = create_empty_document()
    source = payload if isinstance(payload, dict) else {}

    for key in DOCUMENT_SIMPLE_FIELDS:
        document[key] = _stringify(source.get(key))

    document["address"] = _normalize_address(document["address"])

    if document["document_language"] not in {"pt", "en"}:
        document["document_language"] = "pt"

    shared_tax_number = document["nif_number"] or document["vat_number"]
    if shared_tax_number:
        document["nif_number"] = shared_tax_number
        document["vat_number"] = shared_tax_number

    service_types = source.get("service_types")
    if isinstance(service_types, dict):
        for option in SERVICE_TYPE_OPTIONS:
            document["service_types"][option["key"]] = _coerce_bool(service_types.get(option["key"]))

    equipments = source.get("equipments")
    if isinstance(equipments, dict):
        for option in EQUIPMENT_OPTIONS:
            document["equipments"][option["key"]] = _coerce_bool(equipments.get(option["key"]))

        # Compatibilidade com rascunhos criados antes da correção da nomenclatura.
        if "sadco" not in equipments and "adco" in equipments:
            document["equipments"]["sadco"] = _coerce_bool(equipments.get("adco"))
        if "other" not in equipments and "ther" in equipments:
            document["equipments"]["other"] = _coerce_bool(equipments.get("ther"))

    materials = _normalize_material_rows(source.get("materials"))
    materials_used = (
        _coerce_bool(source.get("materials_used"))
        if "materials_used" in source
        else bool(materials)
    )
    document["materials_used"] = materials_used
    document["materials"] = materials if materials_used and materials else [create_empty_material()]

    technician_records = _normalize_technician_rows(source.get("technician_records"))
    document["technician_records"] = technician_records if technician_records else [create_empty_technician_record()]
    document["client_not_present"] = _coerce_bool(source.get("client_not_present"))

    return document


def document_from_excel_and_extra(
    excel_form_data: dict[str, Any] | None,
    extra_data: dict[str, Any] | None,
) -> dict[str, Any]:
    excel = excel_form_data or {}
    raw_extra = extra_data if isinstance(extra_data, dict) else {}
    document = normalize_document_payload(raw_extra)

    _prefer_excel_value(document, raw_extra, "service_number", excel.get("Folha nº"))
    _prefer_excel_value(document, raw_extra, "customer_name", excel.get("Cliente nome"))
    _prefer_excel_value(document, raw_extra, "requested_by", excel.get("Pedido por"))
    _prefer_excel_value(document, raw_extra, "request_date", _format_date_value(excel.get("Data pedido")))
    _prefer_excel_value(document, raw_extra, "customer_number", excel.get("Cliente nº"))
    _prefer_excel_value(document, raw_extra, "nif_number", excel.get("NIF") or excel.get("Ident"))
    _prefer_excel_value(document, raw_extra, "vat_number", excel.get("Ident") or excel.get("NIF"))
    _prefer_excel_value(document, raw_extra, "customer_email", excel.get("Email"))
    _prefer_excel_value(document, raw_extra, "customer_phone", excel.get("Telefone"))
    _prefer_excel_value(document, raw_extra, "site_contact", excel.get("Contacto"))
    _prefer_excel_value(document, raw_extra, "site_phone", excel.get("Telefone (2)"))
    _prefer_excel_value(document, raw_extra, "local_store", excel.get("Local"))
    _prefer_excel_value(document, raw_extra, "contract_number", excel.get("Contrato nº"))
    _prefer_excel_value(document, raw_extra, "address", _compose_address(excel))
    _prefer_excel_value(document, raw_extra, "store_number", excel.get("Loja nº"))
    _prefer_excel_value(
        document,
        raw_extra,
        "requested_tasks",
        excel.get("Avaria reportada") or excel.get("Descrição do trabalho executado"),
    )
    _prefer_excel_value(document, raw_extra, "intervention_report", excel.get("Relatório Técnico"))

    raw_service_types = raw_extra.get("service_types")
    for option in SERVICE_TYPE_OPTIONS:
        if not _has_explicit_flag(raw_service_types, option["key"]) and option["excel_label"]:
            document["service_types"][option["key"]] = _coerce_bool(excel.get(option["excel_label"]))

    raw_equipments = raw_extra.get("equipments")
    for option in EQUIPMENT_OPTIONS:
        if not _has_explicit_flag(raw_equipments, option["key"]) and option["excel_label"]:
            document["equipments"][option["key"]] = _coerce_bool(excel.get(option["excel_label"]))

    if not _has_meaningful_rows(raw_extra.get("technician_records")):
        derived_record = {
            "technician": _stringify(excel.get("Técnico(s)")),
            "start_time": "",
            "end_time": _stringify(excel.get("Fim")),
            "total_hours": _derive_total_hours_from_excel(excel),
            "total_hours_overridden": False,
            "date": _format_date_value(excel.get("Data serviço")),
        }
        if any(derived_record.values()):
            document["technician_records"] = [derived_record]

    shared_tax_number = document["nif_number"] or document["vat_number"]
    if shared_tax_number:
        document["nif_number"] = shared_tax_number
        document["vat_number"] = shared_tax_number

    return document


def document_to_excel_form(document_payload: dict[str, Any]) -> dict[str, Any]:
    document = normalize_document_payload(document_payload)
    shared_tax_number = document["nif_number"] or document["vat_number"]
    technician_records = [
        record
        for record in document["technician_records"]
        if any(
            _stringify(record.get(key))
            for key in ("technician", "start_time", "end_time", "total_hours", "date")
        )
    ]
    materials = [
        row
        for row in document["materials"]
        if any(_stringify(row.get(key)) for key in row)
    ]

    extra_equipment_labels = [
        option["label"]
        for option in EQUIPMENT_OPTIONS
        if option["excel_label"] is None and document["equipments"].get(option["key"])
    ]
    unique_technicians = list(dict.fromkeys(
        record["technician"] for record in technician_records if record.get("technician")
    ))
    first_record = technician_records[0] if technician_records else create_empty_technician_record()
    hours_total = _decimal_hours(first_record.get("total_hours"))
    hours_integer, minutes_integer = _split_decimal_hours(hours_total)

    address_street, address_postal_code = _split_address_parts(document["address"])
    address_cp_zone = _postal_zone(address_postal_code)

    excel_form = {
        "Pedido por": _or_none(document["requested_by"]),
        "Email": _or_none(document["customer_email"]),
        "Telefone": _or_none(document["customer_phone"]),
        "Contacto": _or_none(document["site_contact"]),
        "Telefone (2)": _or_none(document["site_phone"]),
        "Contrato nº": _or_none(document["contract_number"]),
        "Loja nº": _or_none(document["store_number"]),
        "Data pedido": _or_none(document["request_date"]),
        "Cliente nº": _or_none(document["customer_number"]),
        "Ident": _or_none(shared_tax_number),
        "Cliente nome": _or_none(document["customer_name"]),
        "Local": _or_none(document["local_store"]),
        "Morada": _or_none(address_street),
        "Cód. Postal": _or_none(address_postal_code),
        "CP": _or_none(address_cp_zone),
        "NIF": _or_none(shared_tax_number),
        "Avaria reportada": _or_none(document["requested_tasks"]),
        "Data serviço": _or_none(first_record.get("date")),
        "Relatório Técnico": _or_none(document["intervention_report"]),
        "Técnico(s)": _or_none(", ".join(unique_technicians)),
        "Fim": _or_none(first_record.get("end_time")),
        "T.Total trabalho": _or_none(_format_decimal_hours(hours_total)),
        "T.Total h": hours_integer if hours_total is not None else None,
        "T.Total m": minutes_integer if hours_total is not None else None,
        "Descrição do trabalho executado": _or_none(document["requested_tasks"]),
        "Observações": _or_none(
            _build_observations(
                document=document,
                materials=materials,
                technician_records=technician_records,
                extra_equipment_labels=extra_equipment_labels,
            )
        ),
    }

    for option in SERVICE_TYPE_OPTIONS:
        if option["excel_label"]:
            excel_form[option["excel_label"]] = document["service_types"].get(option["key"], False)

    for option in EQUIPMENT_OPTIONS:
        if option["excel_label"]:
            excel_form[option["excel_label"]] = document["equipments"].get(option["key"], False)

    return excel_form


def document_missing_required_fields(document_payload: dict[str, Any]) -> list[str]:
    document = normalize_document_payload(document_payload)
    missing: list[str] = []

    for field in DOCUMENT_REQUIRED_FIELDS:
        if not _stringify(document.get(field["key"])):
            missing.append(field["label"])

    return missing


def document_invalid_fields(document_payload: dict[str, Any]) -> list[str]:
    document = normalize_document_payload(document_payload)
    invalid: list[str] = []

    for index, record in enumerate(document["technician_records"], start=1):
        total_hours = _stringify(record.get("total_hours"))
        if total_hours and _decimal_hours(total_hours) is None:
            invalid.append(f"Total de horas do técnico {index}")

    return invalid


def strip_signature_payload(document_payload: dict[str, Any]) -> dict[str, Any]:
    stripped = deepcopy(document_payload if isinstance(document_payload, dict) else {})
    stripped.pop(CLIENT_SIGNATURE_LABEL, None)
    stripped.pop(TECHNICIAN_SIGNATURE_LABEL, None)
    return stripped


def get_primary_technician_name(document_payload: dict[str, Any] | None) -> str:
    document = normalize_document_payload(document_payload)
    for record in document["technician_records"]:
        technician = _stringify(record.get("technician"))
        if technician:
            return technician
    return ""


def get_technician_initials(technician_name: str | None) -> str:
    raw_name = _stringify(technician_name)
    if not raw_name:
        return ""

    normalized_name = unicodedata.normalize("NFKD", raw_name).encode("ascii", "ignore").decode("ascii")
    name_parts = [part for part in normalized_name.replace("-", " ").split() if part]
    if not name_parts:
        return ""

    first_letter = next((char for char in name_parts[0] if char.isalpha()), "")
    last_part = name_parts[-1] if len(name_parts) > 1 else name_parts[0]
    last_letter = next((char for char in last_part if char.isalpha()), "")

    if len(name_parts) == 1:
        return first_letter.upper()

    return f"{first_letter}{last_letter}".upper()


def _normalize_material_rows(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []

    rows: list[dict[str, str]] = []
    for row in value[:DOCUMENT_MAX_MATERIALS]:
        if not isinstance(row, dict):
            continue

        normalized = {
            "ref": _stringify(row.get("ref")),
            "description": _stringify(row.get("description")),
            "qty": _normalize_quantity(row.get("qty")),
        }
        if any(normalized.values()):
            rows.append(normalized)

    return rows


def _normalize_technician_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []

    rows: list[dict[str, Any]] = []
    for row in value[:DOCUMENT_MAX_TECHNICIANS]:
        if not isinstance(row, dict):
            continue

        start_time = _normalize_time_value(row.get("start_time"))
        end_time = _normalize_time_value(row.get("end_time"))
        calculated_total = _calculate_total_hours(start_time, end_time)
        raw_total_hours = _stringify(row.get("total_hours"))
        total_hours = (
            _normalize_total_hours_value(raw_total_hours)
            if raw_total_hours
            else calculated_total
        )
        if "total_hours_overridden" in row:
            total_hours_overridden = _coerce_bool(row.get("total_hours_overridden"))
        else:
            total_hours_overridden = bool(
                raw_total_hours
                and (not calculated_total or total_hours != calculated_total)
            )

        normalized = {
            "technician": _stringify(row.get("technician")),
            "start_time": start_time,
            "end_time": end_time,
            "total_hours": total_hours,
            "total_hours_overridden": total_hours_overridden,
            "date": _normalize_date_value(row.get("date")),
        }
        if any(
            _stringify(normalized.get(key))
            for key in ("technician", "start_time", "end_time", "total_hours", "date")
        ):
            rows.append(normalized)

    return rows


def _build_observations(
    *,
    document: dict[str, Any],
    materials: list[dict[str, str]],
    technician_records: list[dict[str, str]],
    extra_equipment_labels: list[str],
) -> str:
    sections: list[str] = []

    if extra_equipment_labels:
        sections.append("Equipamentos extra: " + ", ".join(extra_equipment_labels))

    if document.get("client_not_present"):
        sections.append("Cliente não presente na obra — assinatura dispensada.")

    if materials:
        material_lines = [
            f"{row['ref'] or '-'} | {row['description'] or '-'} | Qtd {row['qty'] or '-'}"
            for row in materials
        ]
        sections.append("Materiais:\n" + "\n".join(material_lines))

    if technician_records:
        record_lines = []
        for record in technician_records:
            parts = [record.get("technician") or "-"]
            if record.get("date"):
                parts.append(record["date"])
            if record.get("start_time") or record.get("end_time"):
                parts.append(f"{record.get('start_time') or '--:--'}-{record.get('end_time') or '--:--'}")
            if record.get("total_hours"):
                parts.append(record["total_hours"])
            record_lines.append(" | ".join(parts))

        sections.append("Registos técnicos:\n" + "\n".join(record_lines))

    return "\n\n".join(section for section in sections if section.strip())


def _derive_total_hours_from_excel(excel_form_data: dict[str, Any]) -> str:
    total_hours = _decimal_hours(excel_form_data.get("T.Total trabalho"))
    if total_hours is None:
        hours = _stringify(excel_form_data.get("T.Total h"))
        minutes = _stringify(excel_form_data.get("T.Total m"))
        if hours or minutes:
            try:
                total_hours = Decimal(hours or "0") + (Decimal(minutes or "0") / Decimal("60"))
            except InvalidOperation:
                total_hours = None

    return _format_duration_hours(total_hours)


def _split_decimal_hours(value: Decimal | None) -> tuple[int | None, int | None]:
    if value is None:
        return None, None

    total_minutes = int((value * Decimal("60")).to_integral_value())
    hours = total_minutes // 60
    minutes = total_minutes % 60
    return hours, minutes


def _decimal_hours(value: Any) -> Decimal | None:
    total_minutes = _duration_minutes(value)
    if total_minutes is not None:
        return Decimal(total_minutes) / Decimal("60")

    raw = _stringify(value).replace(",", ".")
    if not raw:
        return None

    try:
        decimal_hours = Decimal(raw)
    except InvalidOperation:
        return None
    return decimal_hours if decimal_hours >= 0 else None


def _format_decimal_hours(value: Decimal | None) -> str:
    if value is None:
        return ""

    normalized = value.quantize(Decimal("0.01"))
    text = format(normalized, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _normalize_total_hours_value(value: Any) -> str:
    text = _stringify(value)
    if not text:
        return ""

    decimal_hours = _decimal_hours(text)
    if decimal_hours is None:
        return text

    return _format_duration_hours(decimal_hours)


def _duration_minutes(value: Any) -> int | None:
    raw = _stringify(value).strip().lower().replace(",", ".")
    if not raw:
        return None

    if ":" in raw:
        hours_text, minutes_text, *_ = raw.split(":") + [""]
        try:
            hours = int(hours_text or "0")
            minutes = int(minutes_text or "0")
        except ValueError:
            return None
        if hours < 0 or minutes < 0 or minutes >= 60:
            return None
        return (hours * 60) + minutes

    hour_match = re.search(r"(\d+(?:\.\d+)?)\s*h", raw)
    minute_match = re.search(r"(\d+)\s*(?:m|min)", raw)
    if hour_match or minute_match:
        hours = Decimal(hour_match.group(1)) if hour_match else Decimal("0")
        minutes = int(minute_match.group(1)) if minute_match else 0
        if hour_match and minutes >= 60:
            return None
        return int((hours * Decimal("60")).to_integral_value()) + minutes

    return None


def _format_duration_hours(value: Decimal | None) -> str:
    if value is None:
        return ""

    minutes_total = int((value * Decimal("60")).to_integral_value())
    hours = minutes_total // 60
    minutes = minutes_total % 60
    if hours and minutes:
        return f"{hours} h {minutes:02d} min"
    if hours:
        return f"{hours} h"
    return f"{minutes} min"


def _calculate_total_hours(start_time: str, end_time: str) -> str:
    start = _parse_time(start_time)
    end = _parse_time(end_time)
    if start is None or end is None:
        return ""

    start_minutes = start.hour * 60 + start.minute
    end_minutes = end.hour * 60 + end.minute
    if end_minutes < start_minutes:
        end_minutes += 24 * 60

    total = Decimal(end_minutes - start_minutes) / Decimal("60")
    return _format_duration_hours(total)


def _parse_time(value: str) -> time | None:
    text = _normalize_time_value(value)
    if not text:
        return None

    try:
        return datetime.strptime(text, "%H:%M").time()
    except ValueError:
        return None


def _normalize_time_value(value: Any) -> str:
    text = _stringify(value)
    if not text:
        return ""

    if len(text) == 5 and text[2] == ":":
        return text

    return text


def _normalize_date_value(value: Any) -> str:
    return _format_date_value(value)


def _format_date_value(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return _stringify(value)


POSTAL_CODE_RE = re.compile(r"\b\d{4}-\d{3}(?:\s+[^,;|]+)?")


def _compose_address(excel_form_data: dict[str, Any]) -> str:
    street, embedded_postal_code = _split_address_parts(excel_form_data.get("Morada"))
    _, explicit_postal_code = _split_address_parts(excel_form_data.get("Cód. Postal"))
    postal_code = explicit_postal_code or embedded_postal_code

    parts = []
    for part in (street, postal_code):
        normalized = _stringify(part)
        if normalized and normalized not in parts:
            parts.append(normalized)

    return ", ".join(parts)


def _normalize_address(value: Any) -> str:
    street, postal_code = _split_address_parts(value)
    return ", ".join(part for part in (street, postal_code) if part)


def _split_address_parts(value: Any) -> tuple[str, str]:
    text = _stringify(value)
    if not text:
        return "", ""

    matches = list(POSTAL_CODE_RE.finditer(text))
    if not matches:
        return _clean_address_text(text), ""

    postal_code = matches[0].group(0).strip(" ,;|")
    text_without_cp_suffix = text
    cp_zone = _postal_zone(postal_code)
    if cp_zone:
        text_without_cp_suffix = re.sub(
            rf"({POSTAL_CODE_RE.pattern})(?:\s*[,;|]\s*{re.escape(cp_zone)}\b)+",
            r"\1",
            text_without_cp_suffix,
        )

    street = POSTAL_CODE_RE.sub(" ", text_without_cp_suffix)
    street = _clean_address_text(street)
    return street, postal_code


def _clean_address_text(value: str) -> str:
    text = re.sub(r"\s+", " ", _stringify(value))
    text = re.sub(r"\s*[,;|]\s*", ", ", text)
    text = re.sub(r"(?:,\s*){2,}", ", ", text)
    return text.strip(" ,;|")


def _postal_zone(postal_code: str) -> str:
    match = re.search(r"\d{2}", _stringify(postal_code))
    return match.group(0) if match else ""


def _normalize_quantity(value: Any) -> str:
    text = _stringify(value)
    if not text:
        return ""

    try:
        decimal_value = Decimal(text.replace(",", "."))
    except InvalidOperation:
        return text

    if decimal_value == decimal_value.to_integral():
        return str(int(decimal_value))

    return format(decimal_value.normalize(), "f").rstrip("0").rstrip(".")


def _prefer_excel_value(
    document: dict[str, Any],
    raw_extra: dict[str, Any],
    key: str,
    excel_value: Any,
) -> None:
    if _stringify(raw_extra.get(key)):
        return

    document[key] = _stringify(excel_value)


def _has_explicit_flag(value: Any, key: str) -> bool:
    return isinstance(value, dict) and key in value


def _has_meaningful_rows(value: Any) -> bool:
    if not isinstance(value, list):
        return False

    for row in value:
        if isinstance(row, dict) and any(
            _stringify(cell)
            for key, cell in row.items()
            if key != "total_hours_overridden"
        ):
            return True
    return False


def _or_none(value: Any) -> Any:
    text = _stringify(value)
    return text or None


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0

    text = _stringify(value).strip().lower()
    return text in {"1", "true", "x", "yes", "sim", "on"}


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()
