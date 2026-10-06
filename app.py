import streamlit as st
import pandas as pd
import camelot
import re
import os
import hashlib
import tempfile
import unicodedata
from io import BytesIO

st.set_page_config(page_title="Verificador AE x Diários", layout="wide")
st.title("🔍 Verificador de Lançamento de Notas — Avaliação Especial")

st.markdown("""
**Fluxo:** 1️⃣ envie a planilha de solicitações → 2️⃣ o sistema mostra quais diários
precisam ser enviados → 3️⃣ faça upload dos diários → 4️⃣ rode a verificação.
""")

COLUNAS_NOTAS = ["AP1/AV1", "AP2/AV2", "TE", "AE", "ND", "TOTAL PARCIAL", "FINAL"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def normalizar(s):
    if s is None:
        return ""
    s = str(s).upper().strip()
    s = unicodedata.normalize("NFKD", s).encode("ASCII", "ignore").decode("ASCII")
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalizar_codigo(v):
    if pd.isna(v):
        return ""
    try:
        return str(int(float(v)))
    except (ValueError, TypeError):
        return re.sub(r"\D", "", str(v))


def valor_em_branco(valor):
    if valor is None:
        return True
    s = str(valor).strip()
    if s in ("", "-", "--", "nan", "NaN", "None"):
        return True
    s_norm = s.replace(",", ".").replace(" ", "")
    try:
        return float(s_norm) == 0.0
    except ValueError:
        return False


def extrair_tabela_notas(df_bruto):
    linhas_alunos = []
    for _, row in df_bruto.iterrows():
        primeira = str(row.iloc[0]).strip()
        if re.match(r"^\d{1,3}$", primeira):
            linhas_alunos.append(row)

    if not linhas_alunos:
        return None

    n_cols = df_bruto.shape[1]
    if n_cols < 9:
        return None

    registros = []
    for row in linhas_alunos:
        matricula = re.sub(r"\D", "", str(row.iloc[1]))
        nome = str(row.iloc[2]).strip()
        if not matricula:
            continue
        notas = row.iloc[n_cols - 7:n_cols].tolist()
        reg = {"MATRICULA": matricula, "NOME": nome}
        for nome_col, val in zip(COLUNAS_NOTAS, notas):
            reg[nome_col] = val
        registros.append(reg)

    return pd.DataFrame(registros) if registros else None


def eh_tabela_de_notas(df_bruto):
    texto = " ".join(df_bruto.astype(str).values.flatten()).upper()
    return "AV1/AP1" in texto or "AP1/AV1" in texto


@st.cache_data(show_spinner=False)
def extrair_notas_pdf(pdf_bytes: bytes) -> dict:
    """Retorna {matricula: {coluna: valor}} extraído do PDF."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name
    try:
        tables = camelot.read_pdf(
            tmp_path, pages="all", flavor="stream", strip_text="\n"
        )
        notas = {}
        for t in tables:
            if not eh_tabela_de_notas(t.df):
                continue
            df = extrair_tabela_notas(t.df)
            if df is None:
                continue
            for _, row in df.iterrows():
                notas[row["MATRICULA"]] = row.to_dict()
        return notas
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# ETAPA 1 — Upload da planilha
# ---------------------------------------------------------------------------
st.header("1️⃣ Lista de solicitações")
uploaded_excel = st.file_uploader(
    "Envie o arquivo Excel com as solicitações", type=["xlsx"], key="excel"
)

if not uploaded_excel:
    st.info("⬆️ Envie a planilha para começar.")
    st.stop()

# Hash para invalidar uploads se a planilha mudar
excel_hash = hashlib.md5(uploaded_excel.getvalue()).hexdigest()[:8]

df_excel = pd.read_excel(uploaded_excel)
disc_cols = [c for c in df_excel.columns if "Disciplina" in str(c)]

solicitacoes = []
for _, row in df_excel.iterrows():
    matricula = normalizar_codigo(row.get("Matrícula"))
    nome = str(row.get("Nome do Aluno", "")).strip()
    turma = normalizar_codigo(row.get("Turma"))
    etapa = str(row.get("Etapa", "")).strip()

    for i, col in enumerate(disc_cols, start=1):
        val = row.get(col)
        if pd.isna(val) or str(val).strip() == "":
            continue
        disc_str = str(val).strip()
        if " - " in disc_str:
            partes = disc_str.split(" - ", 1)
            disc_code = partes[0].strip()
            disc_name = partes[1].strip()
        else:
            disc_code = ""
            disc_name = disc_str

        solicitacoes.append({
            "matricula": matricula,
            "nome": nome,
            "turma": turma,
            "etapa": etapa,
            "posicao": i,
            "disciplina_raw": disc_str,
            "disciplina_code": disc_code,
            "disciplina_name": disc_name,
        })

if not solicitacoes:
    st.error("Nenhuma solicitação encontrada na planilha.")
    st.stop()

# ---------------------------------------------------------------------------
# ETAPA 2 — Diários necessários
# ---------------------------------------------------------------------------
st.header("2️⃣ Diários que devem ser enviados")

# Agrupa (turma, codigo) -> {nome, quantidade}
necessarios = {}
for sol in solicitacoes:
    key = (sol["turma"], sol["disciplina_code"])
    if key not in necessarios:
        necessarios[key] = {"nome": sol["disciplina_name"], "qtd": 0}
    necessarios[key]["qtd"] += 1

# Tabela resumo geral
linhas_resumo = []
for (turma, code), info in sorted(necessarios.items()):
    linhas_resumo.append({
        "Turma": turma,
        "Disciplina": info["nome"],
        "Código": code or "—",
        "Solicitações": info["qtd"],
    })
df_resumo = pd.DataFrame(linhas_resumo)
st.dataframe(df_resumo, use_container_width=True, hide_index=True)

st.caption(
    f"📊 {len(solicitacoes)} solicitação(ões) em "
    f"{len(necessarios)} diário(s) de {df_resumo['Turma'].nunique()} turma(s)."
)

# ---------------------------------------------------------------------------
# ETAPA 3 — Upload por turma/disciplina
# ---------------------------------------------------------------------------
st.header("3️⃣ Faça upload dos diários")
st.markdown(
    "Os campos estão agrupados por **turma**. Envie o PDF correspondente a cada disciplina."
)

diarios_carregados = {}
turmas = sorted(set(s["turma"] for s in solicitacoes))

for turma in turmas:
    discs_turma = sorted(
        [(c, d) for (t, c), d in necessarios.items() if t == turma],
        key=lambda x: x[1]["nome"],
    )
    with st.expander(f"📘 Turma {turma} — {len(discs_turma)} diário(s)", expanded=True):
        for code, info in discs_turma:
            up = st.file_uploader(
                f"**{info['nome']}** ({info['qtd']} solicitações) — código {code or '—'}",
                type=["pdf"],
                key=f"pdf_{excel_hash}_{turma}_{code}",
            )
            if up is not None:
                diarios_carregados[(turma, code)] = {
                    "arquivo": up,
                    "nome": info["nome"],
                }

faltando = len(necessarios) - len(diarios_carregados)
if faltando > 0:
    st.warning(
        f"⚠️ Faltam **{faltando}** diário(s). "
        "Você pode rodar a verificação mesmo assim — eles aparecerão como "
        "*Diário não enviado*."
    )
else:
    st.success("✅ Todos os diários necessários foram enviados!")

# ---------------------------------------------------------------------------
# ETAPA 4 — Verificação
# ---------------------------------------------------------------------------
st.header("4️⃣ Rodar verificação")

if st.button("▶️ Rodar verificação", type="primary"):

    # -------- Processa PDFs --------
    diarios_notas = {}
    with st.spinner("Lendo diários..."):
        for (turma, code), info in diarios_carregados.items():
            try:
                notas = extrair_notas_pdf(info["arquivo"].getvalue())
                diarios_notas[(turma, code)] = notas
            except Exception as e:
                st.warning(f"⚠️ Erro em {turma}/{info['nome']}: {e}")

    # -------- Verifica cada solicitação --------
    for sol in solicitacoes:
        key = (sol["turma"], sol["disciplina_code"])
        if key not in diarios_notas:
            sol["status"] = "Diário não enviado"
            continue
        notas = diarios_notas[key]
        n_aluno = notas.get(sol["matricula"])
        if n_aluno is None:
            for mat, n in notas.items():
                if mat.lstrip("0") == sol["matricula"].lstrip("0"):
                    n_aluno = n
                    break
        if n_aluno is None:
            sol["status"] = "Aluno não encontrado"
        else:
            ae = n_aluno.get("AE", "")
            sol["status"] = "Não" if valor_em_branco(ae) else "Sim"

    # -------- Consolida por aluno --------
    alunos = {}
    for sol in solicitacoes:
        key = (sol["turma"], sol["matricula"])
        if key not in alunos:
            alunos[key] = {
                "Matrícula": sol["matricula"],
                "Nome do Aluno": sol["nome"],
                "Turma": sol["turma"],
                "Etapa": sol["etapa"],
                "disciplinas": {},
            }
        alunos[key]["disciplinas"][sol["posicao"]] = sol

    linhas = []
    for _, alu in alunos.items():
        linha = {
            "Matrícula": alu["Matrícula"],
            "Nome do Aluno": alu["Nome do Aluno"],
            "Turma": alu["Turma"],
            "Etapa": alu["Etapa"],
        }
        for i in range(1, 5):
            disc = alu["disciplinas"].get(i)
            if disc:
                linha[f"{i}ª Disciplina"] = disc["disciplina_raw"]
                linha[f"Lançou a Nota da {i}ª Disciplina? (Sim / Não)"] = disc["status"]
            else:
                linha[f"{i}ª Disciplina"] = ""
                linha[f"Lançou a Nota da {i}ª Disciplina? (Sim / Não)"] = ""
        linhas.append(linha)

    df_result = (
        pd.DataFrame(linhas)
        .sort_values(by=["Turma", "Nome do Aluno"])
        .reset_index(drop=True)
    )

    # -------- Resumo --------
    st.markdown("---")
    st.subheader("📈 Resumo")

    total_sol = len(solicitacoes)
    total_sim = sum(1 for s in solicitacoes if s["status"] == "Sim")
    total_nao = sum(1 for s in solicitacoes if s["status"] == "Não")
    total_sem_diario = sum(1 for s in solicitacoes if s["status"] == "Diário não enviado")
    total_sem_aluno = sum(1 for s in solicitacoes if s["status"] == "Aluno não encontrado")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total de solicitações", total_sol)
    c2.metric("✅ Lançadas", total_sim)
    c3.metric("❌ Não lançadas", total_nao)
    c4.metric("📄 Diário não enviado", total_sem_diario)
    c5.metric("👤 Aluno não encontrado", total_sem_aluno)

    # -------- Resultado por turma --------
    st.markdown("---")
    st.subheader("📋 Resultado por turma (ordem alfabética)")
    for turma in sorted(df_result["Turma"].unique()):
        grupo = df_result[df_result["Turma"] == turma].reset_index(drop=True)
        st.markdown(f"### Turma {turma}")
        st.dataframe(grupo, use_container_width=True, hide_index=True)

    # -------- Excel --------
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df_result.to_excel(writer, sheet_name="Todas as Turmas", index=False)
        for turma in sorted(df_result["Turma"].unique()):
            grupo = df_result[df_result["Turma"] == turma].copy()
            sheet_name = f"Turma {turma}"[:31]
            grupo.to_excel(writer, sheet_name=sheet_name, index=False)

    st.download_button(
        label="📥 Baixar resultado da varredura (Excel)",
        data=output.getvalue(),
        file_name="resultado_varredura_avaliacao_especial.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
