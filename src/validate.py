"""Validação de dados da Tabela Mestra antes do cálculo de KPIs."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .columns import COL, DATETIME_KEYS

EMPTY_MARKERS = {"", "--", "—", "nan", "None", "NaT", "null", "NULL"}

# Colunas mínimas para KPIs, alertas e risco/SLA
REQUIRED_COLUMNS = (
    "waybill",
    "status",
    "pre_point",
    "entry_point",
    "delay_days",
    "closed_loop_days",
    "sla_days",
)

# Campos críticos: nulos atrapalham identidade, malha ou atraso/SLA
CRITICAL_NULL_FIELDS = (
    "waybill",
    "status",
    "pre_point",
    "entry_point",
    "delay_days",
    "closed_loop_days",
    "sla_days",
    "due_at",
)

# Datas mais usadas em risco / timeline / SOP
CRITICAL_DATETIME_FIELDS = (
    "due_at",
    "closed_loop_at",
    "signed_at",
    "attempt_1",
    "attempt_2",
    "attempt_3",
    "last_track_time",
    "created_at",
)


@dataclass
class ValidationIssue:
    code: str
    severity: str  # error | warning
    message: str
    column: str | None = None
    count: int | None = None


@dataclass
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(
        self,
        code: str,
        severity: str,
        message: str,
        *,
        column: str | None = None,
        count: int | None = None,
    ) -> None:
        self.issues.append(
            ValidationIssue(
                code=code,
                severity=severity,
                message=message,
                column=column,
                count=count,
            )
        )

    def summary_lines(self) -> list[str]:
        lines: list[str] = []
        for issue in self.issues:
            prefix = "ERRO" if issue.severity == "error" else "AVISO"
            suffix = f" ({issue.count})" if issue.count is not None else ""
            lines.append(f"[{prefix}] {issue.message}{suffix}")
        return lines

    def to_dict(self) -> dict:
        """Formato JSON-serializável (seguro para df.attrs / Streamlit / Arrow)."""
        return {
            "issues": [
                {
                    "code": i.code,
                    "severity": i.severity,
                    "message": i.message,
                    "column": i.column,
                    "count": i.count,
                }
                for i in self.issues
            ]
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> ValidationReport:
        report = cls()
        if not data:
            return report
        for item in data.get("issues", []):
            report.issues.append(
                ValidationIssue(
                    code=item.get("code", ""),
                    severity=item.get("severity", "warning"),
                    message=item.get("message", ""),
                    column=item.get("column"),
                    count=item.get("count"),
                )
            )
        return report


def get_validation_report(df: pd.DataFrame) -> ValidationReport | None:
    """Lê relatório de ``df.attrs['validation']`` (objeto ou dict)."""
    cached = df.attrs.get("validation")
    if cached is None:
        return None
    if isinstance(cached, ValidationReport):
        return cached
    if isinstance(cached, dict):
        return ValidationReport.from_dict(cached)
    return None


class DataValidationError(ValueError):
    """Falhas bloqueantes de schema/qualidade que impedem KPIs confiáveis."""

    def __init__(self, report: ValidationReport):
        self.report = report
        msg = "; ".join(report.summary_lines()) or "Dados inválidos para KPIs"
        super().__init__(msg)


def _is_empty_value(val) -> bool:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return True
    if pd.isna(val):
        return True
    text = str(val).strip()
    return text in EMPTY_MARKERS


def _is_filled(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.strip()
    return series.notna() & ~s.isin(EMPTY_MARKERS)


def check_required_columns(df: pd.DataFrame, report: ValidationReport | None = None) -> ValidationReport:
    """Colunas obrigatórias ausentes (erro bloqueante)."""
    report = report or ValidationReport()
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    for col in missing:
        label = COL.get(col, col)
        report.add(
            "missing_column",
            "error",
            f"Coluna obrigatória ausente: {col} ({label})",
            column=col,
        )
    return report


def check_date_formats(df: pd.DataFrame, report: ValidationReport | None = None) -> ValidationReport:
    """Valores preenchidos que não parseiam como data (formato inconsistente)."""
    report = report or ValidationReport()
    keys = [k for k in DATETIME_KEYS if k in df.columns]
    # Prioriza campos críticos, mas varre todos os datetime canônicos presentes
    ordered = [k for k in CRITICAL_DATETIME_FIELDS if k in keys] + [
        k for k in keys if k not in CRITICAL_DATETIME_FIELDS
    ]

    for col in ordered:
        series = df[col]
        filled = _is_filled(series)
        if not filled.any():
            continue

        # Já datetime: NaT onde havia valor original indica falha prévia de parse
        if pd.api.types.is_datetime64_any_dtype(series):
            bad = int((filled & series.isna()).sum())
        else:
            parsed = pd.to_datetime(series.where(filled), errors="coerce")
            bad = int((filled & parsed.isna()).sum())

        if bad <= 0:
            continue

        severity = "error" if col in CRITICAL_DATETIME_FIELDS else "warning"
        report.add(
            "bad_date_format",
            severity,
            f"Formato de data inconsistente em '{col}': {bad} valor(es) não parseáveis",
            column=col,
            count=bad,
        )
    return report


def check_critical_nulls(df: pd.DataFrame, report: ValidationReport | None = None) -> ValidationReport:
    """Nulos / marcadores vazios em campos críticos."""
    report = report or ValidationReport()
    if df.empty:
        report.add("empty_dataframe", "error", "DataFrame vazio — sem linhas para calcular KPIs")
        return report

    total = len(df)
    for col in CRITICAL_NULL_FIELDS:
        if col not in df.columns:
            continue
        null_mask = df[col].map(_is_empty_value)
        n_null = int(null_mask.sum())
        if n_null <= 0:
            continue
        pct = n_null / total * 100
        # Identidade: nulo bloqueia. SLA/malha: aviso (export real costuma ter "--" em entregues).
        # Só bloqueia SLA/malha se a coluna estiver 100% vazia.
        if col in ("waybill", "status"):
            severity = "error"
        elif pct >= 100:
            severity = "error"
        else:
            severity = "warning"
        report.add(
            "critical_null",
            severity,
            f"Valores nulos em campo crítico '{col}': {n_null}/{total} ({pct:.1f}%)",
            column=col,
            count=n_null,
        )
    return report


def validate_tracking_data(df: pd.DataFrame) -> ValidationReport:
    """Executa todas as checagens de qualidade antes dos KPIs."""
    report = ValidationReport()
    check_required_columns(df, report)
    check_date_formats(df, report)
    check_critical_nulls(df, report)
    return report


def ensure_valid_for_kpi(df: pd.DataFrame, *, raise_on_error: bool = True) -> ValidationReport:
    """Valida e, por padrão, bloqueia cálculo de KPI se houver erros."""
    report = validate_tracking_data(df)
    if raise_on_error and not report.ok:
        raise DataValidationError(report)
    return report
