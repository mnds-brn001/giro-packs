"""Ingestão do export Express/SRM → pastas do projeto + Parquet dos packs."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .loader import convert_tracking_to_parquet
from .paths import (
    ENTREGUES_DIR,
    EXPORT_GLOB,
    MONITORAMENTO_DIR,
    PARQUET_DIR,
    PRAZO_DIR,
    ROOT,
)

STAMP_RE = re.compile(r"(20\d{6})\d*")
PARQUET_SUFFIXES = {".parquet", ".pq"}


@dataclass(frozen=True)
class IngestResult:
    source: Path
    parquet: Path
    latest: Path
    stamp: str
    skipped: bool


def _layout(root: Path | None = None) -> tuple[Path, Path, Path, Path, Path]:
    base = root or ROOT
    return (
        base,
        base / "data" / "monitoramento",
        base / "data" / "parquet",
        base / "data" / "prazo",
        base / "data" / "entregues",
    )


def stamp_from_export(path: Path, *, fallback: date | None = None) -> str:
    """YYYY-MM-DD a partir do timestamp do filename (…-20260908104227066-…)."""
    match = STAMP_RE.search(path.name)
    if match:
        raw = match.group(1)
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    when = fallback or date.today()
    return when.isoformat()


def _newest(paths: list[Path]) -> Path | None:
    files = [p for p in paths if p.is_file() and not p.name.startswith("~$")]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def _prazo_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return [
        path
        for path in folder.glob("*.xlsx")
        if "prazo" in path.name.lower() and not path.name.startswith("~$")
    ]


def resolve_monitoramento(
    explicit: str | Path | None = None,
    *,
    root: Path | None = None,
) -> Path:
    """Arquivo de monitoramento: argumento, data/monitoramento, ou raiz."""
    base, mon_dir, parquet_dir, _prazo, _ent = _layout(root)
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file():
            return path.resolve()
        for folder in (mon_dir, parquet_dir, base):
            nested = folder / path.name
            if nested.is_file():
                return nested.resolve()
        raise FileNotFoundError(f"Export não encontrado: {path}")

    found = _newest(list(mon_dir.glob(EXPORT_GLOB)))
    if found:
        return found.resolve()
    found = _newest(list(base.glob(EXPORT_GLOB)))
    if found:
        return found.resolve()
    found = _newest(
        list(parquet_dir.glob("monitoramento_*.parquet"))
        + list(base.glob("monitoramento*.parquet"))
    )
    if found:
        return found.resolve()
    raise FileNotFoundError(
        f"Nenhum {EXPORT_GLOB} em {mon_dir} nem na raiz do projeto."
    )


def resolve_prazo(
    explicit: str | Path | None = None,
    *,
    root: Path | None = None,
) -> Path:
    base, _mon, _parquet, prazo_dir, _ent = _layout(root)
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file():
            return path.resolve()
        nested = prazo_dir / path.name
        if nested.is_file():
            return nested.resolve()
        nested = base / path.name
        if nested.is_file():
            return nested.resolve()
        raise FileNotFoundError(f"Prazo não encontrado: {path}")

    found = _newest(_prazo_files(prazo_dir))
    if found:
        return found.resolve()
    found = _newest(_prazo_files(base))
    if found:
        return found.resolve()
    raise FileNotFoundError(f"Nenhum arquivo de prazo em {prazo_dir} nem na raiz do projeto.")


def _stage_xlsx(xlsx: Path, monitoramento_dir: Path) -> Path:
    monitoramento_dir.mkdir(parents=True, exist_ok=True)
    dest = monitoramento_dir / xlsx.name
    if dest.resolve() == xlsx.resolve():
        return dest
    if not dest.is_file() or xlsx.stat().st_mtime > dest.stat().st_mtime:
        shutil.copy2(xlsx, dest)
    return dest


def _publish_latest(dated: Path, latest: Path) -> None:
    if dated.resolve() == latest.resolve():
        return
    shutil.copy2(dated, latest)


def ingest_monitoramento(
    source: str | Path | None = None,
    *,
    force: bool = False,
    root: Path | None = None,
) -> IngestResult:
    """Lê o xlsx de pontualidade e grava Parquet para os packs.

    - ``data/parquet/monitoramento_YYYY-MM-DD.parquet`` (esta extração)
    - ``data/parquet/monitoramento_latest.parquet`` (reuso no mesmo dia)
    """
    _base, mon_dir, parquet_dir, _prazo, _ent = _layout(root)
    parquet_dir.mkdir(parents=True, exist_ok=True)

    origin = resolve_monitoramento(source, root=root)
    stamp = stamp_from_export(origin)
    dated = parquet_dir / f"monitoramento_{stamp}.parquet"
    latest = parquet_dir / "monitoramento_latest.parquet"

    if origin.suffix.lower() in PARQUET_SUFFIXES:
        if origin.resolve() != dated.resolve():
            shutil.copy2(origin, dated)
        _publish_latest(dated, latest)
        return IngestResult(
            source=origin,
            parquet=dated,
            latest=latest,
            stamp=stamp,
            skipped=True,
        )

    staged = _stage_xlsx(origin, mon_dir)
    skip = (
        not force
        and dated.is_file()
        and dated.stat().st_mtime >= staged.stat().st_mtime
    )
    if not skip:
        convert_tracking_to_parquet(staged, dated)
    _publish_latest(dated, latest)
    return IngestResult(
        source=staged,
        parquet=dated,
        latest=latest,
        stamp=stamp,
        skipped=skip,
    )


def resolve_entregues(
    explicit: str | Path | None = None,
    *,
    root: Path | None = None,
) -> Path:
    """Export só de Pedido entregue: argumento, data/entregues, ou parquet."""
    base, _mon, parquet_dir, _prazo, ent_dir = _layout(root)
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file():
            return path.resolve()
        for folder in (ent_dir, parquet_dir, base):
            nested = folder / path.name
            if nested.is_file():
                return nested.resolve()
        raise FileNotFoundError(f"Export de entregues não encontrado: {path}")

    found = _newest(list(ent_dir.glob(EXPORT_GLOB)))
    if found:
        return found.resolve()
    found = _newest(
        list(parquet_dir.glob("entregues_*.parquet"))
        + list(parquet_dir.glob("entregues_latest.parquet"))
    )
    if found:
        return found.resolve()
    raise FileNotFoundError(f"Nenhum {EXPORT_GLOB} em {ent_dir}.")


def ingest_entregues(
    source: str | Path | None = None,
    *,
    force: bool = False,
    root: Path | None = None,
) -> IngestResult:
    """Converte a extração de entregues e grava Parquet da aba SLA Entregues.

    - ``data/parquet/entregues_YYYY-MM-DD.parquet``
    - ``data/parquet/entregues_latest.parquet``
    """
    _base, _mon, parquet_dir, _prazo, ent_dir = _layout(root)
    parquet_dir.mkdir(parents=True, exist_ok=True)

    origin = resolve_entregues(source, root=root)
    stamp = stamp_from_export(origin)
    dated = parquet_dir / f"entregues_{stamp}.parquet"
    latest = parquet_dir / "entregues_latest.parquet"

    if origin.suffix.lower() in PARQUET_SUFFIXES:
        if origin.resolve() != dated.resolve():
            shutil.copy2(origin, dated)
        _publish_latest(dated, latest)
        return IngestResult(
            source=origin,
            parquet=dated,
            latest=latest,
            stamp=stamp,
            skipped=True,
        )

    staged = _stage_xlsx(origin, ent_dir)
    skip = (
        not force
        and dated.is_file()
        and dated.stat().st_mtime >= staged.stat().st_mtime
    )
    if not skip:
        convert_tracking_to_parquet(staged, dated)
    _publish_latest(dated, latest)
    return IngestResult(
        source=staged,
        parquet=dated,
        latest=latest,
        stamp=stamp,
        skipped=skip,
    )


def default_dirs() -> tuple[Path, Path, Path, Path]:
    """Atalho para scripts: (monitoramento, parquet, prazo, entregues)."""
    return MONITORAMENTO_DIR, PARQUET_DIR, PRAZO_DIR, ENTREGUES_DIR
