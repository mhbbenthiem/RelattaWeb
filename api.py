import os
import re
import io
import math
import time
import certifi
import logging
import requests
import pandas as pd

from datetime import datetime
from collections import defaultdict
from flask import Flask, request, jsonify, send_file, render_template
from flask_cors import CORS
from dotenv import load_dotenv, find_dotenv




# ===============================
# CONFIG INICIAL
# ===============================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

def get_base_directory():
    return os.path.dirname(os.path.abspath(__file__))


load_dotenv()

SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_ANON_KEY") or ""
SUPABASE_SCHEMA = os.getenv("SUPABASE_SCHEMA", "public")


if not SUPABASE_URL:
    raise Exception("SUPABASE_URL não carregou do .env")


EMAIL_CONFIG = {
    "servidor": "smtp.gmail.com",
    "porta": 587,
    "usuario": os.getenv("EMAIL_USUARIO", ""),
    "senha": os.getenv("EMAIL_SENHA", ""),
}

def _sb_assert():
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("Defina SUPABASE_URL e SUPABASE_KEY no .env")

def _sb_base():
    _sb_assert()
    return f"{SUPABASE_URL}/rest/v1"

def _sb_headers():
    h = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    if SUPABASE_SCHEMA and SUPABASE_SCHEMA != "public":
        h["Accept-Profile"] = SUPABASE_SCHEMA
        h["Content-Profile"] = SUPABASE_SCHEMA
    return h

_session = requests.Session()

# ===============================
# FLASK
# ===============================

app = Flask(__name__)
CORS(app)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder="static")
@app.route("/")
def home():
    return render_template("index.html")
app.config.update(
    SEND_FILE_MAX_AGE_DEFAULT=0,
    TEMPLATES_AUTO_RELOAD=True,
    SECRET_KEY=os.getenv("SECRET_KEY", "sistema_relatorios_2024"),
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

@app.before_request
def _log_in():
    request._t0 = time.time()
    app.logger.info(f"→ {request.method} {request.path} args={dict(request.args)}")

@app.after_request
def _log_out(resp):
    dt = ""
    if hasattr(request, "_t0"):
        dt = f" ({(time.time() - request._t0) * 1000:.0f} ms)"
    app.logger.info(f"← {request.method} {request.path} {resp.status_code}{dt}")
    return resp

# ===============================
# HELPERS SUPABASE
# ===============================

def _to_snake(s: str) -> str:
    s = re.sub(r"[^\w]+", "_", str(s).strip(), flags=re.UNICODE)
    s = re.sub(r"_+", "_", s).strip("_")
    return s.lower() or "coluna"

def _to_filters(params: dict | None) -> dict:
    q = {"select": "*"}
    if not params:
        return q

    for k, v in params.items():
        if v is None or v == "":
            continue

        kk = _to_snake(k)

        if kk in ("limit", "offset"):
            q[kk] = int(v)
        elif kk == "order":
            q["order"] = str(v)
        elif kk == "select":
            q["select"] = str(v)
        else:
            q[kk] = f"eq.{v}"

    return q

def api_get_table(table: str, params: dict | None = None, timeout: int = 10):
    url = f"{_sb_base()}/{table}"
    q = _to_filters(params)

    print("URL:", url)
    print("PARAMS:", q)

    r = _session.get(
        url,
        headers=_sb_headers(),
        params=q,
        timeout=timeout
    )

    print("STATUS:", r.status_code)

    r.raise_for_status()
    return r.json()

def api_post_table(table: str, payload: dict, timeout: int = 15):
    url = f"{_sb_base()}/{table}"

    def _san(v):
        if v is None:
            return None
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
        if isinstance(v, (pd.Timestamp, datetime)):
            return v.isoformat(sep=" ")
        return v

    payload = {k: _san(v) for k, v in payload.items()}

    r = _session.post(url, headers=_sb_headers(), json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    return data[0] if isinstance(data, list) and data else data

def api_patch_table(table: str, record_id: str, payload: dict, timeout: int = 15):
    url = f"{_sb_base()}/{table}"
    params = {"id": f"eq.{record_id}"}

    r = _session.patch(url, headers=_sb_headers(), params=params, json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    return data[0] if isinstance(data, list) and data else data

# ===============================
# REGRAS
# ===============================

def get_trimestre_atual():
    mes = datetime.now().month
    if mes <= 4:
        return 1
    elif mes <= 8:
        return 2
    return 3

# ===============================
# ROTAS
# ===============================



@app.route("/status")
def status():
    try:
        profs = api_get_table("profs", {"limit": 1})
        alunos = api_get_table("alunos", {"limit": 1})
        respostas = api_get_table("respostas", {"limit": 1, "order": "id.desc"})

        return jsonify({
            "ok": True,
            "timestamp": datetime.now().isoformat(),
            "supabase": {
                "profs": len(profs) >= 0,
                "alunos": len(alunos) >= 0,
                "respostas": len(respostas) >= 0,
            }
        })
    except Exception as e:
        return jsonify({"ok": False, "erro": str(e)}), 500

@app.route("/dados")
def carregar_dados():
    try:
        app.logger.info("SUPABASE_URL: %s", SUPABASE_URL)
        app.logger.info("SUPABASE_KEY carregada? %s", bool(SUPABASE_KEY))

        app.logger.info("Buscando professores no Supabase...")
        professores = api_get_table("profs", {"limit": 5000})



        app.logger.info("Professores carregados: %s", len(professores))

        app.logger.info("Buscando alunos no Supabase...")
        alunos = api_get_table("alunos", {"limit": 5000})

        app.logger.info("Alunos carregados: %s", len(alunos))

        return jsonify({
            "professores": professores,
            "alunos": alunos,
            "respostas": []
        })

    except Exception as e:
        app.logger.exception("Erro em /dados")
        return jsonify({"erro": str(e)}), 500

@app.route("/verificar_alunos_disponiveis", methods=["POST"])
def verificar_alunos_disponiveis():
    try:
        data = request.get_json() or {}
        ano = (data.get("ano") or "").strip()
        turma = (data.get("turma") or "").strip()
        turno = (data.get("turno") or "").strip()

        alunos = api_get_table("alunos", {
            "ano": ano,
            "turma": turma,
            "turno": turno,
            "order": "aluno.asc",
            "limit": 5000
        })

        nomes = []
        for a in alunos:
            nome = (a.get("aluno") or a.get("nome") or "").strip()
            if nome:
                nomes.append(nome)

        return jsonify({"alunos": nomes})

    except Exception as e:
        return jsonify({"erro": str(e)}), 500

@app.route("/verificar_materias_disponiveis", methods=["POST"])
def verificar_materias_disponiveis():
    try:
        data = request.get_json() or {}
        professor = (data.get("nome") or "").strip()
        aluno = (data.get("aluno") or "").strip()
        funcao = (data.get("funcao") or "").strip().title()
        trimestre_atual = get_trimestre_atual()

        materias_base = ["Língua Portuguesa", "Matemática", "História", "Geografia"]

        if funcao == "Regente":
            materias_iniciais = materias_base
        elif funcao == "Corregente":
            materias_iniciais = ["Ciências"]
        else:
            materias_iniciais = [funcao] if funcao else materias_base

        regs = api_get_table("respostas", {
            "professor": professor,
            "aluno": aluno,
            "trimestre": trimestre_atual,
            "limit": 100
        })

        usadas = {(r.get("materia") or "").strip() for r in regs}
        disponiveis = [m for m in materias_iniciais if m not in usadas]

        return jsonify({"materias": disponiveis})

    except Exception as e:
        return jsonify({"erro": str(e)}), 500

@app.route("/verificar_perfil_turma", methods=["POST"])
def verificar_perfil_turma():
    try:
        data = request.get_json() or {}
        ano = (data.get("ano") or "").strip()
        turma = (data.get("turma") or "").strip()
        turno = (data.get("turno") or "").strip()

        rows = api_get_table("respostas", {
            "ano": ano,
            "turma": turma,
            "turno": turno,
            "limit": 5000,
            "order": "datahora.desc"
        })

        candidatos = [r for r in rows if (r.get("perfilturma") or "").strip()]

        if not candidatos:
            return jsonify({"perfil_existente": False, "perfil": ""})

        perfil = (candidatos[0].get("perfilturma") or "").strip()
        return jsonify({"perfil_existente": True, "perfil": perfil})

    except Exception as e:
        return jsonify({"erro": str(e)}), 500

@app.route("/salvar_resposta", methods=["POST"])
def salvar_resposta():
    try:
        data = request.get_json() or {}

        payload = {
            "professor": (data.get("nome") or "").strip() or None,
            "ano": (data.get("ano") or "").strip() or None,
            "turno": (data.get("turno") or "").strip() or None,
            "turma": (data.get("turma") or "").strip() or None,
            "funcao": (data.get("funcao") or "").strip() or None,
            "aluno": (data.get("aluno") or "").strip() or None,
            "materia": (data.get("materia") or "").strip() or None,
            "descricao": (data.get("descricao") or "").strip() or None,
            "papi": (data.get("papi") or "").strip() or None,
            "inclusao": (data.get("inclusao") or "").strip() or None,
            "trimestre": int(data.get("trimestre") or get_trimestre_atual()),
            "perfilturma": (data.get("perfilTurma") or "").strip() or None,
        }

        inserted = api_post_table("respostas", payload)
        return jsonify({"sucesso": True, "rowIndex": str(inserted.get("id") or "")})

    except Exception as e:
        return jsonify({"erro": str(e)}), 500

@app.route("/buscar_turmas_conselho", methods=["GET"])
def buscar_turmas_conselho():
    try:
        alunos = api_get_table("alunos", {"limit": 5000})

        turmas = set()
        for row in alunos:
            ano = str(row.get("ano") or "").strip()
            turma = str(row.get("turma") or "").strip()
            if ano and turma:
                turmas.add(f"{ano} {turma}")

        return jsonify({"turmas": sorted(turmas)})

    except Exception as e:
        return jsonify({"erro": str(e)}), 500

@app.route("/buscar_ficha_turma", methods=["POST"])
def buscar_ficha_turma():
    try:
        data = request.get_json() or {}
        turma_completa = (data.get("turma") or "").strip()
        turno = (data.get("turno") or "").strip()

        parts = turma_completa.split()
        if len(parts) >= 3:
            ano = f"{parts[0]} {parts[1]}"
            turma = parts[2]
        elif len(parts) == 2:
            ano = parts[0]
            turma = parts[1]
        else:
            return jsonify({"erro": "Formato de turma inválido"}), 400

        rows = api_get_table("respostas", {
            "ano": ano,
            "turma": turma,
            "turno": turno,
            "order": "datahora.asc",
            "limit": 5000
        })

        perfil_turma = ""
        candidatos = [r for r in rows if (str(r.get("perfilturma") or "").strip())]
        if candidatos:
            candidatos.sort(key=lambda r: (str(r.get("datahora") or ""), str(r.get("id") or "")), reverse=True)
            perfil_turma = (candidatos[0].get("perfilturma") or "").strip()

        por_aluno = defaultdict(list)
        for r in rows:
            nome_aluno = (r.get("aluno") or "").strip()
            if nome_aluno:
                por_aluno[nome_aluno].append(r)

        def fmt_data(dh):
            if not dh:
                return "Data não informada"
            try:
                return pd.to_datetime(str(dh), errors="coerce").strftime("%d/%m/%Y")
            except Exception:
                return str(dh)

        resultado_alunos = []
        for nome_aluno, regs in por_aluno.items():
            regs.sort(key=lambda reg: (int(reg.get("trimestre") or 0), str(reg.get("datahora") or "")))

            registros_organizados = []
            for r in regs:
                registros_organizados.append({
                    "materia": (r.get("materia") or "Não informada"),
                    "descricao": (r.get("descricao") or "Sem descrição"),
                    "professor": (r.get("professor") or "Não informado"),
                    "funcao": (r.get("funcao") or "Não informada"),
                    "trimestre": int(r.get("trimestre") or 1),
                    "data": fmt_data(r.get("datahora"))
                })

            resultado_alunos.append({
                "nome": nome_aluno,
                "ano": ano,
                "turma": turma,
                "turno": turno,
                "foto": None,
                "registros": registros_organizados
            })

        return jsonify({
            "alunos": resultado_alunos,
            "perfil_turma": perfil_turma,
            "total_alunos_turma": len(por_aluno),
            "alunos_com_registros": len(resultado_alunos)
        })

    except Exception as e:
        return jsonify({"erro": str(e)}), 500

@app.route("/buscar_resumo", methods=["POST"])
def buscar_resumo():
    try:
        data = request.get_json() or {}

        professor = (data.get("professor") or "").strip()
        trimestre = (data.get("trimestre") or "").strip()
        mes = (data.get("mes") or "").strip()
        aluno_filtro = (data.get("aluno") or "").strip().lower()
        ano_filtro = (data.get("ano") or "").strip()
        turma_filtro = (data.get("turma") or "").strip()

        if not professor:
            return jsonify({"erro": "Professor não informado."}), 400

        rows = api_get_table("respostas", {
            "professor": professor,
            "limit": 5000
        })

        # filtros adicionais
        filtrados = []
        for r in rows:
            if trimestre and str(r.get("trimestre") or "") != trimestre:
                continue

            if ano_filtro and str(r.get("ano") or "").strip() != ano_filtro:
                continue

            if turma_filtro and str(r.get("turma") or "").strip() != turma_filtro:
                continue

            nome_aluno = str(r.get("aluno") or "").strip()
            if aluno_filtro and aluno_filtro not in nome_aluno.lower():
                continue

            if mes:
                datahora = r.get("datahora")
                try:
                    dt = pd.to_datetime(str(datahora), errors="coerce")
                    if pd.isna(dt) or str(dt.month) != mes:
                        continue
                except Exception:
                    continue

            filtrados.append(r)

        if not filtrados:
            return jsonify({
                "resumo": "Nenhum registro encontrado com os filtros informados.",
                "rowIndexes": [],
                "registros": []
            })

        grupos = defaultdict(list)
        for r in filtrados:
            chave = (
                r.get("ano") or "",
                r.get("turma") or "",
                r.get("turno") or ""
            )
            grupos[chave].append(r)

        resumo = []
        registros = []

        resumo.append("RESUMO DE PREENCHIMENTOS")
        resumo.append(f"Professor(a): {professor}")
        resumo.append("=" * 60)
        resumo.append(f"Total de registros: {len(filtrados)}")
        resumo.append(f"Data do relatório: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
        resumo.append("")

        def fmt_data(datahora):
            if not datahora:
                return "Data não informada"
            try:
                return pd.to_datetime(str(datahora), errors="coerce").strftime("%d/%m/%Y %H:%M")
            except Exception:
                return str(datahora)

        for (ano, turma, turno), grupo in grupos.items():
            resumo.append(f"TURMA: {ano} {turma} - {turno}")
            resumo.append("-" * 40)

            alunos = defaultdict(list)
            for r in grupo:
                alunos[r.get("aluno") or "—"].append(r)

            for aluno, regs in alunos.items():
                resumo.append(f"\nALUNO: {aluno}")
                resumo.append("Registros:")

                for r in regs:
                    rid = str(r.get("id") or "")
                    data_fmt = fmt_data(r.get("datahora"))
                    materia = r.get("materia") or "Não informada"
                    tri = r.get("trimestre") or 1
                    desc = r.get("descricao") or "Sem descrição"

                    resumo.append(f"[ID {rid}]")
                    resumo.append(f"• {materia} ({tri}º Tri) - {data_fmt}")
                    resumo.append(f"{desc}")
                    resumo.append("")

                    registros.append({
                        "id": rid,
                        "aluno": (aluno or "").strip(),
                        "materia": materia,
                        "dataHora": data_fmt,
                        "descricao": desc
                    })

        row_indexes = [str(r.get("id") or "") for r in filtrados]

        resumo.append("=" * 60)
        resumo.append("Relatório gerado automaticamente pelo Sistema de Relatórios")

        return jsonify({
            "resumo": "\n".join(resumo),
            "rowIndexes": row_indexes,
            "registros": registros
        })

    except Exception as e:
        app.logger.exception("Erro em /buscar_resumo")
        return jsonify({"erro": f"Erro interno: {str(e)}"}), 500

@app.route("/salvar_edicao", methods=["POST"])
def salvar_edicao():
    UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
    try:
        data = request.get_json(silent=True) or {}
        conteudo = (data.get("conteudo") or "").strip()
        rec_id = (data.get("id") or data.get("rowIndex") or "").strip()

        if not rec_id or not UUID_RE.match(rec_id):
            return jsonify({"erro": "ID inválido (UUID esperado)"}), 400

        resp = api_patch_table("respostas", rec_id, {"descricao": conteudo})
        return jsonify({"mensagem": "Descrição atualizada com sucesso!", "id": resp.get("id")}), 200

    except Exception as e:
        return jsonify({"erro": str(e)}), 500

@app.get("/export/respostas.xlsx")
def export_respostas_xlsx():
    try:
        professor = request.args.get("professor")
        ano = request.args.get("ano")
        turma = request.args.get("turma")
        turno = request.args.get("turno")

        params = {"limit": 100000}
        if professor:
            params["professor"] = professor
        if ano:
            params["ano"] = ano
        if turma:
            params["turma"] = turma
        if turno:
            params["turno"] = turno

        rows = api_get_table("respostas", params)
        df = pd.DataFrame(rows)

        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Respostas")
        buf.seek(0)

        return send_file(
            buf,
            as_attachment=True,
            download_name="respostas.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    except Exception as e:
        return jsonify({"erro": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)