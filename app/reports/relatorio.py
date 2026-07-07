"""Relatorio de conferencia (Excel) a partir dos XMLs baixados.

Le as notas em  PASTA_SAIDA/<Cliente>/{Emitidas,Recebidas,Canceladas}/*.xml,
extrai TODOS os campos fiscais relevantes (valores, base de calculo, aliquota,
ISS, PIS, COFINS, IRRF, CSLL, INSS, retencoes, deducoes, descricao do servico,
codigos de tributacao...) e gera:

  1. Um Excel POR CLIENTE  (<Cliente>/_Relatorio_<Cliente>.xlsx):
     uma aba por tipo (Emitidas/Recebidas/Canceladas), detalhada nota a nota,
     + uma aba Resumo com os totais e a CONFERENCIA de completude por NSU.

  2. Um Excel CONSOLIDADO (PASTA_SAIDA/_Relatorio_Consolidado.xlsx):
     uma linha por cliente com quantidade, valores e o veredito da conferencia.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

log = logging.getLogger("nfse.relatorio")

TIPOS = ("Emitidas", "Recebidas", "Canceladas")

# Catalogo de campos do relatorio detalhado.
#   (rotulo, largura, chave, tipo)  — tipo: "txt" | "money" | "pct" | "code"
# 'chave' e a coluna interna; para 'money'/'pct' o valor sai do XML como numero.
_CAMPOS = [
    ("Numero",            12, "numero",       "txt"),
    ("Competencia",       11, "competencia",  "txt"),
    ("Emissao",           11, "emissao",      "txt"),
    ("Prestador CNPJ/CPF", 17, "prest_doc",   "txt"),
    ("Prestador",         28, "prest_nome",   "txt"),
    ("Tomador CNPJ/CPF",  17, "toma_doc",     "txt"),
    ("Tomador",           28, "toma_nome",    "txt"),
    ("Municipio Incid.",  18, "municipio",    "txt"),
    ("Cod.Trib.Nac",      12, "cTribNac",     "txt"),
    ("Descricao Servico", 45, "xDescServ",    "txt"),
    ("Valor Servico",     14, "vServ",        "money"),
    ("Desc.Incond",       12, "vDescIncond",  "money"),
    ("Desc.Cond",         12, "vDescCond",    "money"),
    ("Deducoes",          12, "vDR",          "money"),
    ("Base Calculo",      14, "vBC",          "money"),
    ("Aliq ISS %",        10, "pAliqAplic",   "pct"),
    ("Valor ISS",         12, "vISSQN",       "money"),
    ("ISS Retido",        10, "issRetido",    "code"),
    ("PIS",               12, "vPis",         "money"),
    ("COFINS",            12, "vCofins",      "money"),
    ("IRRF",              12, "vRetIRRF",     "money"),
    ("CSLL",              12, "vRetCSLL",     "money"),
    ("INSS/CP",           12, "vRetCP",       "money"),
    ("Total Retencoes",   14, "vTotalRet",    "money"),
    ("Valor Liquido",     14, "vLiq",         "money"),
    ("Situacao",          11, "situacao",     "txt"),
    ("Chave de Acesso",   46, "chave",        "txt"),
    ("Arquivo",           38, "arquivo",      "txt"),
]
_MONEY = [c[2] for c in _CAMPOS if c[3] == "money"]
# Impostos mostrados no Resumo (rotulo -> chave)
_RESUMO_IMPOSTOS = [
    ("Valor Servico", "vServ"), ("Base Calculo", "vBC"), ("Valor ISS", "vISSQN"),
    ("PIS", "vPis"), ("COFINS", "vCofins"), ("IRRF", "vRetIRRF"),
    ("CSLL", "vRetCSLL"), ("INSS/CP", "vRetCP"),
    ("Total Retencoes", "vTotalRet"), ("Valor Liquido", "vLiq"),
]


# ───────────────────────── parsing do XML ───────────────────────────

def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _txt(root, *nomes: str) -> str:
    if root is None:
        return ""
    alvo = {n.lower() for n in nomes}
    for el in root.iter():
        if _localname(el.tag).lower() in alvo and (el.text or "").strip():
            return el.text.strip()
    return ""


def _num(root, *nomes: str) -> float:
    v = _txt(root, *nomes)
    try:
        return float(v) if v else 0.0
    except ValueError:
        return 0.0


def _bloco(root, *nomes: str):
    alvo = {n.lower() for n in nomes}
    for el in root.iter():
        if _localname(el.tag).lower() in alvo:
            return el
    return None


_INVALIDO = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _sanitizar(nome: str) -> str:
    return _INVALIDO.sub("", nome or "").strip(". ")[:80] or "cliente"


def extrair_nota(xml_path: Path, situacao: str = "") -> dict | None:
    """Le um XML de NFS-e e devolve um dict {chave_do_campo: valor} com todos
    os campos fiscais do relatorio. None se nao parsear."""
    try:
        root = ET.fromstring(xml_path.read_text(encoding="utf-8", errors="replace"))
    except (ET.ParseError, OSError):
        return None
    emit = _bloco(root, "emit", "prest")
    toma = _bloco(root, "toma", "tomador")
    inf = _bloco(root, "infNFSe")
    compet = _txt(root, "dCompet")
    emi = _txt(root, "dhEmi", "dhProc", "dhEvento")
    chave = (inf.get("Id") if inf is not None else "") or ""
    tp_ret_iss = _txt(root, "tpRetISSQN")
    return {
        "numero": _txt(root, "nNFSe", "nDPS"),
        "competencia": compet[:7] if compet else (emi[:7] if emi else ""),
        "emissao": emi[:10] if emi else "",
        "prest_doc": _txt(emit, "CNPJ", "CPF", "NIF"),
        "prest_nome": _txt(emit, "xNome", "xRazSoc", "xFant"),
        "toma_doc": _txt(toma, "CNPJ", "CPF", "NIF"),
        "toma_nome": _txt(toma, "xNome", "xRazSoc", "xFant"),
        "municipio": _txt(root, "xLocIncid", "xLocEmi"),
        "cTribNac": _txt(root, "cTribNac"),
        "xDescServ": _txt(root, "xDescServ", "xTribNac"),
        "vServ": _num(root, "vServ"),
        "vDescIncond": _num(root, "vDescIncond"),
        "vDescCond": _num(root, "vDescCond"),
        "vDR": _num(root, "vDR"),
        "vBC": _num(root, "vBC"),
        "pAliqAplic": _num(root, "pAliqAplic"),
        "vISSQN": _num(root, "vISSQN"),
        "issRetido": "Sim" if tp_ret_iss == "1" else ("Nao" if tp_ret_iss else ""),
        "vPis": _num(root, "vPis"),
        "vCofins": _num(root, "vCofins"),
        "vRetIRRF": _num(root, "vRetIRRF"),
        "vRetCSLL": _num(root, "vRetCSLL"),
        "vRetCP": _num(root, "vRetCP"),
        "vTotalRet": _num(root, "vTotalRet"),
        "vLiq": _num(root, "vLiq"),
        "situacao": situacao or "Normal",
        "chave": re.sub(r"\D", "", chave),
        "arquivo": xml_path.name,
    }


# ───────────────────────── montagem do Excel ────────────────────────

_HDR_FILL = PatternFill("solid", fgColor="1F4E78")
_HDR_FONT = Font(bold=True, color="FFFFFF")
_TOT_FILL = PatternFill("solid", fgColor="D9E1F2")
_TOT_FONT = Font(bold=True)
_TITULO_FONT = Font(bold=True, size=13)
_VERDE, _VERMELHO = "1E7D34", "C00000"


def _escrever_aba(ws, notas: list[dict]) -> dict:
    """Preenche uma aba com as notas (detalhadas) + linha de TOTAL.
    Retorna {qtd, <chave_money>: soma, ...}."""
    rotulos = [c[0] for c in _CAMPOS]
    ws.append(rotulos)
    for ci, (rot, larg, _k, _t) in enumerate(_CAMPOS, start=1):
        cell = ws.cell(row=1, column=ci)
        cell.fill = _HDR_FILL
        cell.font = _HDR_FONT
        cell.alignment = Alignment(horizontal="center")
        ws.column_dimensions[get_column_letter(ci)].width = larg

    tot = {"qtd": 0}
    for k in _MONEY:
        tot[k] = 0.0
    for nota in notas:
        ws.append([nota.get(c[2], "") for c in _CAMPOS])
        tot["qtd"] += 1
        for k in _MONEY:
            tot[k] += float(nota.get(k) or 0)

    # formato numerico nas colunas de valor/percentual
    fmt_idx = [(i + 1, c[3]) for i, c in enumerate(_CAMPOS) if c[3] in ("money", "pct")]
    for r in range(2, ws.max_row + 1):
        for ci, _t in fmt_idx:
            ws.cell(row=r, column=ci).number_format = "#,##0.00"

    # linha TOTAL
    linha = [""] * len(_CAMPOS)
    linha[0] = f"TOTAL ({tot['qtd']})"
    for i, c in enumerate(_CAMPOS):
        if c[3] == "money":
            linha[i] = tot[c[2]]
    ws.append(linha)
    rt = ws.max_row
    for ci in range(1, len(_CAMPOS) + 1):
        ws.cell(row=rt, column=ci).fill = _TOT_FILL
        ws.cell(row=rt, column=ci).font = _TOT_FONT
    for ci, _t in fmt_idx:
        ws.cell(row=rt, column=ci).number_format = "#,##0.00"
    ws.freeze_panes = "A2"
    return tot


@dataclass
class ResumoCliente:
    nome: str = ""
    cnpj: str = ""
    totais: dict = field(default_factory=dict)       # tipo -> {qtd, vServ, ...}
    conferencia: dict = field(default_factory=dict)  # {total_caixa, recebidos, faltando, completo}


def _veredito(conf: dict) -> tuple[str, str]:
    if not conf:
        return "—", "808080"
    if conf.get("completo", True):
        return "BATEU — caixa completa, sem notas faltando", _VERDE
    faltam = conf.get("faltando") or []
    return f"NAO BATEU — faltam {len(faltam)} NSU: {', '.join(map(str, faltam[:20]))}", _VERMELHO


def gerar_excel_cliente(pasta_cliente: Path, nome_cliente: str, cnpj: str = "",
                        conferencia: dict | None = None) -> ResumoCliente | None:
    """Gera o Excel detalhado de UM cliente. Retorna o resumo (totais por tipo)
    ou None se nao houver nenhuma nota."""
    pasta_cliente = Path(pasta_cliente)
    resumo = ResumoCliente(nome=nome_cliente, cnpj=cnpj, conferencia=conferencia or {})
    wb = Workbook()
    wb.remove(wb.active)

    houve = False
    for tipo in TIPOS:
        sub = pasta_cliente / tipo
        if not sub.is_dir():
            continue
        situacao = "Cancelada" if tipo == "Canceladas" else "Normal"
        notas = []
        for xml in sorted(sub.glob("*.xml")):
            if xml.name.lower().startswith("cancelamento"):
                continue  # XML do evento (nao e a nota) — nao entra nos valores
            nota = extrair_nota(xml, situacao=situacao)
            if nota is not None:
                notas.append(nota)
        if not notas:
            continue
        houve = True
        ws = wb.create_sheet(title=tipo)
        resumo.totais[tipo] = _escrever_aba(ws, notas)

    if not houve:
        return None

    _montar_resumo(wb, resumo)

    destino = pasta_cliente / f"_Relatorio_{_sanitizar(nome_cliente)}.xlsx"
    try:
        wb.save(destino)
        log.info("[%s] Relatorio gerado: %s", cnpj or nome_cliente, destino.name)
    except OSError as exc:
        log.warning("Falha ao salvar relatorio de %s: %s", nome_cliente, exc)
    return resumo


def _montar_resumo(wb: Workbook, resumo: ResumoCliente) -> None:
    """Aba Resumo: totais de impostos por tipo + conferencia de completude."""
    ws = wb.create_sheet(title="Resumo", index=0)
    ws["A1"] = f"Relatorio de conferencia — {resumo.nome}"
    ws["A1"].font = _TITULO_FONT
    if resumo.cnpj:
        ws["A2"] = f"CNPJ: {resumo.cnpj}"
    ws.append([])

    cab = ["Tipo", "Qtd"] + [r[0] for r in _RESUMO_IMPOSTOS]
    ws.append(cab)
    hr = ws.max_row
    for ci in range(1, len(cab) + 1):
        ws.cell(row=hr, column=ci).fill = _HDR_FILL
        ws.cell(row=hr, column=ci).font = _HDR_FONT
    for tipo in TIPOS:
        t = resumo.totais.get(tipo)
        if not t:
            continue
        ws.append([tipo, t["qtd"]] + [t.get(chave, 0) for _rot, chave in _RESUMO_IMPOSTOS])
    for r in range(hr + 1, ws.max_row + 1):
        for ci in range(3, len(cab) + 1):
            ws.cell(row=r, column=ci).number_format = "#,##0.00"
    ws.column_dimensions["A"].width = 13
    ws.column_dimensions["B"].width = 7
    for ci in range(3, len(cab) + 1):
        ws.column_dimensions[get_column_letter(ci)].width = 15

    # Bloco CONFERENCIA (completude por NSU) — veredito BATEU / NAO BATEU
    if resumo.conferencia:
        conf = resumo.conferencia
        ws.append([])
        ws.append(["Conferencia (completude por NSU)"])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True, size=12)
        ws.append(["Total de documentos no ADN", conf.get("total_caixa", 0)])
        ws.append(["Documentos recebidos", conf.get("recebidos", 0)])
        ws.append(["NSU faltando", len(conf.get("faltando") or [])])
        texto, cor = _veredito(conf)
        ws.append([texto])
        cell = ws.cell(row=ws.max_row, column=1)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=cor)


def gerar_consolidado(base_saida: Path, resumos: list[ResumoCliente]) -> Path | None:
    """Excel consolidado: uma linha por cliente com qtd, valores e veredito."""
    resumos = [r for r in resumos if r and r.totais]
    if not resumos:
        return None
    wb = Workbook()
    ws = wb.active
    ws.title = "Consolidado"
    ws["A1"] = "Relatorio Consolidado — NFS-e por cliente"
    ws["A1"].font = _TITULO_FONT
    ws.append([])

    cab = ["Cliente", "CNPJ", "Emit Qtd", "Emit Valor", "Receb Qtd", "Receb Valor",
           "Canc Qtd", "Canc Valor", "Total Notas", "Total Servico",
           "Total ISS", "Total Retencoes", "Conferencia"]
    ws.append(cab)
    hr = ws.max_row
    for ci in range(1, len(cab) + 1):
        ws.cell(row=hr, column=ci).fill = _HDR_FILL
        ws.cell(row=hr, column=ci).font = _HDR_FONT

    def _g(r: ResumoCliente, tipo: str, chave: str):
        return (r.totais.get(tipo) or {}).get(chave, 0)

    soma = {"qtd": 0, "serv": 0.0}
    divergentes = 0
    for r in sorted(resumos, key=lambda x: x.nome.lower()):
        e_q, e_v = _g(r, "Emitidas", "qtd"), _g(r, "Emitidas", "vServ")
        r_q, r_v = _g(r, "Recebidas", "qtd"), _g(r, "Recebidas", "vServ")
        c_q, c_v = _g(r, "Canceladas", "qtd"), _g(r, "Canceladas", "vServ")
        tot_q = e_q + r_q + c_q
        tot_v = e_v + r_v + c_v
        iss = _g(r, "Emitidas", "vISSQN") + _g(r, "Recebidas", "vISSQN") + _g(r, "Canceladas", "vISSQN")
        ret = _g(r, "Emitidas", "vTotalRet") + _g(r, "Recebidas", "vTotalRet") + _g(r, "Canceladas", "vTotalRet")
        soma["qtd"] += tot_q
        soma["serv"] += tot_v
        completo = (r.conferencia or {}).get("completo", True)
        falt = len((r.conferencia or {}).get("faltando") or [])
        verdito = "BATEU" if completo else f"NAO BATEU ({falt} NSU)"
        if not completo:
            divergentes += 1
        ws.append([r.nome, r.cnpj, e_q, e_v, r_q, r_v, c_q, c_v, tot_q, tot_v, iss, ret, verdito])
        cv = ws.cell(row=ws.max_row, column=len(cab))
        cv.font = Font(bold=True, color="FFFFFF")
        cv.fill = PatternFill("solid", fgColor=(_VERDE if completo else _VERMELHO))

    ws.append(["TOTAL GERAL", "", "", "", "", "", "", "", soma["qtd"], soma["serv"], "", "",
               "TODOS OK" if divergentes == 0 else f"{divergentes} divergente(s)"])
    rt = ws.max_row
    for ci in range(1, len(cab) + 1):
        ws.cell(row=rt, column=ci).fill = _TOT_FILL
        ws.cell(row=rt, column=ci).font = _TOT_FONT

    for r in range(hr + 1, ws.max_row + 1):
        for ci in (4, 6, 8, 10, 11, 12):
            ws.cell(row=r, column=ci).number_format = "#,##0.00"
    for ci, larg in enumerate((34, 18, 9, 14, 9, 14, 9, 14, 11, 15, 14, 15, 20), start=1):
        ws.column_dimensions[get_column_letter(ci)].width = larg
    ws.freeze_panes = "A4"

    destino = Path(base_saida) / "_Relatorio_Consolidado.xlsx"
    try:
        wb.save(destino)
        log.info("Relatorio consolidado gerado: %s (%d cliente(s))", destino, len(resumos))
    except OSError as exc:
        log.warning("Falha ao salvar consolidado: %s", exc)
        return None
    return destino
