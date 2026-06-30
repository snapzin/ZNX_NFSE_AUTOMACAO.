"""Relatorio de conferencia (Excel) a partir dos XMLs baixados.

Le as notas em  PASTA_SAIDA/<Cliente>/{Emitidas,Recebidas,Canceladas}/*.xml,
extrai os valores (servico, ISS, retencoes, liquido) e gera:

  1. Um Excel POR CLIENTE  (<Cliente>/_Relatorio_<Cliente>.xlsx):
     uma aba por tipo (Emitidas/Recebidas/Canceladas) + uma aba Resumo com os
     totais. E o mesmo "Relatorio" que a extensao oficial mostra, so que feito
     direto dos XMLs (sem navegador).

  2. Um Excel CONSOLIDADO (PASTA_SAIDA/_Relatorio_Consolidado.xlsx):
     uma linha por cliente com a quantidade e o valor total de cada tipo —
     para conferir num relance se os valores batem com as notas.
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

# Colunas do relatorio por nota (rotulo, largura)
_COLUNAS = [
    ("Numero", 14), ("Competencia", 12),
    ("Prestador CNPJ", 18), ("Prestador", 30),
    ("Tomador CNPJ", 18), ("Tomador", 30),
    ("Valor Servico", 14), ("Descontos", 12), ("ISS", 12),
    ("Retencoes", 12), ("Valor Liquido", 14),
    ("Arquivo", 40),
]
_COLS_VALOR = {"Valor Servico", "Descontos", "ISS", "Retencoes", "Valor Liquido"}


# ───────────────────────── parsing dos valores ──────────────────────

def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _txt(root: ET.Element, *nomes: str) -> str:
    alvo = {n.lower() for n in nomes}
    for el in root.iter():
        if _localname(el.tag).lower() in alvo and (el.text or "").strip():
            return el.text.strip()
    return ""


def _num(root: ET.Element, *nomes: str) -> float:
    v = _txt(root, *nomes)
    try:
        return float(v) if v else 0.0
    except ValueError:
        return 0.0


def _bloco(root: ET.Element, *nomes: str):
    alvo = {n.lower() for n in nomes}
    for el in root.iter():
        if _localname(el.tag).lower() in alvo:
            return el
    return None


_INVALIDO = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _sanitizar(nome: str) -> str:
    return _INVALIDO.sub("", nome or "").strip(". ")[:80] or "cliente"


@dataclass
class LinhaNota:
    numero: str = ""
    competencia: str = ""
    prestador_cnpj: str = ""
    prestador_nome: str = ""
    tomador_cnpj: str = ""
    tomador_nome: str = ""
    valor_servico: float = 0.0
    descontos: float = 0.0
    iss: float = 0.0
    retencoes: float = 0.0
    valor_liquido: float = 0.0
    arquivo: str = ""

    def como_linha(self) -> list:
        return [
            self.numero, self.competencia,
            self.prestador_cnpj, self.prestador_nome,
            self.tomador_cnpj, self.tomador_nome,
            self.valor_servico, self.descontos, self.iss,
            self.retencoes, self.valor_liquido,
            self.arquivo,
        ]


def extrair_linha(xml_path: Path) -> LinhaNota | None:
    """Le um XML de NFS-e e extrai os campos de valor para o relatorio."""
    try:
        root = ET.fromstring(xml_path.read_text(encoding="utf-8", errors="replace"))
    except (ET.ParseError, OSError):
        return None
    emit = _bloco(root, "emit", "prest")
    toma = _bloco(root, "toma", "tomador")
    data_compet = _txt(root, "dCompet")
    data_emi = _txt(root, "dhEmi", "dhProc", "dhEvento")
    return LinhaNota(
        numero=_txt(root, "nNFSe", "nDPS"),
        competencia=(data_compet[:7] if data_compet else (data_emi[:10] if data_emi else "")),
        prestador_cnpj=_txt(emit, "CNPJ", "CPF") if emit is not None else "",
        prestador_nome=_txt(emit, "xNome", "xRazSoc", "xFant") if emit is not None else "",
        tomador_cnpj=_txt(toma, "CNPJ", "CPF") if toma is not None else "",
        tomador_nome=_txt(toma, "xNome", "xRazSoc", "xFant") if toma is not None else "",
        # vServ pode estar em <vServPrest> ou <valores>; pega o primeiro encontrado.
        valor_servico=_num(root, "vServ"),
        descontos=_num(root, "vDescIncond"),
        iss=_num(root, "vISSQN"),
        retencoes=_num(root, "vTotalRet"),
        valor_liquido=_num(root, "vLiq"),
        arquivo=xml_path.name,
    )


# ───────────────────────── montagem do Excel ────────────────────────

_HDR_FILL = PatternFill("solid", fgColor="1F4E78")
_HDR_FONT = Font(bold=True, color="FFFFFF")
_TOT_FILL = PatternFill("solid", fgColor="D9E1F2")
_TOT_FONT = Font(bold=True)
_TITULO_FONT = Font(bold=True, size=13)


def _escrever_aba(ws, titulo: str, linhas: list[LinhaNota]) -> dict:
    """Preenche uma aba com as notas de um tipo + linha de totais.
    Retorna os totais {qtd, valor_servico, iss, retencoes, valor_liquido}."""
    rotulos = [c[0] for c in _COLUNAS]
    ws.append(rotulos)
    for ci, (rot, larg) in enumerate(_COLUNAS, start=1):
        cell = ws.cell(row=1, column=ci)
        cell.fill = _HDR_FILL
        cell.font = _HDR_FONT
        cell.alignment = Alignment(horizontal="center")
        ws.column_dimensions[get_column_letter(ci)].width = larg

    tot = {"qtd": 0, "valor_servico": 0.0, "iss": 0.0, "retencoes": 0.0, "valor_liquido": 0.0}
    for ln in linhas:
        ws.append(ln.como_linha())
        tot["qtd"] += 1
        tot["valor_servico"] += ln.valor_servico
        tot["iss"] += ln.iss
        tot["retencoes"] += ln.retencoes
        tot["valor_liquido"] += ln.valor_liquido

    # formata as colunas de valor como numero com 2 casas
    idx_valor = [i + 1 for i, c in enumerate(_COLUNAS) if c[0] in _COLS_VALOR]
    for r in range(2, ws.max_row + 1):
        for ci in idx_valor:
            ws.cell(row=r, column=ci).number_format = "#,##0.00"

    # linha de TOTAL
    linha_total = [""] * len(_COLUNAS)
    linha_total[0] = f"TOTAL ({tot['qtd']})"
    mapa = {"Valor Servico": "valor_servico", "ISS": "iss",
            "Retencoes": "retencoes", "Valor Liquido": "valor_liquido"}
    for i, (rot, _l) in enumerate(_COLUNAS):
        if rot in mapa:
            linha_total[i] = tot[mapa[rot]]
    ws.append(linha_total)
    rt = ws.max_row
    for ci in range(1, len(_COLUNAS) + 1):
        ws.cell(row=rt, column=ci).fill = _TOT_FILL
        ws.cell(row=rt, column=ci).font = _TOT_FONT
        if ci in idx_valor:
            ws.cell(row=rt, column=ci).number_format = "#,##0.00"
    ws.freeze_panes = "A2"
    return tot


@dataclass
class ResumoCliente:
    nome: str = ""
    cnpj: str = ""
    totais: dict = field(default_factory=dict)   # tipo -> {qtd, valor_servico, ...}
    conferencia: dict = field(default_factory=dict)  # {total_caixa, recebidos, faltando, completo}


def _veredito(conf: dict) -> tuple[str, str]:
    """Texto e cor do veredito de completude a partir da conferencia por NSU."""
    if not conf:
        return "—", "808080"
    if conf.get("completo", True):
        return "BATEU — caixa completa, sem notas faltando", "1E7D34"  # verde
    faltam = conf.get("faltando") or []
    return f"NAO BATEU — faltam {len(faltam)} NSU: {', '.join(map(str, faltam[:20]))}", "C00000"  # vermelho


def gerar_excel_cliente(pasta_cliente: Path, nome_cliente: str, cnpj: str = "",
                        conferencia: dict | None = None) -> ResumoCliente | None:
    """Gera o Excel de conferencia de UM cliente. Retorna o resumo (totais por
    tipo) ou None se nao houver nenhuma nota."""
    pasta_cliente = Path(pasta_cliente)
    resumo = ResumoCliente(nome=nome_cliente, cnpj=cnpj, conferencia=conferencia or {})
    wb = Workbook()
    wb.remove(wb.active)

    houve = False
    for tipo in TIPOS:
        sub = pasta_cliente / tipo
        if not sub.is_dir():
            continue
        # ignora o XML do evento de cancelamento na contagem de valores (nao tem vServ)
        linhas = []
        for xml in sorted(sub.glob("*.xml")):
            if xml.name.lower().startswith("cancelamento"):
                continue
            ln = extrair_linha(xml)
            if ln is not None:
                linhas.append(ln)
        if not linhas:
            continue
        houve = True
        ws = wb.create_sheet(title=tipo)
        resumo.totais[tipo] = _escrever_aba(ws, tipo, linhas)

    if not houve:
        return None

    # aba Resumo (primeira)
    ws = wb.create_sheet(title="Resumo", index=0)
    ws["A1"] = f"Relatorio de conferencia — {nome_cliente}"
    ws["A1"].font = _TITULO_FONT
    if cnpj:
        ws["A2"] = f"CNPJ: {cnpj}"
    ws.append([])
    ws.append(["Tipo", "Qtd", "Valor Servico", "ISS", "Retencoes", "Valor Liquido"])
    hr = ws.max_row
    for ci in range(1, 7):
        ws.cell(row=hr, column=ci).fill = _HDR_FILL
        ws.cell(row=hr, column=ci).font = _HDR_FONT
    for tipo in TIPOS:
        t = resumo.totais.get(tipo)
        if not t:
            continue
        ws.append([tipo, t["qtd"], t["valor_servico"], t["iss"], t["retencoes"], t["valor_liquido"]])
    for r in range(hr + 1, ws.max_row + 1):
        for ci in range(3, 7):
            ws.cell(row=r, column=ci).number_format = "#,##0.00"
    for ci, larg in enumerate((14, 8, 16, 14, 14, 16), start=1):
        ws.column_dimensions[get_column_letter(ci)].width = larg

    # Bloco de CONFERENCIA (completude por NSU) — o veredito BATEU / NAO BATEU
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

    destino = pasta_cliente / f"_Relatorio_{_sanitizar(nome_cliente)}.xlsx"
    try:
        wb.save(destino)
        log.info("[%s] Relatorio gerado: %s", cnpj or nome_cliente, destino.name)
    except OSError as exc:
        log.warning("Falha ao salvar relatorio de %s: %s", nome_cliente, exc)
        return resumo
    return resumo


def gerar_consolidado(base_saida: Path, resumos: list[ResumoCliente]) -> Path | None:
    """Gera o Excel consolidado: uma linha por cliente com qtd e valor por tipo."""
    resumos = [r for r in resumos if r and r.totais]
    if not resumos:
        return None
    wb = Workbook()
    ws = wb.active
    ws.title = "Consolidado"
    ws["A1"] = "Relatorio Consolidado — NFS-e por cliente"
    ws["A1"].font = _TITULO_FONT
    ws.append([])

    cab = ["Cliente", "CNPJ",
           "Emit Qtd", "Emit Valor", "Receb Qtd", "Receb Valor",
           "Canc Qtd", "Canc Valor", "Total Notas", "Total Servico", "Conferencia"]
    ws.append(cab)
    hr = ws.max_row
    for ci in range(1, len(cab) + 1):
        ws.cell(row=hr, column=ci).fill = _HDR_FILL
        ws.cell(row=hr, column=ci).font = _HDR_FONT

    def _g(r: ResumoCliente, tipo: str, campo: str):
        return (r.totais.get(tipo) or {}).get(campo, 0)

    soma = {"qtd": 0, "serv": 0.0}
    divergentes = 0
    for r in sorted(resumos, key=lambda x: x.nome.lower()):
        e_q, e_v = _g(r, "Emitidas", "qtd"), _g(r, "Emitidas", "valor_servico")
        r_q, r_v = _g(r, "Recebidas", "qtd"), _g(r, "Recebidas", "valor_servico")
        c_q, c_v = _g(r, "Canceladas", "qtd"), _g(r, "Canceladas", "valor_servico")
        tot_q = e_q + r_q + c_q
        tot_v = e_v + r_v + c_v
        soma["qtd"] += tot_q
        soma["serv"] += tot_v
        completo = (r.conferencia or {}).get("completo", True)
        falt = len((r.conferencia or {}).get("faltando") or [])
        verdito = "BATEU" if completo else f"NAO BATEU ({falt} NSU)"
        if not completo:
            divergentes += 1
        ws.append([r.nome, r.cnpj, e_q, e_v, r_q, r_v, c_q, c_v, tot_q, tot_v, verdito])
        cv = ws.cell(row=ws.max_row, column=len(cab))
        cv.font = Font(bold=True, color="FFFFFF")
        cv.fill = PatternFill("solid", fgColor=("1E7D34" if completo else "C00000"))

    ws.append(["TOTAL GERAL", "", "", "", "", "", "", "", soma["qtd"], soma["serv"],
               "TODOS OK" if divergentes == 0 else f"{divergentes} divergente(s)"])
    rt = ws.max_row
    for ci in range(1, len(cab) + 1):
        ws.cell(row=rt, column=ci).fill = _TOT_FILL
        ws.cell(row=rt, column=ci).font = _TOT_FONT

    for r in range(hr + 1, ws.max_row + 1):
        for ci in (4, 6, 8, 10):
            ws.cell(row=r, column=ci).number_format = "#,##0.00"
    for ci, larg in enumerate((34, 18, 9, 14, 9, 14, 9, 14, 12, 16, 22), start=1):
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
