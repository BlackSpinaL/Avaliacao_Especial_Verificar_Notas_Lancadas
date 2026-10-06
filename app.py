import streamlit as st
import pandas as pd
import camelot
import re
import os
import tempfile
import unicodedata
from io import BytesIO

st.set_page_config(page_title="Verificador AE x Diários", layout="wide")
st.title("🔍 Verificador de Lançamento de Notas — Avaliação Especial")

st.markdown("""
Cruza a **lista de solicitações de Avaliação Especial** (Excel) com os **diários em PDF**
e verifica se a nota da coluna **AE** já foi lançada para cada aluno solicitante.

**Como funciona o cruzamento:** Turma + Disciplina (código ou nome) + Matrícula do aluno.
""")

colunas_notas = ["AP1/AV1", "AP2/AV2", "TE", "AE", "ND", "TOTAL PARCIAL", "FINAL"]

uploaded_excel = st.file_uploader("📋 Lista de solicitações (Excel .xlsx)", type=["xlsx"])
uploaded_pdfs = st.file_uploader(
    "📄 Diários (PDFs) — pode enviar vários", type=["pdf"], accept_multiple_files=True
)


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
    """Garante que matrícula/turma viram string de dígitos, sem .0"""
    if pd.isna(v):
        return ""
    try:
        return str(int(float(v)))
    except (ValueError, TypeError):
        return re.sub(r"\D", "", str(v))


def valor_em_branco(valor):
    """True se o valor está vazio/zero (ou seja, AE não lançada)."""
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
    """Extrai as linhas dos alunos de uma tabela camelot."""
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
        for nome_col, val in zip(colunas_notas, notas):
            reg[nome_col] = val
        registros.append(reg)

    return pd.DataFrame(registros) if registros else None


def eh_tabela_de_notas(df_bruto):
    texto = " ".join(df_bruto.astype(str).values.flatten()).upper()
    return "AV1/AP1" in texto or "AP1/AV1" in texto


# ---------------------------------------------------------------------------
# Processamento do PDF
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def processar_pdf(pdf_bytes, filename):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name

    try:
        turma, disc_code, disciplina, professor = None, None, None, None

        # 1) Tenta extrair turma + código da disciplina do nome do arquivo
        #    Ex.: "Diário_Disciplina_Turma_ 12301_017.pdf"
        m = re.search(r"(\d{5})[\s_]+(\d{3})", filename)
        if m:
            turma = m.group(1)
            disc_code = m.group(2)

        # 2) Lê o cabeçalho da página 1
        header_tables = camelot.read_pdf(
            tmp_path, pages="1", flavor="stream", strip_text="\n"
        )
        texto_cab = ""
        for ht in header_tables:
            texto_cab += " " + " ".join(ht.df.astype(str).values.flatten())

        if not turma:
            m_t = re.search(r"TARDE\s+(\d{5})", texto_cab)
            if m_t:
                turma = m_t.group(1)

        m_d = re.search(r"DISCIPLINA\s+(.+?)\s+MESES", texto_cab, re.IGNORECASE)
        if m_d:
            disciplina = m_d.group(1).strip().upper()

        m_p = re.search(
            r"PROFESSOR\s+([A-ZÀ-Ú\s]+?)\s+(?:MÊS|MESES|DISCIPLINA|\d)",
            texto_cab,
            re.IGNORECASE,
        )
        if m_p:
            professor = m_p.group(1).strip().title()

        # 3) Lê todas as páginas e captura as notas dos alunos
        tables = camelot.read_pdf(
            tmp_path, pages="all", flavor="stream", strip_text="\n"
        )
        notas_alunos = {}
        for t in tables:
            if not eh_tabela_de_notas(t.df):
                continue
            df = extrair_tabela_notas(t.df)
            if df is None:
                continue
            for _, row in df.iterrows():
                notas_alunos[row["MATRICULA"]] = row.to_dict()

        return {
            "turma": turma,
            "disc_code": disc_code,
            "disciplina": disciplina,
            "professor": professor,
            "notas": notas_alunos,
        }
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Execução principal
# ---------------------------------------------------------------------------
if uploaded_excel and uploaded_pdfs:
    if st.button("▶️ Rodar verificação", type="primary"):

        with st.spinner("Lendo a lista de solicitações..."):
            df_excel = pd.read_excel(uploaded_excel)

        # ---------- Processa PDFs ----------
        diarios_por_codigo = {}
        diarios_por_nome = {}
        diarios_info = []

        prog = st.progress(0.0, text="Processando PDFs...")
        total = len(uploaded_pdfs)
        for i, pdf in enumerate(uploaded_pdfs):
            try:
                dados = processar_pdf(pdf.getvalue(), pdf.name)
            except Exception as e:
                st.warning(f"⚠️ Erro ao processar **{pdf.name}**: {e}")
                prog.progress((i + 1) / total)
                continue

            if not dados["turma"]:
                st.warning(f"⚠️ Não identifiquei a turma em **{pdf.name}**.")
                prog.progress((i + 1) / total)
                continue

            if dados.get("disc_code"):
                diarios_por_codigo[(dados["turma"], dados["disc_code"])] = dados
            if dados["disciplina"]:
                diarios_por_nome[
                    (dados["turma"], normalizar(dados["disciplina"]))
                ] = dados

            diarios_info.append({
                "Arquivo": pdf.name,
                "Turma": dados["turma"],
                "Disciplina": dados["disciplina"],
                "Código": dados.get("disc_code", ""),
                "Professor": dados.get("professor", ""),
                "Alunos": len(dados["notas"]),
            })
            prog.progress((i + 1) / total, text=f"Processando PDFs... ({i+1}/{total})")
        prog.empty()

        if not diarios_info:
            st.error("❌ Nenhum diário válido foi processado.")
            st.stop()

        st.success(f"✅ {len(diarios_info)} diário(s) processado(s).")

        with st.expander("📊 Diários processados"):
            st.dataframe(pd.DataFrame(diarios_info), use_container_width=True)

        # ---------- Lê solicitações ----------
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

        # ---------- Verifica cada solicitação ----------
        for sol in solicitacoes:
            diario = None
            if sol["disciplina_code"]:
                diario = diarios_por_codigo.get(
                    (sol["turma"], sol["disciplina_code"])
                )
            if diario is None:
                diario = diarios_por_nome.get(
                    (sol["turma"], normalizar(sol["disciplina_name"]))
                )

            if diario is None:
                sol["status"] = "Diário não enviado"
                continue

            notas = diario["notas"].get(sol["matricula"])
            if notas is None:
                # Tenta casar ignorando zeros à esquerda
                for mat, n in diario["notas"].items():
                    if mat.lstrip("0") == sol["matricula"].lstrip("0"):
                        notas = n
                        break

            if notas is None:
                sol["status"] = "Aluno não encontrado"
            else:
                ae = notas.get("AE", "")
                sol["status"] = "Não" if valor_em_branco(ae) else "Sim"

        # ---------- Monta DataFrame de saída ----------
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

        df_result = pd.DataFrame(linhas)
        df_result = df_result.sort_values(
            by=["Turma", "Nome do Aluno"]
        ).reset_index(drop=True)

        # ---------- Resumo ----------
        st.markdown("---")
        st.subheader("📈 Resumo da varredura")

        total_sol = len(solicitacoes)
        total_sim = sum(1 for s in solicitacoes if s["status"] == "Sim")
        total_nao = sum(1 for s in solicitacoes if s["status"] == "Não")
        total_sem_diario = sum(
            1 for s in solicitacoes if s["status"] == "Diário não enviado"
        )
        total_sem_aluno = sum(
            1 for s in solicitacoes if s["status"] == "Aluno não encontrado"
        )

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Total de solicitações", total_sol)
        c2.metric("✅ Lançadas (Sim)", total_sim)
        c3.metric("❌ Não lançadas (Não)", total_nao)
        c4.metric("📄 Diário não enviado", total_sem_diario)
        c5.metric("👤 Aluno não encontrado", total_sem_aluno)

        # ---------- Resultado por turma ----------
        st.markdown("---")
        st.subheader("📋 Resultado por turma (alunos em ordem alfabética)")

        for turma in sorted(df_result["Turma"].unique()):
            grupo = df_result[df_result["Turma"] == turma].reset_index(drop=True)
            st.markdown(f"### Turma {turma}")
            st.dataframe(grupo, use_container_width=True)

        # ---------- Gera Excel ----------
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
else:
    if not uploaded_excel:
        st.info("⬆️ Envie a planilha de solicitações para começar.")
    if not uploaded_pdfs:
        st.info("⬆️ Envie um ou mais diários em PDF.")