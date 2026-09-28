"""Versioned SADI definition, extracted from SADI.xlsx, sheet 'b) SADI'.

Peripheral checks form one permanent section per site, after repeaters.
This module has no storage, network or operational configuration dependencies.
"""
from copy import deepcopy
from datetime import date
from functools import lru_cache
import base64
import hashlib
import hmac
import io
import json
import re

from PIL import Image


VERSION = "sadi-2"
GROUPS = {"conventional": "Central convencional", "addressable": "Central endereçável", "repeater": "Repetidor"}
GENERAL = [
    ("B22", "Verificação de que não existiram alterações ao projeto e/ou Medidas de Autoproteção."),
    ("B23", "Verificação dos eventos registados nos registos das Medidas de Autoproteção."),
]
CONVENTIONAL = [
    ("B44", "Verificar funcionamento geral incluindo teclado e chaves"),
    ("B45", "Verificar funcionalidade e nível de luminosidade dos leds de falha e/ou alarme"),
    ("B46", "Verificar estado da alimentação (230 v / 50 Hz)"),
    ("B47", "Medição da carga e validade das baterias, verificação da tensão de entrada/saída, limpeza e reaperto de bornes"),
    ("B48", "Confirmação de ligação entre Central Principal e PC de operacionalização quando exista"),
    ("B49", "Verificação da acessibilidade e existência de sinalética da(s) central(ais)"),
]
ADDRESSABLE = [
    ("B68", CONVENTIONAL[0][1]), ("B69", CONVENTIONAL[1][1]),
    ("B70", CONVENTIONAL[2][1]), ("B71", CONVENTIONAL[3][1]),
    ("B72", "Confirmação de ligação entre centrais (quando em rede) e repetidores"),
    ("B73", CONVENTIONAL[4][1]), ("B74", CONVENTIONAL[5][1]),
]
REPEATER = [
    ("B90", CONVENTIONAL[0][1]), ("B91", CONVENTIONAL[1][1]),
    ("B92", CONVENTIONAL[3][1]),
    ("B93", "Verificação da acessibilidade e existência de sinalética dos repetidores"),
]
PERIPHERALS = [
    ("B107", "Verificação da alimentação de fontes auxiliares da instalação"),
    ("B108", "Verificação da carga e validade das baterias e reaperto de bornes das fontes auxiliares"),
    ("B109", "Verificação do estado geral de conservação dos detetores"),
    ("B110", "Verificação do estado geral de conservação acessibilidade e sinalização dos botões de Alarme"),
    ("B111", "Verificação do estado geral de conservação das sirenes"),
    ("B112", "Verificação do estado geral das cablagens"),
    ("B113", "Inspeção visual para verificar se ocorreram mudanças estruturais ou ocupacionais que tenham afetado os requisitos para a localização de botões de alarme manual, detetores e sirenes"),
    ("B115", "A inspeção visual para confirmar que um espaço de pelo menos 0,5 m é conservado desimpedido em todas direções abaixo de cada detetor e que todos os botões de alarme manual permanecem desobstruídos e conspícuos"),
]
TRIALS = [
    ("B121", "Ensaios e testes às funções gerais das centrais"),
    ("B122", "Ensaio e teste de todos os detetores"),
    ("B123", "Ensaio e teste de todos os botões"),
    ("B124", "Ensaio e teste de todas as sirenes"),
    ("B125", "Ensaio funcional dos comandos"),
    ("B126", "Ensaio da comunicação de alarme ao corpo de bombeiros ou central recetora de alarmes"),
    ("B127", "Execução de simulação de um alarme por zona e análise das ativações (*)"),
]
START_WARNING = "ATENÇÃO - VERIFICAR SE OCUPANTES FORAM AVISADOS DO ENSAIO. Colocar o Sistema em modo Teste e/ou verificar se comandos podem ser ativados."
END_WARNING = 'Repor todos os sistemas em situação normal de funcionamento. Não esquecer de retirar "modo teste" e/ou "modo service".'
TRIAL_WARNING = "(*) Para esta operação poderão ter de ser desativados alguns módulos de dispositivos de proteção."
COVERAGE_WARNING = "Quando o procedimento seja mensal, trimestral ou semestral indicar em observações a % de elementos testados ou preferencialmente as áreas do edifício onde os elementos foram testados de forma que ao longo do ano 100% dos dispositivos e todas as zonas sejam testados."
PERIODS = {"monthly": "Mensal", "quarterly": "Trimestral", "half_yearly": "Semestral", "annual": "Anual", "other": "Outra"}
SIGNATURE_EXCEPTIONS = {
    "customer": {"field": "customer_not_present", "label": "Cliente não presente na obra"},
}
FIELDS = {
    "conventional": [("brand", "Marca", "text"), ("model", "Modelo", "text"), ("location", "Local da central", "text"), ("total", "N.º zonas total", "number"), ("used", "N.º zonas em uso", "number"), ("detectors", "N.º de detetores", "number"), ("buttons", "N.º de botoneiras", "number"), ("sirens", "N.º de sirenes", "number")],
    "addressable": [("brand", "Marca", "text"), ("model", "Modelo", "text"), ("location", "Local da central", "text"), ("total", "N.º loops central", "number"), ("used", "N.º loops em uso", "number")],
    "repeater": [("brand", "Marca", "text"), ("model", "Modelo", "text"), ("location", "Local do repetidor", "text")],
}
DEFINITION = dict(version=VERSION, groups=GROUPS, fields=FIELDS, periods=PERIODS,
                  signature_exceptions=SIGNATURE_EXCEPTIONS,
                  general=GENERAL, conventional=CONVENTIONAL, addressable=ADDRESSABLE,
                  repeater=REPEATER, peripherals=PERIPHERALS, trials=TRIALS,
                  start_warning=START_WARNING, end_warning=END_WARNING,
                  trial_warning=TRIAL_WARNING, coverage_warning=COVERAGE_WARNING)
PERIPHERAL_FIELDS = ("peripheral_observations", "coverage_percent", "coverage_areas")
# Keep legacy departure values for draft/signature compatibility; no longer displayed or required.
SITE_FIELDS = ("id", "version", "location", "date", "technician", "scie", "departure", "period", "period_other", "observations", "final_observations") + PERIPHERAL_FIELDS
UNIT_FIELDS = ("id", "brand", "model", "location", "total", "used", "detectors", "buttons", "sirens", "observations")
PHOTO_LIMITS = {"count": 10, "bytes": 1_000_000, "edge": 1600, "source_bytes": 10_000_000}
DEFINITION["photo_limits"] = PHOTO_LIMITS


def valid_photo_image(image):
    """Only bounded, decoded raster images may reach preview/PDF img elements."""
    if not isinstance(image, str) or len(image) > PHOTO_LIMITS["bytes"] * 4 // 3 + 32:
        return False
    return _decode_photo_image(image)


@lru_cache(maxsize=32)
def _decode_photo_image(image):
    match = re.fullmatch(r"data:image/(jpeg|png);base64,([A-Za-z0-9+/=]+)", image)
    if not match:
        return False
    try:
        data = base64.b64decode(match[2], validate=True)
        if len(data) > PHOTO_LIMITS["bytes"]:
            return False
        with Image.open(io.BytesIO(data)) as picture:
            if (picture.format != {"jpeg": "JPEG", "png": "PNG"}[match[1]]
                    or not 1 <= picture.width <= PHOTO_LIMITS["edge"]
                    or not 1 <= picture.height <= PHOTO_LIMITS["edge"]):
                return False
            picture.load()
        return True
    except Exception:
        return False


def _photos(value):
    if value is None:
        return []
    if not isinstance(value, list):
        value = [{}]  # Preserve a visible validation error instead of silently dropping it.
    photos = []
    for raw in value:
        raw = raw if isinstance(raw, dict) else {}
        photo = {key: text(raw.get(key)) for key in ("id", "name", "caption", "image")}
        valid = valid_photo_image(photo["image"])
        photo["error"] = "" if valid else "Fotografia inválida. Remova-a e adicione novamente uma imagem JPG, PNG ou WebP."
        if not valid:
            photo["image"] = ""
        photos.append(photo)
    return photos


def text(value):
    return str(value).strip() if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ""


def applicable(document):
    return bool(document.get("service_types", {}).get("manutencao") and document.get("equipments", {}).get("sadi"))


def normalize_sites(value):
    """Whitelist structural data; malformed collections remain invalid, never become complete."""
    if not isinstance(value, list):
        return []
    sites = []
    for raw in value:
        raw = raw if isinstance(raw, dict) else {}
        raw = _upgrade_site(raw)
        site = {key: text(raw.get(key)) for key in SITE_FIELDS}
        for exception in SIGNATURE_EXCEPTIONS.values():
            site[exception["field"]] = raw.get(exception["field"]) is True
        site["photos"] = _photos(raw.get("photos"))
        site["general"] = _answers(raw.get("general"), GENERAL)
        site["peripherals"] = _answers(raw.get("peripherals"), PERIPHERALS)
        site["trials"] = _answers(raw.get("trials"), TRIALS)
        history = raw.get("peripheral_history")
        site["peripheral_history"] = [_history_record(record) for record in history if isinstance(record, dict)] if isinstance(history, list) else []
        site["configuration"] = {}
        config = raw.get("configuration") if isinstance(raw.get("configuration"), dict) else {}
        for kind in GROUPS:
            site["configuration"][kind] = config.get(kind) if isinstance(config.get(kind), bool) else None
            units = raw.get(kind) if isinstance(raw.get(kind), list) else []
            site[kind] = []
            for unit in units:
                unit = unit if isinstance(unit, dict) else {}
                item = {key: text(unit.get(key)) for key in UNIT_FIELDS}
                item["checks"] = _answers(unit.get("checks"), DEFINITION[kind])
                site[kind].append(item)
        signatures = raw.get("signatures") if isinstance(raw.get("signatures"), dict) else {}
        site["signatures"] = {}
        for role in ("technician", "customer"):
            sig = signatures.get(role)
            if isinstance(sig, dict) and not signature_waived(site, role):
                site["signatures"][role] = {key: text(sig.get(key)) for key in ("name", "date", "image", "token")}
        # Unsigned captures survive draft saves but never count as a signature or appear in PDFs.
        drafts = raw.get("signature_drafts")
        site["signature_drafts"] = {}
        if isinstance(drafts, dict):
            for role in ("technician", "customer"):
                draft = drafts.get(role)
                if isinstance(draft, dict) and not signature_waived(site, role):
                    item = {key: text(draft.get(key)) for key in ("name", "date", "image")}
                    if not item["image"].startswith("data:image/png;base64,") or not valid_photo_image(item["image"]):
                        item["image"] = ""
                    site["signature_drafts"][role] = item
        sites.append(site)
    return sites


def _answers(value, questions):
    value = value if isinstance(value, dict) else {}
    return {key: {"answer": text(value.get(key, {}).get("answer")), "justification": text(value.get(key, {}).get("justification"))}
            if isinstance(value.get(key), dict) else {"answer": "", "justification": ""} for key, _ in questions}


def _history_record(raw):
    record = {key: text(raw.get(key)) for key in ("id", "label", "location") + PERIPHERAL_FIELDS}
    record["active"] = raw.get("active") is True
    record["peripherals"] = _answers(raw.get("peripherals"), PERIPHERALS)
    record["trials"] = _answers(raw.get("trials"), TRIALS)
    return record


def _upgrade_site(raw):
    """Lift v1 responses without choosing between conflicting central records.

    Original responses (including inactive equipment) remain in draft history.
    Only unanimous responses from all active centrals can populate the new section.
    This is an in-memory normalization; existing files are not modified on read.
    """
    if raw.get("version") != "sadi-1":
        return raw
    upgraded = deepcopy(raw)
    configuration = raw.get("configuration") if isinstance(raw.get("configuration"), dict) else {}
    records = []
    for kind in ("conventional", "addressable"):
        units = raw.get(kind) if isinstance(raw.get(kind), list) else []
        for index, unit in enumerate(units, 1):
            if isinstance(unit, dict):
                records.append(_history_record({**unit, "label": f"{GROUPS[kind]} {index}",
                                                "active": configuration.get(kind) is True}))
    active = [record for record in records if record["active"]]
    for field, questions in (("peripherals", PERIPHERALS), ("trials", TRIALS)):
        merged = _answers({}, questions)
        for key, _ in questions:
            values = [record[field][key] for record in active]
            if values and all(value == values[0] for value in values):
                merged[key] = deepcopy(values[0])
        upgraded.setdefault(field, merged)
    for field in ("coverage_percent", "coverage_areas"):
        values = [record[field] for record in active]
        upgraded.setdefault(field, values[0] if values and all(value == values[0] for value in values) else "")
    upgraded.setdefault("peripheral_observations", "\n\n".join(
        f"{record['label']}: {record['peripheral_observations']}" for record in active if record["peripheral_observations"]))
    upgraded["peripheral_history"] = [record for record in records if
        any(record[field] for field in PERIPHERAL_FIELDS) or
        any(answer["answer"] or answer["justification"] for field in ("peripherals", "trials") for answer in record[field].values())]
    upgraded["version"] = VERSION
    upgraded["signatures"] = {}
    return upgraded


def _valid_id(value):
    return bool(re.fullmatch(r"[a-zA-Z0-9_-]{8,64}", value))


def _valid_date(value):
    try:
        date.fromisoformat(value)
        return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))
    except ValueError:
        return False


def site_errors(site):
    errors = []
    photos = site.get("photos", [])
    if len(photos) > PHOTO_LIMITS["count"]:
        errors.append(f"Fotografias: máximo de {PHOTO_LIMITS['count']} por local")
    photo_ids = set()
    for index, photo in enumerate(photos, 1):
        if not _valid_id(photo.get("id", "")) or photo["id"] in photo_ids:
            errors.append(f"Fotografia {index}: identificador inválido ou repetido")
        photo_ids.add(photo.get("id"))
        if photo.get("error") or not valid_photo_image(photo.get("image", "")):
            errors.append(f"Fotografia {index}: imagem inválida; remova e adicione novamente")
    for key, label in (("location", "Identificação do local"), ("technician", "Técnico de serviço"), ("date", "Data da manutenção")):
        if not site[key]:
            errors.append(label)
    if not _valid_id(site["id"]):
        errors.append("Identificador do local inválido")
    if site["version"] != VERSION:
        errors.append("Versão SADI não suportada")
    if site["date"] and not _valid_date(site["date"]):
        errors.append("Data da manutenção inválida")
    if site["period"] not in PERIODS:
        errors.append("Periodicidade")
    if site["period"] == "other" and not site["period_other"]:
        errors.append("Descrição da periodicidade")

    def checks(answers, questions, prefix):
        for key, label in questions:
            response = answers[key]
            if response["answer"] not in {"OK", "NC", "NA"}:
                errors.append(f"{prefix}: {label}")
            elif response["answer"] == "NC" and not response["justification"]:
                errors.append(f"{prefix}: justificação NC — {label}")

    checks(site["general"], GENERAL, "Sistema")
    ids = set()
    for kind, title in GROUPS.items():
        enabled = site["configuration"][kind]
        if enabled is None:
            errors.append(f"Existência de {title.lower()}")
        if not enabled:
            continue
        if not site[kind]:
            errors.append(f"Quantidade de {title.lower()}")
        for index, unit in enumerate(site[kind], 1):
            prefix = f"{title} {index}"
            if not _valid_id(unit["id"]) or unit["id"] in ids:
                errors.append(f"{prefix}: identificador inválido ou repetido")
            ids.add(unit["id"])
            for key, label, field_type in FIELDS[kind]:
                value = unit[key]
                if not value or (field_type == "number" and not re.fullmatch(r"\d+", value)):
                    errors.append(f"{prefix}: {label}")
            if unit["total"].isdigit() and unit["used"].isdigit() and int(unit["used"]) > int(unit["total"]):
                errors.append(f"{prefix}: quantidade em uso superior ao total")
            checks(unit["checks"], DEFINITION[kind], prefix)
    checks(site["peripherals"], PERIPHERALS, "Periféricos")
    checks(site["trials"], TRIALS, "Periféricos / Ensaios")
    percent = site["coverage_percent"]
    if percent:
        try:
            if not 0 <= float(percent.replace(",", ".")) <= 100:
                raise ValueError()
        except ValueError:
            errors.append("Periféricos: percentagem inválida")
    if site["period"] in {"monthly", "quarterly", "half_yearly"} and not (percent or site["coverage_areas"]):
        errors.append("Periféricos: percentagem ou áreas testadas")
    return errors


def signature_content(document, site):
    content = deepcopy(site)
    content.pop("signatures", None)
    content.pop("signature_drafts", None)
    # Empty optional additions do not invalidate an existing sadi-2 signature.
    for key in ("photos", "final_observations", *(item["field"] for item in SIGNATURE_EXCEPTIONS.values())):
        if not content.get(key):
            content.pop(key, None)
    for kind in GROUPS:
        if not site["configuration"][kind]:
            content[kind] = []
    return {"site": content, "customer_name": document.get("customer_name", ""),
            "service_number": document.get("service_number", ""), "active": applicable(document)}


def signature_token(secret, document, site, role, signature):
    value = {"content": signature_content(document, site), "role": role,
             "signature": {key: signature.get(key, "") for key in ("name", "date", "image")}}
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return hmac.new(str(secret).encode(), raw, hashlib.sha256).hexdigest()


def signature_waived(site, role):
    field = SIGNATURE_EXCEPTIONS.get(role, {}).get("field")
    return field is not None and site.get(field) is True


def signature_valid(secret, document, site, role):
    if signature_waived(site, role):
        return False
    signature = site["signatures"].get(role, {})
    token = signature.get("token", "")
    return bool(token and hmac.compare_digest(token, signature_token(secret, document, site, role, signature)))


def make_signature(secret, document, site, role, name, signed_date, image):
    """Bind a signature to the current draft; completion is checked at finalization."""
    if role not in {"technician", "customer"} or not text(name) or not _valid_date(text(signed_date)):
        raise ValueError("Indique o signatário e uma data válida.")
    if signature_waived(site, role):
        raise ValueError("Desmarque a exceção antes de recolher esta assinatura.")
    if not _valid_id(site["id"]) or site["version"] != VERSION:
        raise ValueError("Checklist inválida para recolher a assinatura.")
    if not isinstance(image, str) or not image.startswith("data:image/png;base64,") or len(image) > 1_000_000:
        raise ValueError("Assinatura PNG inválida ou demasiado grande.")
    try:
        raw = base64.b64decode(image.split(",", 1)[1], validate=True)
        with Image.open(io.BytesIO(raw)) as pic:
            if pic.format != "PNG" or not 2 <= pic.width <= 2000 or not 2 <= pic.height <= 1000:
                raise ValueError()
            pic.load()
            rgba = pic.convert("RGBA")
            pixels = rgba.get_flattened_data() if hasattr(rgba, "get_flattened_data") else rgba.getdata()
            visible_ink = sum(1 for r, g, b, a in pixels if a > 30 and min(r, g, b) < 220)
            if visible_ink < 12:
                raise ValueError()
    except Exception as exc:
        raise ValueError("Desenhe uma assinatura válida no espaço indicado.") from exc
    signature = {"name": text(name), "date": text(signed_date), "image": image}
    signature["token"] = signature_token(secret, document, site, role, signature)
    return signature


def clear_invalid_signatures(document, secret):
    for site in document.get("maintenance_checklists", []):
        for role in list(site["signatures"]):
            if not signature_valid(secret, document, site, role):
                del site["signatures"][role]


def document_errors(document, secret, signatures=True):
    if not applicable(document):
        return []
    sites = document.get("maintenance_checklists", [])
    if not sites:
        return ["SADI: indique o número de locais e preencha as checklists."]
    errors, ids = [], set()
    for index, site in enumerate(sites, 1):
        prefix = f"SADI / {site['location'] or 'Local ' + str(index)}"
        if site["id"] in ids:
            errors.append(prefix + ": identificador de local repetido")
        ids.add(site["id"])
        errors.extend(f"{prefix}: {message}" for message in site_errors(site))
        if signatures:
            for role, label in (("technician", "técnico"), ("customer", "cliente")):
                if not signature_waived(site, role) and not signature_valid(secret, document, site, role):
                    errors.append(f"{prefix}: assinatura do {label}")
    return errors
