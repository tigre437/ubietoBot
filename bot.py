import discord
from discord.ext import commands
import sqlite3
import json
from datetime import datetime
import os
import re
from dotenv import load_dotenv

# --- TOKEN ---
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# --- DB HELPERS ---
# Obtener la carpeta donde está bot.py
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Base de datos en la misma carpeta
DB_NAME = os.path.join(BASE_DIR, "quiniela.db")

class PersistentViewBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
    # Buscar la jornada activa (la más reciente sin resultados)
        row = db_query("""
            SELECT jornada 
            FROM partidos 
            WHERE resultado IS NULL 
            ORDER BY jornada DESC 
            LIMIT 1
        """, fetch=True)

        if row:
            jornada_activa = row[0][0]
            self.add_view(QuinielaView(jornada_activa))
            print(f"✅ View registrada para la jornada {jornada_activa}")
        else:
            print("⚠️ No hay jornada activa, no se registró ninguna view persistente.")


bot = PersistentViewBot()

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        
        # --- tabla de jornadas ---
        c.execute("""
        CREATE TABLE IF NOT EXISTS jornadas (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            numero INTEGER UNIQUE,
            cerrada INTEGER DEFAULT 0
        )
        """)
        
        # --- tabla de partidos ---
        c.execute("""
        CREATE TABLE IF NOT EXISTS partidos (
            jornada INTEGER,
            numero INTEGER,
            titulo TEXT,
            resultado TEXT,
            activo INTEGER DEFAULT 1,
            PRIMARY KEY (jornada, numero)
        )
        """)

        # --- tabla de quinielas ---
        c.execute("""
        CREATE TABLE IF NOT EXISTS quinielas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id TEXT,
            jornada INTEGER,
            prediccion TEXT,
            fecha TIMESTAMP
        )
        """)

        # --- tabla de puntuaciones ---
        c.execute("""
        CREATE TABLE IF NOT EXISTS puntuaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id TEXT,
            jornada INTEGER,
            aciertos INTEGER,
            fecha TIMESTAMP
        )
        """)

        conn.commit()


def db_query(query, params=(), fetch=False, many=False):
    with sqlite3.connect(DB_NAME) as conn:
        cur = conn.cursor()
        if many:
            cur.executemany(query, params)
        else:
            cur.execute(query, params)
        if fetch:
            return cur.fetchall()
        conn.commit()

def jornada_bloqueada(jornada: int) -> bool:
    rows = db_query("SELECT cerrada FROM jornadas WHERE numero=?", (jornada,), fetch=True)
    return bool(rows) and rows[0][0] == 1


init_db()
temp_data = {}

# ---------- VALIDACIÓN ----------
def validar_marcador(valor: str) -> bool:
    return bool(re.match(r'^\d+-\d+$', valor))

# ---------- MODALES JORNADA ----------
class CrearJornadaModal1(discord.ui.Modal):
    def __init__(self, jornada: int):
        super().__init__(title=f"Crear Jornada {jornada} - Parte 1")
        self.jornada = jornada
        self.inputs = []
        for i in range(1, 6):
            campo = discord.ui.TextInput(label=f"Partido {i}", placeholder="Ej: Real Madrid vs Barcelona", required=True)
            self.add_item(campo)
            self.inputs.append(campo)

    async def on_submit(self, interaction: discord.Interaction):
        temp_data[interaction.user.id] = [campo.value.strip() for campo in self.inputs]
        await interaction.response.send_message(
            "Parte 1 guardada. Pulsa el botón para introducir los últimos 5 partidos.",
            view=CrearJornadaParte2View(self.jornada),
            ephemeral=True
        )

class CrearJornadaModal2(discord.ui.Modal):
    def __init__(self, jornada: int):
        super().__init__(title=f"Crear Jornada {jornada} - Parte 1")
        self.jornada = jornada
        self.inputs = []
        for i in range(6, 11):
            campo = discord.ui.TextInput(label=f"Partido {i}", placeholder="Ej: Mallorca vs Betis", required=True)
            self.add_item(campo)
            self.inputs.append(campo)

    async def on_submit(self, interaction: discord.Interaction):
        # Recuperar la parte 1 de los partidos y añadir la parte 2
        partidos = temp_data.get(interaction.user.id, []) + [campo.value.strip() for campo in self.inputs]

        # Guardar en la tabla partidos
        params = [(self.jornada, i, partido) for i, partido in enumerate(partidos, start=1)]
        db_query(
            "INSERT OR REPLACE INTO partidos (jornada, numero, titulo) VALUES (?, ?, ?)",
            params, many=True
        )

        # Registrar jornada en tabla jornadas si no existe
        existing = db_query("SELECT 1 FROM jornadas WHERE numero=?", (self.jornada,), fetch=True)
        if not existing:
            db_query("INSERT INTO jornadas (numero, cerrada) VALUES (?, 0)", (self.jornada,))

        # Enviar embed con los partidos
        embed = discord.Embed(
            title=f"📋 Quiniela Jornada {self.jornada}",
            description="Haz click en el botón para enviar tu pronóstico.",
            color=discord.Color.green()
        )
        for i, partido in enumerate(partidos, start=1):
            embed.add_field(name=f"Partido {i}", value=partido, inline=False)

        await interaction.response.send_message(embed=embed, view=QuinielaView(self.jornada))

class CrearJornadaParte2View(discord.ui.View):
    def __init__(self, jornada: int):
        super().__init__(timeout=None)
        self.jornada = jornada

    @discord.ui.button(label="Parte 2", style=discord.ButtonStyle.primary)
    async def parte2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CrearJornadaModal2(self.jornada))

class CrearJornadaView(discord.ui.View):
    def __init__(self, numero_jornada: int):
        super().__init__(timeout=None)
        self.numero_jornada = numero_jornada

    @discord.ui.button(label="Crear Jornada", style=discord.ButtonStyle.success)
    async def crear(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("⚠️ Solo administradores.", ephemeral=True)
            return
        
        await interaction.response.send_modal(CrearJornadaModal1(self.numero_jornada))

# ---------- MODALES QUINIELA ----------
class QuinielaModal1(discord.ui.Modal, title="Enviar Quiniela - Parte 1"):
    def __init__(self, jornada: int, partidos: list):
        super().__init__()
        self.jornada = jornada
        self.inputs = []
        for partido in partidos[:5]:
            campo = discord.ui.TextInput(label=partido, placeholder="Ej: 2-1", max_length=5)
            self.add_item(campo)
            self.inputs.append(campo)

    async def on_submit(self, interaction: discord.Interaction):
        pars = []
        for campo in self.inputs:
            valor = campo.value.strip()
            if not validar_marcador(valor):
                await interaction.response.send_message(f"⚠️ El resultado '{valor}' no es válido. Usa el formato 'X-Y'.", ephemeral=True)
                return
            pars.append(valor)

        temp_data[interaction.user.id] = pars
        await interaction.response.send_message("Parte 1 enviada.", view=QuinielaParte2View(self.jornada, temp_data[interaction.user.id]), ephemeral=True)

class QuinielaModal2(discord.ui.Modal, title="Enviar Quiniela - Parte 2"):
    def __init__(self, jornada: int, parte1: list, partidos: list):
        super().__init__()
        self.jornada = jornada
        self.parte1 = parte1
        self.inputs = []
        for partido in partidos[5:]:
            campo = discord.ui.TextInput(label=partido, placeholder="Ej: 1-1", max_length=5)
            self.add_item(campo)
            self.inputs.append(campo)

    async def on_submit(self, interaction: discord.Interaction):
        if jornada_bloqueada(self.jornada):
            await interaction.response.send_message("⛔ Jornada bloqueada.", ephemeral=True)
            return

        parte2 = []
        for campo in self.inputs:
            valor = campo.value.strip()
            if not validar_marcador(valor):
                await interaction.response.send_message(f"⚠️ El resultado '{valor}' no es válido.", ephemeral=True)
                return
            parte2.append(valor)

        predicciones = self.parte1 + parte2
        usuario_id = str(interaction.user.id)
        existe = db_query("SELECT 1 FROM quinielas WHERE usuario_id=? AND jornada=?", (usuario_id, self.jornada), fetch=True)
        if existe:
            db_query("UPDATE quinielas SET prediccion=?, fecha=? WHERE usuario_id=? AND jornada=?", (json.dumps(predicciones), datetime.now(), usuario_id, self.jornada))
            msg = "✅ Quiniela actualizada."
        else:
            db_query("INSERT INTO quinielas (usuario_id, jornada, prediccion, fecha) VALUES (?, ?, ?, ?)", (usuario_id, self.jornada, json.dumps(predicciones), datetime.now()))
            msg = "✅ Quiniela registrada."
        await interaction.response.send_message(msg, ephemeral=True)

class QuinielaParte2View(discord.ui.View):
    def __init__(self, jornada: int, parte1: list):
        super().__init__(timeout=None)
        self.jornada = jornada
        self.parte1 = parte1

    @discord.ui.button(label="Parte 2", style=discord.ButtonStyle.primary)
    async def parte2(self, interaction: discord.Interaction, button: discord.ui.Button):
        rows = db_query("SELECT titulo FROM partidos WHERE jornada=? ORDER BY numero", (self.jornada,), fetch=True)
        partidos = [row[0] for row in rows]
        if len(partidos) < 10:
            await interaction.response.send_message("⚠️ Faltan partidos para esta jornada.", ephemeral=True)
            return
        await interaction.response.send_modal(QuinielaModal2(self.jornada, self.parte1, partidos))

class QuinielaView(discord.ui.View):
    def __init__(self, jornada: int):
        super().__init__(timeout=None)
        self.jornada = jornada

        # Crear el botón con custom_id único
        boton = discord.ui.Button(
            label="Enviar Quiniela",
            style=discord.ButtonStyle.primary,
            custom_id=f"persistent_view:quiniela_{jornada}"
        )
        boton.callback = self.enviar  # asignar función
        self.add_item(boton)

    async def enviar(self, interaction: discord.Interaction):
        usuario_id = str(interaction.user.id)

        rows = db_query("SELECT titulo FROM partidos WHERE jornada=? ORDER BY numero", (self.jornada,), fetch=True)
        partidos = [row[0] for row in rows]
        if not partidos:
            await interaction.response.send_message("⚠️ No hay partidos.", ephemeral=True)
            return
        if jornada_bloqueada(self.jornada):
            await interaction.response.send_message("⛔ Jornada bloqueada.", ephemeral=True)
            return

        rows = db_query("SELECT prediccion FROM quinielas WHERE usuario_id=? AND jornada=?", (usuario_id, self.jornada), fetch=True)
        if rows:
            try:
                predicciones = json.loads(rows[0][0])
            except json.JSONDecodeError:
                predicciones = rows[0][0].split(",")
            await interaction.response.send_message(
                "✏️ Ya has enviado una quiniela para esta jornada. Puedes editarla aquí:",
                view=EditarQuinielaButton(self.jornada, predicciones),
                ephemeral=True
            )
        else:
            await interaction.response.send_modal(QuinielaModal1(self.jornada, partidos))



# ---------- MODALES RESULTADOS (2 PARTES) ----------
class ResultadosModal1(discord.ui.Modal, title="Resultados - Parte 1"):
    def __init__(self, jornada: int, partidos: list):
        super().__init__()
        self.jornada = jornada
        self.partidos = partidos
        self.inputs = []
        for partido in partidos[:5]:
            campo = discord.ui.TextInput(label=partido, placeholder="Ej: 2-1", max_length=5)
            self.add_item(campo)
            self.inputs.append(campo)

    async def on_submit(self, interaction: discord.Interaction):
        temp_data[f"resultados_{interaction.user.id}"] = [campo.value.strip() for campo in self.inputs]
        await interaction.response.send_message("Parte 1 guardada.", view=ResultadosParte2View(self.jornada, self.partidos), ephemeral=True)

class ResultadosModal2(discord.ui.Modal, title="Resultados - Parte 2"):
    def __init__(self, jornada: int, partidos: list):
        super().__init__()
        self.jornada = jornada
        self.partidos = partidos
        self.inputs = []
        for partido in partidos[5:]:
            campo = discord.ui.TextInput(label=partido, placeholder="Ej: 1-1", max_length=5)
            self.add_item(campo)
            self.inputs.append(campo)

    async def on_submit(self, interaction: discord.Interaction):
        parte1 = temp_data.get(f"resultados_{interaction.user.id}", [])
        resultados = parte1 + [campo.value.strip() for campo in self.inputs]

        for valor in resultados:
            if not validar_marcador(valor):
                await interaction.response.send_message(f"⚠️ El resultado '{valor}' no es válido.", ephemeral=True)
                return

        for i, res in enumerate(resultados, start=1):
            db_query("UPDATE partidos SET resultado=? WHERE jornada=? AND numero=?", (res, self.jornada, i))

        await interaction.response.send_message(f"✅ Resultados de la jornada {self.jornada} guardados.", ephemeral=True)

class ResultadosParte2View(discord.ui.View):
    def __init__(self, jornada: int, partidos: list):
        super().__init__(timeout=None)
        self.jornada = jornada
        self.partidos = partidos

    @discord.ui.button(label="Parte 2", style=discord.ButtonStyle.primary)
    async def parte2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ResultadosModal2(self.jornada, self.partidos))

class ResultadosView(discord.ui.View):
    def __init__(self, jornada: int):
        super().__init__(timeout=None)
        self.jornada = jornada

    @discord.ui.button(label="Introducir Resultados", style=discord.ButtonStyle.danger)
    async def introducir(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("⚠️ Solo administradores.", ephemeral=True)
            return
        rows = db_query("SELECT titulo FROM partidos WHERE jornada=? ORDER BY numero", (self.jornada,), fetch=True)
        partidos = [r[0] for r in rows]
        if len(partidos) != 10:
            await interaction.response.send_message("⚠️ Debe haber 10 partidos cargados.", ephemeral=True)
            return
        await interaction.response.send_modal(ResultadosModal1(self.jornada, partidos))

# ---------- COMANDOS ----------

@bot.command()
@commands.has_permissions(administrator=True)
async def crearjornada(ctx, numero: int):
    """
    Crea una nueva jornada con un número específico.
    """
    # Comprobar si la jornada ya existe
    existing = db_query("SELECT 1 FROM jornadas WHERE numero=?", (numero,), fetch=True)
    if existing:
        await ctx.send(f"⚠️ La jornada {numero} ya existe.")
        return

    # Crear la view pasando el número de la jornada
    await ctx.send(
        f"📅 Pulsa el botón para crear la jornada {numero}:",
        view=CrearJornadaView(numero)
    )


@bot.command()
@commands.has_permissions(administrator=True)
async def borrarjornada(ctx, jornada: int):
    # Comprobamos si existe la jornada
    rows = db_query("SELECT 1 FROM partidos WHERE jornada=?", (jornada,), fetch=True)
    if not rows:
        await ctx.send(f"⚠️ La jornada {jornada} no existe en la base de datos.")
        return

    # Borramos datos en cascada
    db_query("DELETE FROM partidos WHERE jornada=?", (jornada,))
    db_query("DELETE FROM quinielas WHERE jornada=?", (jornada,))
    db_query("DELETE FROM puntuaciones WHERE jornada=?", (jornada,))

    await ctx.send(f"🗑️ Jornada {jornada} y todos sus datos han sido eliminados.")


@bot.command()
@commands.has_permissions(administrator=True)
async def resultados(ctx, jornada: int):
    await ctx.send(f"⚽ Introducir resultados para Jornada {jornada}:", view=ResultadosView(jornada))

@bot.command()
@commands.has_permissions(administrator=True)
async def corregir(ctx, jornada: int):
    # Obtener todos los partidos con su estado de activo
    partidos = db_query(
        "SELECT resultado, activo FROM partidos WHERE jornada=? ORDER BY numero",
        (jornada,), fetch=True
    )

    if not partidos or all(r[0] is None or r[1] == 0 for r in partidos):
        await ctx.send("⚠️ Faltan resultados en esta jornada o todos los partidos están suspendidos.")
        return

    quinielas = db_query("SELECT usuario_id, prediccion FROM quinielas WHERE jornada=?", (jornada,), fetch=True)
    if not quinielas:
        await ctx.send("ℹ️ No hay quinielas registradas para esta jornada.")
        return

    ranking = []
    status_msg = await ctx.send(f"🔄 Corrigiendo {len(quinielas)} quinielas... 0/{len(quinielas)}")

    for idx, (usuario_id, pred_json) in enumerate(quinielas, start=1):
        try:
            predicciones = json.loads(pred_json)
        except json.JSONDecodeError:
            predicciones = []

        puntos = 0
        for i, (resultado, activo) in enumerate(partidos):
            if not activo or not resultado:  # ignorar partidos suspendidos
                continue
            try:
                pred_local, pred_visitante = map(int, predicciones[i].split("-"))
                real_local, real_visitante = map(int, resultado.split("-"))
            except (ValueError, IndexError):
                continue

            # 1 punto por resultado correcto
            if (pred_local > pred_visitante and real_local > real_visitante) or \
               (pred_local < pred_visitante and real_local < real_visitante) or \
               (pred_local == pred_visitante and real_local == real_visitante):
                puntos += 1

            # +3 puntos extra por marcador exacto
            if pred_local == real_local and pred_visitante == real_visitante:
                puntos += 3

        ranking.append((usuario_id, puntos))
        db_query(
            "INSERT INTO puntuaciones (usuario_id, jornada, aciertos, fecha) VALUES (?, ?, ?, ?)",
            (usuario_id, jornada, puntos, datetime.now())
        )

        if idx % 5 == 0 or idx == len(quinielas):
            await status_msg.edit(content=f"🔄 Corrigiendo {len(quinielas)} quinielas... {idx}/{len(quinielas)}")

    ranking.sort(key=lambda x: x[1], reverse=True)
    top25 = ranking[:25]

    embed = discord.Embed(title=f"🏆 Resultados Jornada {jornada}", color=discord.Color.gold())
    for usuario_id, puntos in top25:
        try:
            user = await bot.fetch_user(int(usuario_id))
            embed.add_field(name=f"{user.name} ({user.mention})", value=f"Puntos: **{puntos}**", inline=False)
        except:
            embed.add_field(name=f"Usuario {usuario_id}", value=f"Puntos: **{puntos}**", inline=False)

    await ctx.send(embed=embed)





@bot.command()
async def verquiniela(ctx, jornada: int):
    if jornada == None:
        await ctx.send("🔎 Debes especificar la jornada de la quiniela que quieres ver. Ej: `!verquiniela 1`")
        return
    usuario_id = str(ctx.author.id)
    rows = db_query("SELECT prediccion, fecha FROM quinielas WHERE usuario_id=? AND jornada=?", (usuario_id, jornada), fetch=True)
    if not rows:
        await ctx.send("🔎 No tienes quiniela guardada para esta jornada.")
        return
    pred, fecha = rows[0]
    try:
        lista = json.loads(pred)
    except json.JSONDecodeError:
        lista = pred.split(",")
    texto = "\n".join([f"{i+1}. {v}" for i, v in enumerate(lista)])
    embed = discord.Embed(title=f"📝 Tu quiniela - Jornada {jornada}", description=texto, color=discord.Color.blue())
    embed.set_footer(text=f"Última edición: {fecha}")
    await ctx.send(embed=embed)



# ---------- EDITAR QUINIELA ----------

class EditarQuinielaButton(discord.ui.View):
    def __init__(self, jornada: int, predicciones: list):
        super().__init__(timeout=None)
        self.jornada = jornada
        self.predicciones = predicciones

    @discord.ui.button(label="Editar quiniela", style=discord.ButtonStyle.primary)
    async def button_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EditarQuinielaModal1(self.jornada, self.predicciones))

class EditarQuinielaModal1(discord.ui.Modal, title="Editar Quiniela - Parte 1"):
    def __init__(self, jornada: int, predicciones: list):
        super().__init__()
        self.jornada = jornada
        self.inputs = []
        for i, val in enumerate(predicciones[:5]):
            campo = discord.ui.TextInput(
                label=f"Partido {i+1}",
                placeholder="Ej: 2-1",
                max_length=5,
                default=val
            )
            self.add_item(campo)
            self.inputs.append(campo)

    async def on_submit(self, interaction: discord.Interaction):
        pars = []
        for campo in self.inputs:
            valor = campo.value.strip()
            if not validar_marcador(valor):
                await interaction.response.send_message(f"⚠️ El resultado '{valor}' no es válido. Usa el formato 'X-Y'.", ephemeral=True)
                return
            pars.append(valor)
        temp_data[interaction.user.id] = [campo.value.strip() for campo in self.inputs]
        await interaction.response.send_message(
            "Parte 1 editada. Pulsa el botón para continuar con los últimos 5 partidos.",
            view=EditarQuinielaParte2View(self.jornada, temp_data[interaction.user.id]),
            ephemeral=True
        )

class EditarQuinielaModal2(discord.ui.Modal, title="Editar Quiniela - Parte 2"):
    def __init__(self, jornada: int, parte1: list, predicciones: list):
        super().__init__()
        self.jornada = jornada
        self.parte1 = parte1
        self.inputs = []
        for i, val in enumerate(predicciones[5:]):
            campo = discord.ui.TextInput(
                label=f"Partido {i+6}",
                placeholder="Ej: 1-1",
                max_length=5,
                default=val
            )
            self.add_item(campo)
            self.inputs.append(campo)

    async def on_submit(self, interaction: discord.Interaction):
        pars = []
        for campo in self.inputs:
            valor = campo.value.strip()
            if not validar_marcador(valor):
                await interaction.response.send_message(f"⚠️ El resultado '{valor}' no es válido. Usa el formato 'X-Y'.", ephemeral=True)
                return
            pars.append(valor)
        parte2 = [campo.value.strip() for campo in self.inputs]
        predicciones_nuevas = self.parte1 + parte2
        usuario_id = str(interaction.user.id)
        db_query(
            "UPDATE quinielas SET prediccion=?, fecha=? WHERE usuario_id=? AND jornada=?",
            (json.dumps(predicciones_nuevas), datetime.now(), usuario_id, self.jornada)
        )
        await interaction.response.send_message("✅ Quiniela actualizada.", ephemeral=True)

class EditarQuinielaParte2View(discord.ui.View):
    def __init__(self, jornada: int, parte1: list):
        super().__init__(timeout=None)
        self.jornada = jornada
        self.parte1 = parte1

    @discord.ui.button(label="Parte 2", style=discord.ButtonStyle.primary)
    async def parte2(self, interaction: discord.Interaction, button: discord.ui.Button):
        usuario_id = str(interaction.user.id)
        rows = db_query("SELECT prediccion FROM quinielas WHERE usuario_id=? AND jornada=?", (usuario_id, self.jornada), fetch=True)
        if not rows:
            await interaction.response.send_message("⚠️ No se encontró tu quiniela.", ephemeral=True)
            return
        try:
            predicciones = json.loads(rows[0][0])
        except json.JSONDecodeError:
            predicciones = rows[0][0].split(",")
        await interaction.response.send_modal(EditarQuinielaModal2(self.jornada, self.parte1, predicciones))

# ---------- COMANDO ----------
@bot.command()
async def editarquiniela(ctx, jornada:int):
    if jornada == None:
        await ctx.send("⚠️ Debes especificar una jornada. Ej: !editarquiniela 1")
        return
    usuario_id = str(ctx.author.id)
    if ctx.guild is not None:
        await ctx.message.delete()
    rows = db_query(
        "SELECT prediccion FROM quinielas WHERE usuario_id=? AND jornada=?",
        (usuario_id, jornada),
        fetch=True
    )
    if not rows:
        await ctx.send("⚠️ No tienes quiniela registrada para esta jornada.")
        return

    if jornada_bloqueada(jornada):
        await ctx.send("⛔ Esta jornada está cerrada.")
        return

    try:
        predicciones = json.loads(rows[0][0])
    except json.JSONDecodeError:
        predicciones = rows[0][0].split(",")

    # Abrimos DM
    try:
        dm = await ctx.author.create_dm()
        await dm.send(
            "✏️ Pulsa el botón para editar tu quiniela:",
            view=EditarQuinielaButton(jornada, predicciones)
        )
        if ctx.guild is not None:
            await ctx.send("✅ Te he enviado un mensaje privado para editar tu quiniela.",delete_after=4)
    except discord.Forbidden:
        await ctx.send("⚠️ No puedo enviarte mensajes privados. Revisa tu configuración de privacidad.")

@bot.command()
@commands.has_permissions(administrator=True)
async def suspender_partido(ctx, jornada: int, numero: int, estado: int):
    """
    suspender_partido 5 3 suspendido
    suspender_partido 5 3 activo
    """
    db_query("UPDATE partidos SET activo=? WHERE jornada=? AND numero=?", (estado, jornada, numero))
    await ctx.send(f"✅ Partido {numero} de la jornada {jornada} marcado como {estado}.")

@bot.command()
@commands.has_permissions(administrator=True)
async def cerrar_quiniela(ctx, jornada: int):
    # Comprobar si la jornada existe
    rows = db_query("SELECT cerrada FROM jornadas WHERE numero=?", (jornada,), fetch=True)
    if not rows:
        await ctx.send("❌ No existe una jornada con ese número.")
    else:
        # Si existe, actualizar a cerrada
        db_query("UPDATE jornadas SET cerrada=1 WHERE numero=?", (jornada,))
        await ctx.send(f"Jornada {jornada} marcada como cerrada ✅")


@bot.command()
@commands.has_permissions(administrator=True)
async def abrir_quiniela(ctx, jornada: int):
    # Comprobar si la jornada existe
    rows = db_query("SELECT cerrada FROM jornadas WHERE numero=?", (jornada,), fetch=True)
    if not rows:
        await ctx.send("❌ No existe una jornada con ese número.")
    else:
        # Si existe, actualizar a abierta
        db_query("UPDATE jornadas SET cerrada=0 WHERE numero=?", (jornada,))
        await ctx.send(f"Jornada {jornada} marcada como abierta ✅")



bot.run(TOKEN)
