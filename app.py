from flask import (
    Flask,
    request,
    redirect,
    session,
    render_template_string,
    send_file
)

import sqlite3
import os
import requests
import hashlib
import hmac
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from io import BytesIO
from urllib.parse import quote

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

from werkzeug.security import (
    generate_password_hash,
    check_password_hash
)


app = Flask(__name__)
app.secret_key = os.environ.get(
    "SECRET_KEY",
    "chave-local-apenas-para-desenvolvimento"
)

BANCO = "orcamentos.db"
DATABASE_URL = os.environ.get("DATABASE_URL")
USANDO_POSTGRES = bool(DATABASE_URL)

MERCADO_PAGO_ACCESS_TOKEN = os.environ.get("MERCADO_PAGO_ACCESS_TOKEN")
MERCADO_PAGO_PLAN_ID = os.environ.get("MERCADO_PAGO_PLAN_ID")
MERCADO_PAGO_WEBHOOK_SECRET = os.environ.get("MERCADO_PAGO_WEBHOOK_SECRET")


# =====================================================
# BANCO
# =====================================================

def conectar_banco():
    """
    No Render, usa PostgreSQL através da variável DATABASE_URL.
    No computador local, se DATABASE_URL não existir, continua usando SQLite.
    """
    if USANDO_POSTGRES:
        return psycopg2.connect(
            DATABASE_URL,
            cursor_factory=RealDictCursor
        )

    conexao = sqlite3.connect(BANCO)
    conexao.row_factory = sqlite3.Row
    return conexao


def executar(conexao, sql, parametros=()):
    """
    Executa a mesma consulta tanto no SQLite quanto no PostgreSQL.
    O SQLite usa ? nos parâmetros; o PostgreSQL usa %s.
    """
    if USANDO_POSTGRES:
        sql = sql.replace("?", "%s")

    cursor = conexao.cursor()
    cursor.execute(sql, parametros)
    return cursor


def criar_banco():

    conexao = conectar_banco()

    if USANDO_POSTGRES:
        id_usuario = "SERIAL PRIMARY KEY"
        id_orcamento = "SERIAL PRIMARY KEY"
    else:
        id_usuario = "INTEGER PRIMARY KEY AUTOINCREMENT"
        id_orcamento = "INTEGER PRIMARY KEY AUTOINCREMENT"

    executar(conexao, f"""
        CREATE TABLE IF NOT EXISTS usuarios (
            id {id_usuario},
            nome TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            senha TEXT NOT NULL,
            plano TEXT DEFAULT 'Gratis'
        )
    """)

    executar(conexao, f"""
        CREATE TABLE IF NOT EXISTS orcamentos (
            id {id_orcamento},
            usuario_id INTEGER NOT NULL,
            empresa TEXT NOT NULL,
            telefone TEXT,
            cliente TEXT NOT NULL,
            servico TEXT NOT NULL,
            descricao TEXT NOT NULL,
            valor REAL NOT NULL,
            prazo INTEGER NOT NULL,
            validade INTEGER NOT NULL,
            pagamento TEXT NOT NULL,
            data TEXT NOT NULL
        )
    """)

    conexao.commit()
    conexao.close()


criar_banco()


# =====================================================
# AJUSTAR BANCO ANTIGO
# =====================================================

def adicionar_coluna_se_nao_existir(tabela, coluna, definicao):

    conexao = conectar_banco()

    if USANDO_POSTGRES:
        executar(
            conexao,
            f"ALTER TABLE {tabela} "
            f"ADD COLUMN IF NOT EXISTS {coluna} {definicao}"
        )
        conexao.commit()
        conexao.close()
        return

    cursor = executar(
        conexao,
        f"PRAGMA table_info({tabela})"
    )

    colunas = [
        item["name"]
        for item in cursor.fetchall()
    ]

    if coluna not in colunas:
        executar(
            conexao,
            f"ALTER TABLE {tabela} ADD COLUMN {coluna} {definicao}"
        )
        conexao.commit()

    conexao.close()


adicionar_coluna_se_nao_existir(
    "usuarios",
    "plano",
    "TEXT DEFAULT 'Gratis'"
)


adicionar_coluna_se_nao_existir(
    "usuarios",
    "mercado_pago_subscription_id",
    "TEXT"
)

adicionar_coluna_se_nao_existir(
    "usuarios",
    "mercado_pago_status",
    "TEXT"
)

adicionar_coluna_se_nao_existir(
    "orcamentos",
    "telefone",
    "TEXT"
)

adicionar_coluna_se_nao_existir(
    "orcamentos",
    "validade",
    "INTEGER DEFAULT 7"
)

adicionar_coluna_se_nao_existir(
    "orcamentos",
    "pagamento",
    "TEXT DEFAULT 'A combinar'"
)


# =====================================================
# FUNÇÕES
# =====================================================

def usuario_logado():
    return "usuario_id" in session


def formatar_valor(valor):

    return (
        f"{valor:,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


def mes_atual():

    return datetime.now().strftime("%m/%Y")


def contar_orcamentos_mes(usuario_id):

    conexao = conectar_banco()

    registros = executar(conexao, """
        SELECT data
        FROM orcamentos
        WHERE usuario_id = ?
    """, (usuario_id,)).fetchall()

    conexao.close()

    total = 0

    for item in registros:

        try:

            if item["data"][3:] == mes_atual():
                total += 1

        except:
            pass

    return total


# =====================================================
# CSS
# =====================================================

CSS = """
<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    font-family: Arial, Helvetica, sans-serif;
    background: #f5f7fb;
    color: #1f2937;
}

.sidebar {
    position: fixed;
    width: 240px;
    height: 100vh;
    background: #111827;
    padding: 25px 20px;
    color: white;
}

.logo {
    font-size: 25px;
    font-weight: bold;
    margin-bottom: 35px;
}

.logo span {
    color: #60a5fa;
}

.menu a {
    display: block;
    padding: 13px;
    margin-bottom: 8px;
    color: #d1d5db;
    text-decoration: none;
    border-radius: 8px;
}

.menu a:hover {
    background: #1f2937;
    color: white;
}

.main {
    margin-left: 240px;
    padding: 35px;
}

.topo {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 30px;
}

.cards {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 20px;
    margin-bottom: 30px;
}

.card,
.painel {
    background: white;
    padding: 25px;
    border-radius: 15px;
    box-shadow: 0 3px 12px rgba(0,0,0,0.05);
}

.card h2 {
    font-size: 28px;
}

label {
    display: block;
    margin-top: 15px;
    margin-bottom: 6px;
    font-weight: bold;
}

input,
textarea,
select {
    width: 100%;
    padding: 13px;
    border: 1px solid #d1d5db;
    border-radius: 8px;
    font-size: 15px;
}

textarea {
    min-height: 110px;
}

button,
.botao {
    display: inline-block;
    background: #2563eb;
    color: white;
    border: none;
    padding: 13px 20px;
    border-radius: 8px;
    text-decoration: none;
    cursor: pointer;
}

button {
    margin-top: 20px;
}

.verde {
    background: #16a34a;
}

.escuro {
    background: #111827;
}

.cinza {
    background: #6b7280;
}

.whatsapp {
    background: #16a34a;
}

table {
    width: 100%;
    border-collapse: collapse;
}

th {
    text-align: left;
    background: #f9fafb;
    padding: 13px;
}

td {
    padding: 13px;
    border-top: 1px solid #e5e7eb;
}

.badge {
    display: inline-block;
    background: #dbeafe;
    color: #1d4ed8;
    padding: 6px 10px;
    border-radius: 20px;
    font-size: 12px;
}

.aviso {
    background: #fff7ed;
    color: #9a3412;
    padding: 15px;
    border-radius: 10px;
    margin-bottom: 20px;
}

.login-body {
    min-height: 100vh;
    display: flex;
    justify-content: center;
    align-items: center;
}

.login-box {
    width: 420px;
    background: white;
    padding: 35px;
    border-radius: 15px;
    box-shadow: 0 5px 25px rgba(0,0,0,0.08);
}

.login-logo {
    text-align: center;
    font-size: 28px;
    font-weight: bold;
}

.login-logo span {
    color: #2563eb;
}

.erro {
    background: #fee2e2;
    color: #991b1b;
    padding: 12px;
    border-radius: 8px;
    margin-bottom: 15px;
}

@media (max-width: 900px) {

    .sidebar {
        width: 190px;
    }

    .main {
        margin-left: 190px;
    }

    .cards {
        grid-template-columns: 1fr;
    }
}

</style>
"""


def menu_lateral(plano):

    return render_template_string("""
        <div class="sidebar">

            <div class="logo">
                Orça<span>Fácil</span>
            </div>

            <div class="menu">

                <a href="/">
                    🏠 Dashboard
                </a>

                <a href="/novo">
                    ➕ Novo orçamento
                </a>

                <a href="/historico">
                    📄 Orçamentos
                </a>

                <a href="/planos">
                    💎 Planos
                </a>

                <a href="/logout">
                    🚪 Sair
                </a>

            </div>

            <div style="margin-top:40px;">

                <small>Plano atual</small>

                <br>

                <strong>
                    {{ plano }}
                </strong>

            </div>

        </div>
    """, plano=plano)


# =====================================================
# CADASTRO
# =====================================================

@app.route("/cadastro", methods=["GET", "POST"])
def cadastro():

    erro = ""

    if request.method == "POST":

        nome = request.form["nome"]
        email = request.form["email"].lower()
        senha = request.form["senha"]

        conexao = conectar_banco()

        try:

            executar(conexao, """
                INSERT INTO usuarios
                (nome, email, senha, plano)

                VALUES (?, ?, ?, ?)
            """, (
                nome,
                email,
                generate_password_hash(senha),
                "Gratis"
            ))

            conexao.commit()

            return redirect("/login")

        except (sqlite3.IntegrityError, psycopg2.IntegrityError):

            erro = """
            <div class="erro">
                Este e-mail já está cadastrado.
            </div>
            """

        finally:

            conexao.close()

    return render_template_string("""
        <!DOCTYPE html>

        <html lang="pt-br">

        <head>

            <meta charset="UTF-8">

            <title>Cadastro</title>

            {{ css|safe }}

        </head>

        <body class="login-body">

            <div class="login-box">

                <div class="login-logo">
                    Orça<span>Fácil</span>
                </div>

                <h2>Criar conta</h2>

                {{ erro|safe }}

                <form method="POST">

                    <label>Nome</label>
                    <input name="nome" required>

                    <label>E-mail</label>
                    <input type="email" name="email" required>

                    <label>Senha</label>
                    <input type="password" name="senha" minlength="6" required>

                    <button style="width:100%;">
                        Criar conta
                    </button>

                </form>

                <p style="text-align:center;">
                    <a href="/login">
                        Já tenho uma conta
                    </a>
                </p>

            </div>

        </body>

        </html>
    """, css=CSS, erro=erro)


# =====================================================
# LOGIN
# =====================================================

@app.route("/login", methods=["GET", "POST"])
def login():

    erro = ""

    if request.method == "POST":

        email = request.form["email"].lower()
        senha = request.form["senha"]

        conexao = conectar_banco()

        usuario = executar(conexao, """
            SELECT *
            FROM usuarios
            WHERE email = ?
        """, (email,)).fetchone()

        conexao.close()

        if usuario and check_password_hash(
            usuario["senha"],
            senha
        ):

            session["usuario_id"] = usuario["id"]
            session["usuario_nome"] = usuario["nome"]

            return redirect("/")

        erro = """
        <div class="erro">
            E-mail ou senha incorretos.
        </div>
        """

    return render_template_string("""
        <!DOCTYPE html>

        <html lang="pt-br">

        <head>

            <meta charset="UTF-8">

            <title>Login</title>

            {{ css|safe }}

        </head>

        <body class="login-body">

            <div class="login-box">

                <div class="login-logo">
                    Orça<span>Fácil</span>
                </div>

                <h2>Entrar</h2>

                {{ erro|safe }}

                <form method="POST">

                    <label>E-mail</label>
                    <input type="email" name="email" required>

                    <label>Senha</label>
                    <input type="password" name="senha" required>

                    <button style="width:100%;">
                        Entrar
                    </button>

                </form>

                <p style="text-align:center;">
                    <a href="/cadastro">
                        Criar conta
                    </a>
                </p>

            </div>

        </body>

        </html>
    """, css=CSS, erro=erro)


# =====================================================
# LOGOUT
# =====================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect("/login")


# =====================================================
# DASHBOARD
# =====================================================

@app.route("/")
def dashboard():

    if not usuario_logado():
        return redirect("/login")

    conexao = conectar_banco()

    usuario = executar(conexao, """
        SELECT *
        FROM usuarios
        WHERE id = ?
    """, (
        session["usuario_id"],
    )).fetchone()

    total_orcamentos = executar(conexao, """
        SELECT COUNT(*) AS total
        FROM orcamentos
        WHERE usuario_id = ?
    """, (
        session["usuario_id"],
    )).fetchone()["total"]

    total_valor = executar(conexao, """
        SELECT COALESCE(SUM(valor), 0) AS total
        FROM orcamentos
        WHERE usuario_id = ?
    """, (
        session["usuario_id"],
    )).fetchone()["total"]

    clientes = executar(conexao, """
        SELECT COUNT(DISTINCT cliente) AS total
        FROM orcamentos
        WHERE usuario_id = ?
    """, (
        session["usuario_id"],
    )).fetchone()["total"]

    ultimos = executar(conexao, """
        SELECT *
        FROM orcamentos

        WHERE usuario_id = ?

        ORDER BY id DESC

        LIMIT 5
    """, (
        session["usuario_id"],
    )).fetchall()

    conexao.close()

    usados_mes = contar_orcamentos_mes(
        session["usuario_id"]
    )

    return render_template_string("""
        <!DOCTYPE html>

        <html lang="pt-br">

        <head>

            <meta charset="UTF-8">

            <title>Dashboard</title>

            {{ css|safe }}

        </head>

        <body>

            {{ menu|safe }}

            <div class="main">

                <div class="topo">

                    <div>

                        <h1>Dashboard</h1>

                        <p>
                            Olá, {{ nome }} 👋
                        </p>

                    </div>

                    <span class="badge">
                        Plano {{ plano }}
                    </span>

                </div>


                {% if plano == "Gratis" %}

                    <div class="aviso">

                        Você utilizou

                        <strong>
                            {{ usados_mes }} de 5
                        </strong>

                        orçamentos grátis neste mês.

                    </div>

                {% endif %}


                <div class="cards">

                    <div class="card">

                        <small>Orçamentos</small>

                        <h2>
                            {{ total }}
                        </h2>

                    </div>

                    <div class="card">

                        <small>Valor total</small>

                        <h2>
                            R$ {{ valor_total }}
                        </h2>

                    </div>

                    <div class="card">

                        <small>Clientes</small>

                        <h2>
                            {{ clientes }}
                        </h2>

                    </div>

                </div>


                <div class="painel">

                    <div style="
                        display:flex;
                        justify-content:space-between;
                        align-items:center;
                    ">

                        <h2>Últimos orçamentos</h2>

                        <a
                            class="botao"
                            href="/novo"
                        >
                            + Novo orçamento
                        </a>

                    </div>

                    <table>

                        <tr>
                            <th>Nº</th>
                            <th>Cliente</th>
                            <th>Serviço</th>
                            <th>Valor</th>
                            <th></th>
                        </tr>

                        {% for item in ultimos %}

                            <tr>

                                <td>
                                    #{{ item["id"] }}
                                </td>

                                <td>
                                    {{ item["cliente"] }}
                                </td>

                                <td>
                                    {{ item["servico"] }}
                                </td>

                                <td>
                                    R$ {{ formatar(item["valor"]) }}
                                </td>

                                <td>

                                    <a href="/orcamento/{{ item['id'] }}">
                                        Abrir
                                    </a>

                                </td>

                            </tr>

                        {% endfor %}

                    </table>

                </div>

            </div>

        </body>

        </html>
    """,
        css=CSS,
        menu=menu_lateral(usuario["plano"]),
        nome=usuario["nome"],
        plano=usuario["plano"],
        usados_mes=usados_mes,
        total=total_orcamentos,
        valor_total=formatar_valor(total_valor),
        clientes=clientes,
        ultimos=ultimos,
        formatar=formatar_valor
    )


# =====================================================
# NOVO ORÇAMENTO
# =====================================================

@app.route("/novo")
def novo():

    if not usuario_logado():
        return redirect("/login")

    conexao = conectar_banco()

    usuario = executar(conexao, """
        SELECT *
        FROM usuarios
        WHERE id = ?
    """, (
        session["usuario_id"],
    )).fetchone()

    conexao.close()

    usados_mes = contar_orcamentos_mes(
        session["usuario_id"]
    )

    bloqueado = (
        usuario["plano"] == "Gratis"
        and usados_mes >= 5
    )

    return render_template_string("""
        <!DOCTYPE html>

        <html lang="pt-br">

        <head>

            <meta charset="UTF-8">

            <title>Novo orçamento</title>

            {{ css|safe }}

        </head>

        <body>

            {{ menu|safe }}

            <div class="main">

                <h1>Novo orçamento</h1>


                {% if bloqueado %}

                    <div class="aviso">

                        Você atingiu o limite de
                        5 orçamentos deste mês.

                        <br><br>

                        <a
                            class="botao"
                            href="/planos"
                        >
                            Conhecer plano Pro
                        </a>

                    </div>

                {% else %}

                    <div class="painel">

                        <form action="/gerar" method="POST">

                            <label>
                                Nome da empresa
                            </label>

                            <input
                                name="empresa"
                                placeholder="Ex: Victor Tecnologia"
                                required
                            >


                            <label>
                                Telefone / WhatsApp
                            </label>

                            <input
                                name="telefone"
                                placeholder="Ex: 24999999999"
                            >


                            <label>
                                Cliente
                            </label>

                            <input
                                name="cliente"
                                required
                            >


                            <label>
                                Serviço
                            </label>

                            <input
                                name="servico"
                                required
                            >


                            <label>
                                Descrição
                            </label>

                            <textarea
                                name="descricao"
                                required
                            ></textarea>


                            <label>
                                Valor
                            </label>

                            <input
                                type="number"
                                step="0.01"
                                min="0"
                                name="valor"
                                required
                            >


                            <label>
                                Prazo em dias
                            </label>

                            <input
                                type="number"
                                name="prazo"
                                min="1"
                                required
                            >


                            <label>
                                Validade do orçamento
                            </label>

                            <select name="validade">

                                <option value="7">
                                    7 dias
                                </option>

                                <option value="15">
                                    15 dias
                                </option>

                                <option value="30">
                                    30 dias
                                </option>

                            </select>


                            <label>
                                Forma de pagamento
                            </label>

                            <select name="pagamento">

                                <option>
                                    Pix
                                </option>

                                <option>
                                    Dinheiro
                                </option>

                                <option>
                                    Cartão
                                </option>

                                <option>
                                    Pix ou cartão
                                </option>

                                <option>
                                    A combinar
                                </option>

                            </select>


                            <button>
                                Gerar orçamento
                            </button>

                        </form>

                    </div>

                {% endif %}

            </div>

        </body>

        </html>
    """,
        css=CSS,
        menu=menu_lateral(usuario["plano"]),
        bloqueado=bloqueado
    )


# =====================================================
# GERAR
# =====================================================

@app.route("/gerar", methods=["POST"])
def gerar():

    if not usuario_logado():
        return redirect("/login")

    conexao = conectar_banco()

    usuario = executar(conexao, """
        SELECT *
        FROM usuarios
        WHERE id = ?
    """, (
        session["usuario_id"],
    )).fetchone()

    conexao.close()

    usados_mes = contar_orcamentos_mes(
        session["usuario_id"]
    )

    if (
        usuario["plano"] == "Gratis"
        and usados_mes >= 5
    ):

        return redirect("/planos")

    empresa = request.form["empresa"]
    telefone = request.form["telefone"]
    cliente = request.form["cliente"]
    servico = request.form["servico"]
    descricao = request.form["descricao"]

    valor = float(
        request.form["valor"]
    )

    prazo = int(
        request.form["prazo"]
    )

    validade = int(
        request.form["validade"]
    )

    pagamento = request.form["pagamento"]

    data = datetime.now().strftime(
        "%d/%m/%Y"
    )

    conexao = conectar_banco()

    sql_insert = """
        INSERT INTO orcamentos
        (
            usuario_id,
            empresa,
            telefone,
            cliente,
            servico,
            descricao,
            valor,
            prazo,
            validade,
            pagamento,
            data
        )

        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    if USANDO_POSTGRES:
        sql_insert += " RETURNING id"

    cursor = executar(
        conexao,
        sql_insert,
        (
            session["usuario_id"],
            empresa,
            telefone,
            cliente,
            servico,
            descricao,
            valor,
            prazo,
            validade,
            pagamento,
            data
        )
    )

    if USANDO_POSTGRES:
        id_orcamento = cursor.fetchone()["id"]
    else:
        id_orcamento = cursor.lastrowid

    conexao.commit()
    conexao.close()

    return redirect(
        f"/orcamento/{id_orcamento}"
    )


# =====================================================
# VISUALIZAR ORÇAMENTO
# =====================================================

@app.route("/orcamento/<int:id>")
def visualizar(id):

    if not usuario_logado():
        return redirect("/login")

    conexao = conectar_banco()

    usuario = executar(conexao, """
        SELECT *
        FROM usuarios
        WHERE id = ?
    """, (
        session["usuario_id"],
    )).fetchone()

    item = executar(conexao, """
        SELECT *
        FROM orcamentos

        WHERE id = ?
        AND usuario_id = ?
    """, (
        id,
        session["usuario_id"]
    )).fetchone()

    conexao.close()

    if not item:
        return "Orçamento não encontrado."

    mensagem_whatsapp = f"""
Olá {item["cliente"]}!

Segue seu orçamento:

Empresa: {item["empresa"]}
Serviço: {item["servico"]}
Valor: R$ {formatar_valor(item["valor"])}
Prazo: {item["prazo"]} dias
Validade: {item["validade"]} dias
Forma de pagamento: {item["pagamento"]}

Obrigado!
"""

    link_whatsapp = (
        "https://wa.me/?text="
        + quote(mensagem_whatsapp)
    )

    return render_template_string("""
        <!DOCTYPE html>

        <html lang="pt-br">

        <head>

            <meta charset="UTF-8">

            <title>Orçamento</title>

            {{ css|safe }}

        </head>

        <body>

            {{ menu|safe }}

            <div class="main">

                <div class="painel">

                    <h1>
                        {{ item["empresa"] }}
                    </h1>

                    {% if item["telefone"] %}

                        <p>
                            WhatsApp:
                            {{ item["telefone"] }}
                        </p>

                    {% endif %}

                    <p>
                        Orçamento #{{ item["id"] }}
                    </p>

                    <hr>

                    <p>
                        <strong>Cliente:</strong>
                        {{ item["cliente"] }}
                    </p>

                    <p>
                        <strong>Serviço:</strong>
                        {{ item["servico"] }}
                    </p>

                    <p>
                        <strong>Descrição:</strong>
                        {{ item["descricao"] }}
                    </p>

                    <p>
                        <strong>Prazo:</strong>
                        {{ item["prazo"] }} dias
                    </p>

                    <p>
                        <strong>Validade:</strong>
                        {{ item["validade"] }} dias
                    </p>

                    <p>
                        <strong>Pagamento:</strong>
                        {{ item["pagamento"] }}
                    </p>

                    <h2>
                        R$ {{ valor }}
                    </h2>


                    <a
                        class="botao verde"
                        href="/pdf/{{ item['id'] }}"
                    >
                        Baixar PDF
                    </a>


                    <a
                        class="botao whatsapp"
                        href="{{ whatsapp }}"
                        target="_blank"
                    >
                        Enviar pelo WhatsApp
                    </a>


                    <a
                        class="botao escuro"
                        href="/"
                    >
                        Dashboard
                    </a>

                </div>

            </div>

        </body>

        </html>
    """,
        css=CSS,
        menu=menu_lateral(usuario["plano"]),
        item=item,
        valor=formatar_valor(item["valor"]),
        whatsapp=link_whatsapp
    )


# =====================================================
# HISTÓRICO
# =====================================================

@app.route("/historico")
def historico():

    if not usuario_logado():
        return redirect("/login")

    conexao = conectar_banco()

    usuario = executar(conexao, """
        SELECT *
        FROM usuarios
        WHERE id = ?
    """, (
        session["usuario_id"],
    )).fetchone()

    itens = executar(conexao, """
        SELECT *
        FROM orcamentos

        WHERE usuario_id = ?

        ORDER BY id DESC
    """, (
        session["usuario_id"],
    )).fetchall()

    conexao.close()

    return render_template_string("""
        <!DOCTYPE html>

        <html lang="pt-br">

        <head>

            <meta charset="UTF-8">

            <title>Orçamentos</title>

            {{ css|safe }}

        </head>

        <body>

            {{ menu|safe }}

            <div class="main">

                <div class="topo">

                    <h1>
                        Meus Orçamentos
                    </h1>

                    <a
                        class="botao"
                        href="/novo"
                    >
                        + Novo
                    </a>

                </div>

                <div class="painel">

                    <table>

                        <tr>
                            <th>Nº</th>
                            <th>Cliente</th>
                            <th>Serviço</th>
                            <th>Valor</th>
                            <th>Data</th>
                            <th></th>
                        </tr>

                        {% for item in itens %}

                            <tr>

                                <td>
                                    #{{ item["id"] }}
                                </td>

                                <td>
                                    {{ item["cliente"] }}
                                </td>

                                <td>
                                    {{ item["servico"] }}
                                </td>

                                <td>
                                    R$ {{ formatar(item["valor"]) }}
                                </td>

                                <td>
                                    {{ item["data"] }}
                                </td>

                                <td>

                                    <a href="/orcamento/{{ item['id'] }}">
                                        Abrir
                                    </a>

                                </td>

                            </tr>

                        {% endfor %}

                    </table>

                </div>

            </div>

        </body>

        </html>
    """,
        css=CSS,
        menu=menu_lateral(usuario["plano"]),
        itens=itens,
        formatar=formatar_valor
    )


# =====================================================
# PLANOS
# =====================================================

@app.route("/planos")
def planos():

    if not usuario_logado():
        return redirect("/login")

    conexao = conectar_banco()

    usuario = executar(conexao, """
        SELECT *
        FROM usuarios
        WHERE id = ?
    """, (
        session["usuario_id"],
    )).fetchone()

    conexao.close()

    return render_template_string("""
        <!DOCTYPE html>

        <html lang="pt-br">

        <head>

            <meta charset="UTF-8">

            <title>Planos</title>

            {{ css|safe }}

        </head>

        <body>

            {{ menu|safe }}

            <div class="main">

                <h1>
                    Planos
                </h1>

                <div class="cards">

                    <div class="card">

                        <h2>Grátis</h2>

                        <h1>R$ 0</h1>

                        <p>
                            ✓ 5 orçamentos por mês
                        </p>

                        <p>
                            ✓ Histórico
                        </p>

                        <p>
                            ✓ PDF
                        </p>

                        <p>
                            ✓ WhatsApp
                        </p>

                    </div>


                    <div class="card">

                        <span class="badge">
                            MAIS POPULAR
                        </span>

                        <h2>Pro</h2>

                        <h1>
                            R$ 29,90/mês
                        </h1>

                        <p>
                            ✓ Orçamentos ilimitados
                        </p>

                        <p>
                            ✓ Todos os recursos
                        </p>

                        <p>
                            ✓ Personalização
                        </p>

                        <p>
                            ✓ Relatórios
                        </p>

                        <a
                            class="botao"
                            href="/assinar-pro"
                        >
                            Assinar Pro
                        </a>

                    </div>

                </div>

                <p>
                    O pagamento do plano Pro é processado pelo Mercado Pago.
                </p>

            </div>

        </body>

        </html>
    """,
        css=CSS,
        menu=menu_lateral(usuario["plano"])
    )



# =====================================================
# ASSINATURA PRO - MERCADO PAGO
# =====================================================

@app.route("/assinar-pro")
def assinar_pro():

    if not usuario_logado():
        return redirect("/login")

    if not MERCADO_PAGO_ACCESS_TOKEN:
        return (
            "Mercado Pago ainda não está configurado no servidor.",
            500
        )

    conexao = conectar_banco()

    usuario = executar(conexao, """
        SELECT *
        FROM usuarios
        WHERE id = ?
    """, (
        session["usuario_id"],
    )).fetchone()

    conexao.close()

    if not usuario:
        session.clear()
        return redirect("/login")

    # Criamos uma assinatura individual em status pending.
    # Assim o external_reference identifica exatamente qual
    # usuário do OrçaFácil iniciou a assinatura.
    url = "https://api.mercadopago.com/preapproval"

    headers = {
        "Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    dados = {
        "reason": "OrçaFácil Pro",
        "external_reference": str(usuario["id"]),
        "payer_email": usuario["email"],
        "auto_recurring": {
            "frequency": 1,
            "frequency_type": "months",
            "transaction_amount": 29.90,
            "currency_id": "BRL"
        },
        "back_url": request.url_root.rstrip("/") + "/planos",
        "status": "pending"
    }

    try:
        resposta = requests.post(
            url,
            json=dados,
            headers=headers,
            timeout=30
        )
    except requests.RequestException:
        return (
            "Não foi possível conectar ao Mercado Pago. "
            "Tente novamente em alguns instantes.",
            502
        )

    if resposta.status_code not in (200, 201):
        return (
            "Não foi possível iniciar a assinatura.<br><br>"
            f"Código: {resposta.status_code}<br>"
            f"Resposta: {resposta.text}",
            502
        )

    assinatura = resposta.json()
    assinatura_id = assinatura.get("id")
    assinatura_status = assinatura.get("status")
    link_pagamento = assinatura.get("init_point")

    if assinatura_id:
        conexao = conectar_banco()

        executar(conexao, """
            UPDATE usuarios
            SET mercado_pago_subscription_id = ?,
                mercado_pago_status = ?
            WHERE id = ?
        """, (
            assinatura_id,
            assinatura_status,
            usuario["id"]
        ))

        conexao.commit()
        conexao.close()

    if not link_pagamento:
        return (
            "O Mercado Pago criou a assinatura, "
            "mas não retornou o link de pagamento.",
            502
        )

    return redirect(link_pagamento)


# =====================================================
# WEBHOOK MERCADO PAGO
# =====================================================

def validar_webhook_mercado_pago():

    if not MERCADO_PAGO_WEBHOOK_SECRET:
        return False

    assinatura_recebida = request.headers.get("x-signature", "")
    request_id = request.headers.get("x-request-id", "")

    partes = {}

    for parte in assinatura_recebida.split(","):
        if "=" in parte:
            chave, valor = parte.strip().split("=", 1)
            partes[chave] = valor

    ts = partes.get("ts")
    v1 = partes.get("v1")

    if not ts or not v1:
        return False

    corpo = request.get_json(silent=True) or {}

    data_id = (
        request.args.get("data.id")
        or request.args.get("id")
        or (corpo.get("data") or {}).get("id")
    )

    if data_id is not None:
        data_id = str(data_id).lower()

    manifesto = ""

    if data_id:
        manifesto += f"id:{data_id};"

    if request_id:
        manifesto += f"request-id:{request_id};"

    manifesto += f"ts:{ts};"

    assinatura_calculada = hmac.new(
        MERCADO_PAGO_WEBHOOK_SECRET.encode("utf-8"),
        manifesto.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(
        assinatura_calculada,
        v1
    )


@app.route("/webhook/mercadopago", methods=["POST"])
def webhook_mercado_pago():

    if not validar_webhook_mercado_pago():
        return "Assinatura inválida", 401

    corpo = request.get_json(silent=True) or {}

    tipo = (
        corpo.get("type")
        or request.args.get("type")
        or request.args.get("topic")
        or ""
    )

    data_id = (
        (corpo.get("data") or {}).get("id")
        or request.args.get("data.id")
        or request.args.get("id")
    )

    if not data_id:
        return "OK", 200

    headers = {
        "Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    # Atualizações da assinatura.
    if tipo == "subscription_preapproval":

        try:
            resposta = requests.get(
                f"https://api.mercadopago.com/preapproval/{data_id}",
                headers=headers,
                timeout=30
            )
        except requests.RequestException:
            return "Erro temporário", 500

        if resposta.status_code != 200:
            return "OK", 200

        assinatura = resposta.json()

        usuario_id = assinatura.get("external_reference")
        status = assinatura.get("status")

        if usuario_id:
            novo_plano = (
                "Pro"
                if status == "authorized"
                else "Gratis"
            )

            conexao = conectar_banco()

            executar(conexao, """
                UPDATE usuarios
                SET plano = ?,
                    mercado_pago_subscription_id = ?,
                    mercado_pago_status = ?
                WHERE id = ?
            """, (
                novo_plano,
                assinatura.get("id"),
                status,
                usuario_id
            ))

            conexao.commit()
            conexao.close()

        return "OK", 200

    # Cobranças recorrentes autorizadas.
    if tipo == "subscription_authorized_payment":

        try:
            resposta = requests.get(
                f"https://api.mercadopago.com/authorized_payments/{data_id}",
                headers=headers,
                timeout=30
            )
        except requests.RequestException:
            return "Erro temporário", 500

        if resposta.status_code != 200:
            return "OK", 200

        cobranca = resposta.json()

        usuario_id = cobranca.get("external_reference")
        pagamento = cobranca.get("payment") or {}
        pagamento_status = pagamento.get("status")

        if usuario_id:
            novo_plano = (
                "Pro"
                if pagamento_status == "approved"
                else "Gratis"
            )

            conexao = conectar_banco()

            executar(conexao, """
                UPDATE usuarios
                SET plano = ?
                WHERE id = ?
            """, (
                novo_plano,
                usuario_id
            ))

            conexao.commit()
            conexao.close()

        return "OK", 200

    # O Mercado Pago recomenda habilitar também notificações
    # de pagamentos. Neste MVP nós as reconhecemos e respondemos,
    # enquanto a situação do plano é controlada pelos eventos
    # de assinatura acima.
    if tipo == "payment":
        return "OK", 200

    return "OK", 200


# =====================================================
# PDF
# =====================================================

@app.route("/pdf/<int:id>")
def gerar_pdf(id):

    if not usuario_logado():
        return redirect("/login")

    conexao = conectar_banco()

    item = executar(conexao, """
        SELECT *
        FROM orcamentos

        WHERE id = ?
        AND usuario_id = ?
    """, (
        id,
        session["usuario_id"]
    )).fetchone()

    conexao.close()

    if not item:
        return "Orçamento não encontrado."

    buffer = BytesIO()

    pdf = canvas.Canvas(
        buffer,
        pagesize=A4
    )

    largura, altura = A4

    pdf.setFont(
        "Helvetica-Bold",
        22
    )

    pdf.drawString(
        50,
        altura - 60,
        item["empresa"]
    )

    pdf.setFont(
        "Helvetica",
        11
    )

    if item["telefone"]:

        pdf.drawString(
            50,
            altura - 85,
            f"WhatsApp: {item['telefone']}"
        )

    pdf.drawString(
        50,
        altura - 110,
        f"Orcamento #{item['id']}"
    )

    pdf.drawString(
        50,
        altura - 130,
        f"Data: {item['data']}"
    )

    pdf.line(
        50,
        altura - 150,
        540,
        altura - 150
    )

    pdf.drawString(
        50,
        altura - 190,
        f"Cliente: {item['cliente']}"
    )

    pdf.drawString(
        50,
        altura - 220,
        f"Servico: {item['servico']}"
    )

    pdf.drawString(
        50,
        altura - 250,
        f"Prazo: {item['prazo']} dias"
    )

    pdf.drawString(
        50,
        altura - 280,
        f"Validade: {item['validade']} dias"
    )

    pdf.drawString(
        50,
        altura - 310,
        f"Pagamento: {item['pagamento']}"
    )

    pdf.setFont(
        "Helvetica-Bold",
        18
    )

    pdf.drawString(
        50,
        altura - 360,
        f"Valor: R$ {formatar_valor(item['valor'])}"
    )

    pdf.save()

    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"orcamento_{id}.pdf",
        mimetype="application/pdf"
    )


# =====================================================
# SERVIDOR
# =====================================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
    )