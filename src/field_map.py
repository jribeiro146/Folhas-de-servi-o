"""
Folhas de Serviço — Mapa de campos da sheet LINK.

Este módulo define o contrato estável entre a sheet LINK do Excel
e a interface da aplicação. Todos os acessos a campos do Excel
devem passar por este mapa — nunca aceder a colunas por índice directo.

Estrutura do LINK:
- Linha 1: headers de categorias (tipos de serviço/sistema)
- Linha 2: labels dos campos
- Linha 3: valores dos dados
"""

from dataclasses import dataclass
from enum import Enum


class FieldType(Enum):
    """Tipos de dados suportados nos campos do LINK."""
    TEXT = "text"
    NUMBER = "number"
    DATE = "date"
    CHECKBOX = "checkbox"       # Campos que usam "X" para marcar
    CURRENCY = "currency"       # Valores monetários
    MULTILINE = "multiline"     # Texto longo


class FieldGroup(Enum):
    """Agrupamento lógico dos campos para organização do formulário."""
    IDENTIFICACAO = "Identificação"
    CONTACTO = "Contacto"
    CONTRATO = "Contrato / Cliente"
    TIPO_SERVICO = "Tipo de Serviço"
    SISTEMAS = "Sistemas"
    CLIENTE = "Cliente"
    AVARIA = "Avaria"
    AGENDAMENTO = "Agendamento"
    RELATORIO = "Relatório Técnico"
    TECNICO = "Técnico / Tempos"
    FACTURACAO = "Facturação"
    DESCRICAO = "Descrição / Notas"


@dataclass(frozen=True)
class FieldDef:
    """Definição de um campo do LINK.

    Attributes:
        column: Letra da coluna no Excel (ex: "A", "AE")
        label: Label descritivo do campo
        field_type: Tipo de dado
        group: Grupo lógico para o formulário
        required: Se o campo é obrigatório para Guardar e enviar
        read_only: Se o campo é apenas de leitura na interface
    """
    column: str
    label: str
    field_type: FieldType
    group: FieldGroup
    required: bool = False
    read_only: bool = False


# ---------------------------------------------------------------------------
# Mapa de campos — CONTRATO ESTÁVEL com o Excel
#
# A ordem segue a ordem das colunas no Excel (A → BR).
# Cada entrada mapeia uma coluna do LINK para um campo do formulário.
# ---------------------------------------------------------------------------

FIELD_MAP: list[FieldDef] = [
    # --- Identificação ---
    FieldDef("A",  "Folha nº",         FieldType.TEXT,     FieldGroup.IDENTIFICACAO, required=True, read_only=True),
    FieldDef("B",  "Pedido por",       FieldType.TEXT,     FieldGroup.IDENTIFICACAO),

    # --- Contacto ---
    FieldDef("C",  "Email",            FieldType.TEXT,     FieldGroup.CONTACTO),
    FieldDef("D",  "Telefone",         FieldType.TEXT,     FieldGroup.CONTACTO),
    FieldDef("E",  "Contacto",         FieldType.TEXT,     FieldGroup.CONTACTO),
    FieldDef("F",  "Telefone (2)",     FieldType.TEXT,     FieldGroup.CONTACTO),

    # --- Contrato / Cliente ---
    FieldDef("G",  "Contrato nº",      FieldType.TEXT,     FieldGroup.CONTRATO),
    FieldDef("H",  "Loja nº",          FieldType.TEXT,     FieldGroup.CONTRATO),
    FieldDef("I",  "Data pedido",       FieldType.DATE,     FieldGroup.CONTRATO),
    FieldDef("J",  "Cliente nº",        FieldType.TEXT,     FieldGroup.CONTRATO),
    FieldDef("K",  "Ident",             FieldType.TEXT,     FieldGroup.CONTRATO),

    # --- Tipo de Serviço (checkboxes — marcados com "X") ---
    FieldDef("L",  "ASSIST",           FieldType.CHECKBOX, FieldGroup.TIPO_SERVICO),
    FieldDef("M",  "MAN",              FieldType.CHECKBOX, FieldGroup.TIPO_SERVICO),
    FieldDef("N",  "COL.SERV",         FieldType.CHECKBOX, FieldGroup.TIPO_SERVICO),
    FieldDef("O",  "GAR",              FieldType.CHECKBOX, FieldGroup.TIPO_SERVICO),
    FieldDef("P",  "INST",             FieldType.CHECKBOX, FieldGroup.TIPO_SERVICO),
    FieldDef("Q",  "PIQ",              FieldType.CHECKBOX, FieldGroup.TIPO_SERVICO),
    FieldDef("R",  "FORM",             FieldType.CHECKBOX, FieldGroup.TIPO_SERVICO),
    FieldDef("S",  "REP.OF",           FieldType.CHECKBOX, FieldGroup.TIPO_SERVICO),
    FieldDef("T",  "ACOMP.COM",        FieldType.CHECKBOX, FieldGroup.TIPO_SERVICO),

    # --- Sistemas (checkboxes — marcados com "X") ---
    FieldDef("U",  "SADI",             FieldType.CHECKBOX, FieldGroup.SISTEMAS),
    FieldDef("V",  "CCTV",             FieldType.CHECKBOX, FieldGroup.SISTEMAS),
    FieldDef("W",  "PA/VA",            FieldType.CHECKBOX, FieldGroup.SISTEMAS),
    FieldDef("X",  "SAI",              FieldType.CHECKBOX, FieldGroup.SISTEMAS),
    FieldDef("Y",  "EXT",              FieldType.CHECKBOX, FieldGroup.SISTEMAS),
    FieldDef("Z",  "SCA",              FieldType.CHECKBOX, FieldGroup.SISTEMAS),
    FieldDef("AA", "EAS",              FieldType.CHECKBOX, FieldGroup.SISTEMAS),
    FieldDef("AB", "SADG",             FieldType.CHECKBOX, FieldGroup.SISTEMAS),
    FieldDef("AC", "SCH",              FieldType.CHECKBOX, FieldGroup.SISTEMAS),
    FieldDef("AD", "OTHER",            FieldType.CHECKBOX, FieldGroup.SISTEMAS),

    # --- Cliente ---
    FieldDef("AE", "Cliente nome",      FieldType.TEXT,     FieldGroup.CLIENTE, required=True),
    FieldDef("AF", "Local",             FieldType.TEXT,     FieldGroup.CLIENTE),
    FieldDef("AG", "Morada",            FieldType.TEXT,     FieldGroup.CLIENTE),
    FieldDef("AH", "Cód. Postal",       FieldType.TEXT,     FieldGroup.CLIENTE),
    FieldDef("AI", "CP",                FieldType.TEXT,     FieldGroup.CLIENTE),
    FieldDef("AJ", "NIF",               FieldType.TEXT,     FieldGroup.CLIENTE),

    # --- Avaria ---
    FieldDef("AK", "Avaria reportada",  FieldType.MULTILINE, FieldGroup.AVARIA),

    # --- Agendamento ---
    FieldDef("AL", "Data serviço",      FieldType.TEXT,     FieldGroup.AGENDAMENTO),
    FieldDef("AM", "Mês",               FieldType.TEXT,     FieldGroup.AGENDAMENTO),

    # --- Relatório Técnico ---
    FieldDef("AN", "Relatório Técnico", FieldType.MULTILINE, FieldGroup.RELATORIO),

    # --- Técnico / Tempos ---
    FieldDef("AO", "Técnico(s)",        FieldType.TEXT,     FieldGroup.TECNICO),
    FieldDef("AP", "Cód. Trabalho",     FieldType.TEXT,     FieldGroup.TECNICO),
    FieldDef("AQ", "T.Viagem",          FieldType.TEXT,     FieldGroup.TECNICO),
    FieldDef("AR", "T.Trabalho",        FieldType.TEXT,     FieldGroup.TECNICO),
    FieldDef("AS", "Fim",               FieldType.TEXT,     FieldGroup.TECNICO),
    FieldDef("AT", "T.Total trabalho",  FieldType.TEXT,     FieldGroup.TECNICO),
    FieldDef("AU", "T.Total h",         FieldType.NUMBER,   FieldGroup.TECNICO),
    FieldDef("AV", "T.Total m",         FieldType.NUMBER,   FieldGroup.TECNICO),
    FieldDef("AW", "T.Total h (2)",     FieldType.TEXT,     FieldGroup.TECNICO),
    FieldDef("AX", "T.Total m (2)",     FieldType.TEXT,     FieldGroup.TECNICO),

    # --- Facturação ---
    FieldDef("AY", "Valor (hora)",      FieldType.CURRENCY, FieldGroup.FACTURACAO),
    FieldDef("AZ", "Valores a faturar", FieldType.CURRENCY, FieldGroup.FACTURACAO),
    FieldDef("BA", "Deslocação Lx/Pt/Fun", FieldType.CURRENCY, FieldGroup.FACTURACAO),
    FieldDef("BB", "Deslocação Kms",    FieldType.CURRENCY, FieldGroup.FACTURACAO),
    FieldDef("BC", "Deslocação custo/km", FieldType.CURRENCY, FieldGroup.FACTURACAO),
    FieldDef("BD", "Piquete",           FieldType.CURRENCY, FieldGroup.FACTURACAO),
    FieldDef("BE", "Material",          FieldType.CURRENCY, FieldGroup.FACTURACAO),
    FieldDef("BF", "Plataforma",        FieldType.CURRENCY, FieldGroup.FACTURACAO),
    FieldDef("BG", "Plataforma custo",  FieldType.CURRENCY, FieldGroup.FACTURACAO),
    FieldDef("BH", "Manutenção",        FieldType.CURRENCY, FieldGroup.FACTURACAO),
    FieldDef("BJ", "Acerto de valor",   FieldType.CURRENCY, FieldGroup.FACTURACAO),
    FieldDef("BK", "TOTAL",             FieldType.CURRENCY, FieldGroup.FACTURACAO, read_only=True),
    FieldDef("BL", "TOTAL s/ MAT/PLAF", FieldType.CURRENCY, FieldGroup.FACTURACAO, read_only=True),

    # --- Descrição / Notas ---
    FieldDef("BM", "Descrição do trabalho executado", FieldType.MULTILINE, FieldGroup.DESCRICAO),
    FieldDef("BN", "Orçamento nº",      FieldType.TEXT,     FieldGroup.DESCRICAO),
    FieldDef("BO", "Nota de encomenda",  FieldType.TEXT,     FieldGroup.DESCRICAO),
    FieldDef("BP", "Faturado?",          FieldType.TEXT,     FieldGroup.DESCRICAO),
    FieldDef("BQ", "Material que falta", FieldType.MULTILINE, FieldGroup.DESCRICAO),
    FieldDef("BR", "Observações",        FieldType.MULTILINE, FieldGroup.DESCRICAO),

    # --- Obra (posição física descoberta pelo cabeçalho da linha 2) ---
    FieldDef(
        "BS",
        "N.º de obra",
        FieldType.TEXT,
        FieldGroup.CONTRATO,
        read_only=True,
    ),
]


# ---------------------------------------------------------------------------
# Índices de acesso rápido
# ---------------------------------------------------------------------------

# Mapa: coluna → FieldDef
FIELD_BY_COLUMN: dict[str, FieldDef] = {f.column: f for f in FIELD_MAP}

# Mapa: label → FieldDef
FIELD_BY_LABEL: dict[str, FieldDef] = {f.label: f for f in FIELD_MAP}

# Campos agrupados por grupo
FIELDS_BY_GROUP: dict[FieldGroup, list[FieldDef]] = {}
for _f in FIELD_MAP:
    FIELDS_BY_GROUP.setdefault(_f.group, []).append(_f)

# Lista de campos obrigatórios
REQUIRED_FIELDS: list[FieldDef] = [f for f in FIELD_MAP if f.required]

# Lista de campos editáveis (não read_only)
EDITABLE_FIELDS: list[FieldDef] = [f for f in FIELD_MAP if not f.read_only]
