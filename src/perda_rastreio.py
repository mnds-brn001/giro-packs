"""Perda de rastreio — cobrança direta nas bases onde o bip parou.

Fonte: exportação de pacotes parados (ou o controle consolidado, aba ``DD``).
A carteira vem da planilha de prazo, cruzada no ponto de parada. Hub não entra
na cobrança da base: volume que morreu no transbordo vai para o resumo de hub.
A faixa abaixo de 20 dias fica de fora da mensagem.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .backlog_lastmile import _append_waybill_copy_block
from .mesh_action import HUB_POINT_RE
from .paths import PERDA_DIR

CHARGE_MIN_DAYS = 20
MIN_BASE = 5
DATE_SHARE = 0.5

EVENTO = {
    "veiculo": "desbloqueio de veículo",
    "expedido hub": "expedido pelo hub sem bip seguinte",
    "voltou hub": "volta ao hub sem recebimento",
    "no ponto": "recebido no ponto",
    "falhou": "entrega falhou",
    "em rota": "saiu para entrega",
    "outro": "outro evento",
}

_DESTINO_RE = re.compile(r"enviado para \[([^\]]+)\]", re.IGNORECASE)

_HEADER_HINTS = {
    "waybill": ("numero do waybill",),
    "dias": ("dias de retencao",),
    "status": ("status do pacote",),
    "ultimo": ("ultimo rastreio",),
    "ponto": ("ponto de parada",),
    "resp": ("responsabilidade",),
    "pre": ("ponto de pre-entrega", "pre-entrega"),
    "track_at": ("tempo de rastreio recente",),
    "cidade": ("cidade do destinatario",),
    "cliente": ("nome do cliente",),
}
_REQUIRED = ("waybill", "dias", "ultimo", "ponto")


@dataclass(frozen=True)
class PerdaRastreioRoutine:
    as_of: pd.Timestamp
    detail: pd.DataFrame
    grave: pd.DataFrame
    summary: pd.DataFrame
    tail: pd.DataFrame
    messages: dict[str, str]
    owner_messages: dict[str, str]
    hubs: pd.DataFrame
    hub_messages: dict[str, str]


def classificar_evento(texto: str) -> str:
    s = str(texto or "").lower()
    if "desbloqueado" in s or "bloqueado" in s:
        return "veiculo"
    if "entrega falhou" in s:
        return "falhou"
    if "saiu para entrega" in s:
        return "em rota"
    if "recebido pelo ponto" in s or "armazenado" in s:
        return "no ponto"
    if "transbordo" in s or "enviado para" in s:
        return "expedido hub"
    if "devolvido ao centro" in s:
        return "voltou hub"
    return "outro"


def attach_carteira(work: pd.DataFrame, responsabilidade: pd.DataFrame) -> pd.DataFrame:
    """Preenche o dono da base pelo ponto de parada. Não apaga carteira já preenchida."""
    resp = responsabilidade.copy()
    resp["base"] = resp["base"].astype("string").str.strip()
    resp["responsavel"] = resp["responsavel"].astype("string").str.strip()
    resp = resp.dropna(subset=["base"]).drop_duplicates("base", keep="first")
    out = work.merge(
        resp.rename(columns={"base": "ponto", "responsavel": "_dono"}),
        on="ponto",
        how="left",
    )
    atual = out["resp"].fillna("").astype(str).str.strip()
    dono = out["_dono"].fillna("").astype(str).str.strip()
    out["resp"] = atual.where(atual.ne("") & atual.ne("SEM CARTEIRA"), dono)
    out["resp"] = out["resp"].replace("", "SEM CARTEIRA")
    return out.drop(columns=["_dono"])


def _fold(text: str) -> str:
    stripped = unicodedata.normalize("NFKD", str(text).strip().lower())
    return "".join(ch for ch in stripped if not unicodedata.combining(ch))


def _header_label(column: str) -> str:
    match = re.search(r"\(([^)]+)\)", str(column))
    return _fold(match.group(1) if match else column)


def map_perda_columns(columns) -> dict[str, str]:
    """Nome original -> chave canônica. Usa o rótulo PT dentro dos parênteses."""
    found: dict[str, str] = {}
    for column in columns:
        label = _header_label(column)
        for key, hints in _HEADER_HINTS.items():
            if key in found:
                continue
            if any(hint in label for hint in hints):
                found[key] = column
                break
    missing = [key for key in _REQUIRED if key not in found]
    if missing:
        raise ValueError(f"Controle sem colunas de perda de rastreio: {', '.join(missing)}")
    return found


def normalize_perda(raw: pd.DataFrame) -> pd.DataFrame:
    mapping = map_perda_columns(raw.columns)
    work = raw.rename(columns={src: key for key, src in mapping.items()}).copy()
    work["waybill"] = work["waybill"].map(lambda v: "" if pd.isna(v) else str(v).strip())
    work = work.loc[work["waybill"].ne("") & work["waybill"].ne("nan")].copy()
    work["dias"] = pd.to_numeric(work["dias"], errors="coerce")
    work["ponto"] = work["ponto"].map(lambda v: "" if pd.isna(v) else str(v).strip())
    work["ultimo"] = work["ultimo"].map(lambda v: "" if pd.isna(v) else str(v).strip())
    work["evento"] = work["ultimo"].map(classificar_evento)
    if "track_at" in work.columns:
        work["track_at"] = pd.to_datetime(work["track_at"], errors="coerce")
    else:
        work["track_at"] = pd.NaT
    for key, default in (("status", ""), ("resp", "SEM CARTEIRA"), ("pre", ""), ("cidade", ""), ("cliente", "")):
        if key not in work.columns:
            work[key] = default
        work[key] = work[key].map(lambda v, empty=default: empty if pd.isna(v) or str(v).strip() == "" else str(v).strip())
    work["resp"] = work["resp"].replace({"": "SEM CARTEIRA"})
    return work.reset_index(drop=True)


def _pick_sheet(book: pd.ExcelFile) -> str:
    named = [name for name in book.sheet_names if str(name).strip().upper() == "DD"]
    if named:
        return named[0]
    for name in book.sheet_names:
        preview = pd.read_excel(book, sheet_name=name, nrows=0)
        try:
            map_perda_columns(preview.columns)
        except ValueError:
            continue
        return name
    raise ValueError("Nenhuma aba com waybill, dias de retenção, último rastreio e ponto de parada.")


def perda_dir(root: Path | None = None) -> Path:
    if root is None:
        return PERDA_DIR
    return Path(root) / "data" / "monitoramento da perda de rastreio"


def resolve_perda_rastreio(
    explicit: str | Path | None = None,
    *,
    root: Path | None = None,
) -> Path:
    """Export de pacotes parados: argumento ou o xlsx mais novo da pasta de drop."""
    folder = perda_dir(root)
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file():
            return path.resolve()
        nested = folder / path.name
        if nested.is_file():
            return nested.resolve()
        raise FileNotFoundError(f"Export de perda de rastreio não encontrado: {path}")
    files = [
        path
        for path in folder.glob("*.xlsx")
        if path.is_file() and not path.name.startswith("~$")
    ] if folder.is_dir() else []
    if not files:
        raise FileNotFoundError(
            f"Nenhum xlsx em {folder}. Coloque a exportação de pacotes parados nessa pasta."
        )
    return max(files, key=lambda path: path.stat().st_mtime).resolve()


def load_perda_rastreio(source: str | Path) -> pd.DataFrame:
    path = Path(source)
    book = pd.ExcelFile(path, engine="calamine")
    sheet = _pick_sheet(book)
    raw = pd.read_excel(book, sheet_name=sheet, engine="calamine")
    return normalize_perda(raw)


def stamp_from_controle(path: str | Path) -> str | None:
    """Data do nome ``06.10.2026`` ou stamp ``20YYMMDD``."""
    name = Path(path).name
    dotted = re.search(r"(?<!\d)(\d{2})\.(\d{2})\.(20\d{2})(?!\d)", name)
    if dotted:
        day, month, year = dotted.groups()
        return f"{year}-{month}-{day}"
    stamp = re.search(r"(20\d{2})(\d{2})(\d{2})", name)
    if stamp:
        year, month, day = stamp.groups()
        return f"{year}-{month}-{day}"
    return None


def _hub_nome(texto: str) -> str:
    match = re.search(r"centro de trânsito de \[([^\]]+)\]", texto, flags=re.IGNORECASE)
    return match.group(1) if match else "o hub"


def _quando(lote: pd.DataFrame) -> str:
    datas = lote["track_at"].dropna()
    if datas.empty:
        return ""
    contagem = datas.dt.date.value_counts()
    if contagem.iloc[0] / len(lote) >= DATE_SHARE:
        return f", em {pd.Timestamp(contagem.index[0]).strftime('%d/%m/%Y')}"
    return (
        f", com último bip entre {datas.min().strftime('%d/%m/%Y')} "
        f"e {datas.max().strftime('%d/%m/%Y')}"
    )


def _pedido(evento: str, base: str, quando: str, amostra: str) -> str:
    if evento == "veiculo":
        return (
            f"O rastreio morreu no desbloqueio do veículo{quando}. "
            f"O último evento é \"{amostra}\" e não houve bip depois. "
            f"A carga abriu em *{base}* e sumiu do sistema."
        )
    if evento == "expedido hub":
        return (
            f"O último evento é expedição do hub{quando}. "
            f"O texto que ficou aberto é \"{amostra}\". Não houve bip no destino."
        )
    if evento == "voltou hub":
        return (
            f"O último evento é a saída de volta para *{_hub_nome(amostra)}*{quando}. "
            "O hub não bipou o recebimento. Expedir não prova que o volume chegou. "
            "Ou o hub confirma a entrada física, ou o pacote ainda está na base."
        )
    if evento == "no ponto":
        return (
            f"O pacote foi recebido em *{base}* e o rastreio parou aí{quando}. "
            "Está no piso, sem rota e sem devolução."
        )
    if evento == "falhou":
        return (
            f"A última marca é \"A entrega falhou\"{quando}, sem tentativa nova "
            "e sem motivo que explique a parada."
        )
    return f"O rastreio parou{quando}. O evento que ficou aberto é \"{amostra}\"."


def _acao(evento: str) -> str:
    if evento == "veiculo":
        return (
            "Localizar a carga desse desbloqueio hoje. Bipar a entrada no ponto ou declarar o extravio. "
            "Sem bip novo, o pacote continua na base."
        )
    if evento == "expedido hub":
        return "Redespachar ou transbordar em lote. O pacote saiu no sistema e o destino não bipou a entrada."
    if evento == "voltou hub":
        return (
            "Fechar a mão: hub bipa o recebimento, ou a base assume que o volume não saiu. "
            "Não deixar a expedição como último rastreio."
        )
    if evento == "no ponto":
        return "Colocar em rota neste turno ou devolver com motivo real. Pacote recebido sem movimento é perda de rastreio da base."
    if evento == "falhou":
        return "Abrir o motivo real da falha e gerar tentativa nova ou devolução. \"Entrega falhou\" sem movimento seguinte não encerra o pacote."
    return "Localizar o volume, bipar o ponto em que ele está e encerrar: entrega ou devolução com motivo."


def _mensagem(base: str, owner: str, grave: pd.DataFrame, recente: int, as_of: pd.Timestamp) -> str:
    dias = grave["dias"]
    evento = str(grave["evento"].value_counts().index[0])
    lote = grave.loc[grave["evento"].eq(evento)]
    amostra = str(lote["ultimo"].mode().iloc[0])
    cidades = grave["cidade"].replace("", pd.NA).dropna().value_counts().head(4)
    cidade_txt = ", ".join(f"{nome} ({qtd})" for nome, qtd in cidades.items()) or "sem cidade"
    lines = [
        "📍 *PERDA DE RASTREIO — COBRANÇA DIRETA*",
        "",
        f"Base: *{base}* | Carteira: *{owner}* | Corte: *{as_of.strftime('%d/%m/%Y')}*",
        (
            f"Rastreio parado: *{len(grave)}*  "
            f"(50d: {int((dias == 50).sum())} · 30d: {int((dias == 30).sum())} · 20d: {int((dias == 20).sum())})"
        ),
        "",
        _pedido(evento, base, _quando(lote), amostra),
        "",
        _acao(evento),
        "",
        f"Cidades: {cidade_txt}.",
    ]
    outros = grave["evento"].value_counts().drop(labels=[evento], errors="ignore")
    if not outros.empty:
        extra = ", ".join(f"{EVENTO.get(nome, nome)} {int(qtd)}" for nome, qtd in outros.items())
        lines.extend(["", f"No mesmo lote, fora do evento principal: {extra}."])
    fora = int(grave["pre"].ne(base).sum()) if "pre" in grave.columns else 0
    if fora:
        lines.extend(
            [
                "",
                f"{fora} AJ deste lote estão com pré-alocado diferente de {base}. "
                "A cobrança é de quem segurou o rastreio, no ponto onde ele parou.",
            ]
        )
    if recente:
        lines.extend(["", f"Além destes, a base tem *{recente}* AJ na faixa de 7–15 dias. Não misturar com esta cobrança."])
    _append_waybill_copy_block(lines, grave, title="AJs para copiar")
    lines.extend(["", "Resposta por AJ: localizado e bipado, ou extravio assumido. Sem texto genérico."])
    return "\n".join(lines)


def _ranking(grave: pd.DataFrame) -> pd.DataFrame:
    if grave.empty:
        return pd.DataFrame(columns=["resp", "ponto", "parados", "d50", "d30", "d20", "evento"])
    summary = (
        grave.groupby(["resp", "ponto"], dropna=False)
        .agg(
            parados=("waybill", "size"),
            d50=("dias", lambda s: int((s == 50).sum())),
            d30=("dias", lambda s: int((s == 30).sum())),
            d20=("dias", lambda s: int((s == 20).sum())),
        )
        .reset_index()
    )
    dominante = grave.groupby("ponto")["evento"].agg(lambda s: s.value_counts().index[0])
    summary["evento"] = summary["ponto"].map(dominante).map(lambda key: EVENTO.get(key, key))
    return summary.sort_values(["d50", "d30", "parados"], ascending=False)


def _hub_quadro(hubs: pd.DataFrame) -> pd.DataFrame:
    columns = ["resp", "ponto", "parados", "d50", "d30", "d20", "destino", "n_destino"]
    if hubs.empty:
        return pd.DataFrame(columns=columns)
    work = hubs.copy()
    work["destino"] = work["ultimo"].map(lambda texto: m.group(1) if (m := _DESTINO_RE.search(str(texto))) else "")
    quadro = _ranking(work).drop(columns=["evento"])
    top = (
        work.loc[work["destino"].ne("")]
        .groupby(["ponto", "destino"], dropna=False)
        .size()
        .reset_index(name="n_destino")
        .sort_values("n_destino", ascending=False)
        .drop_duplicates("ponto")
    )
    quadro = quadro.merge(top, on="ponto", how="left")
    quadro["destino"] = quadro["destino"].fillna("")
    quadro["n_destino"] = quadro["n_destino"].fillna(0).astype(int)
    return quadro.sort_values("parados", ascending=False)


def _mensagem_hub(base: str, owner: str, grave: pd.DataFrame, as_of: pd.Timestamp, destino: str, n_destino: int) -> str:
    dias = grave["dias"]
    lines = [
        "🏛 *PARADO NO HUB*",
        "",
        f"Hub: *{base}* | Carteira: *{owner}* | Corte: *{as_of.strftime('%d/%m/%Y')}*",
        (
            f"Rastreio parado no hub: *{len(grave)}*  "
            f"(50d: {int((dias == 50).sum())} · 30d: {int((dias == 30).sum())} · 20d: {int((dias == 20).sum())})"
        ),
        "",
        "Esse volume morreu no transbordo. Não entra na cobrança da base de rua.",
    ]
    if destino:
        lines.extend(["", f"Destino que mais trava: *{destino}* ({n_destino} AJ). Ação é redespacho ou transbordo, em lote."])
    return "\n".join(lines)


def build_perda_rastreio(
    frame: pd.DataFrame,
    *,
    as_of: pd.Timestamp | str,
    min_days: int = CHARGE_MIN_DAYS,
    min_base: int = MIN_BASE,
    responsavel: str | None = None,
    responsabilidade: pd.DataFrame | None = None,
) -> PerdaRastreioRoutine:
    corte = pd.Timestamp(as_of).normalize()
    work = frame.copy()
    if responsabilidade is not None:
        work = attach_carteira(work, responsabilidade)
    if responsavel:
        needle = responsavel.casefold()
        work = work.loc[work["resp"].astype(str).str.casefold().eq(needle)].copy()
    work["dias"] = pd.to_numeric(work["dias"], errors="coerce")
    work["is_hub"] = work["ponto"].astype("string").str.contains(HUB_POINT_RE, na=False)
    grave_all = work.loc[work["dias"] >= min_days].copy()
    grave_all = grave_all.sort_values(["dias", "track_at", "waybill"], ascending=[False, True, True])
    recente = work.loc[work["dias"] < min_days]
    grave = grave_all.loc[~grave_all["is_hub"]].copy()
    no_hub = grave_all.loc[grave_all["is_hub"]].copy()

    summary = _ranking(grave)
    hubs = _hub_quadro(no_hub)
    messages: dict[str, str] = {}
    blocos: dict[str, list[str]] = {}
    ofensoras = summary.loc[summary["parados"] >= min_base, "ponto"] if not summary.empty else []
    for base in ofensoras:
        parte = grave.loc[grave["ponto"].eq(base)]
        owner = str(parte["resp"].iloc[0])
        n_recente = int((recente["ponto"] == base).sum())
        texto = _mensagem(base, owner, parte, n_recente, corte)
        messages[str(base)] = texto
        blocos.setdefault(owner, []).append(texto)
    owner_messages = {owner: "\n\n".join(partes) for owner, partes in blocos.items()}
    hub_messages: dict[str, str] = {}
    for row in hubs.itertuples(index=False):
        parte = no_hub.loc[no_hub["ponto"].eq(row.ponto)]
        hub_messages[str(row.ponto)] = _mensagem_hub(
            str(row.ponto),
            str(row.resp),
            parte,
            corte,
            str(row.destino),
            int(row.n_destino),
        )
    tail = summary.loc[summary["parados"] < min_base].copy() if not summary.empty else summary
    return PerdaRastreioRoutine(
        as_of=corte,
        detail=work.reset_index(drop=True),
        grave=grave.reset_index(drop=True),
        summary=summary.reset_index(drop=True),
        tail=tail.reset_index(drop=True),
        messages=messages,
        owner_messages=owner_messages,
        hubs=hubs.reset_index(drop=True),
        hub_messages=hub_messages,
    )
