import os
import time
import discord
from discord.ext import commands
from flask import Flask
from threading import Thread
import aiohttp
import json
import re
import uuid

# -------------------- FLASK KEEP ALIVE --------------------
app = Flask("")

@app.route("/")
def home():
    return "your bot is alive!"

def run():
    app.run(host="0.0.0.0", port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# -------------------- CONFIG --------------------
SHAPESINC_API_KEY = "SHAPESINCAPI"
SHAPE_MODEL = "shapesinc/faputa-517i"
DISCORD_TOKEN = "DISCORDTOKEN"
GUILD_ID = 1346509622458191963
CHANNEL_ID = 1415501730652749944

if not SHAPESINC_API_KEY or not SHAPE_MODEL or not DISCORD_TOKEN or not GUILD_ID or not CHANNEL_ID:
    raise ValueError("Faltan variables de configuración requeridas")

print("Shape model:", SHAPE_MODEL)
print("Shapes API Key prefix:", SHAPESINC_API_KEY[:8] + "...")

# -------------------- DISCORD BOT --------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='>', intents=intents)

last_request_time = 0
cooldown_seconds = 5

# -------------------- MEMORIAS --------------------
conversaciones_canal = {}
ids_virtuales = {}
memoria_global = {}  # memoria global por canal
alias_usuarios = {}  # mapping consistente usuario -> alias

MAX_TURNOS = 30   # últimos turnos a considerar
MAX_TOKENS = 600  # tokens para memoria + contexto

def get_historial_canal(channel_id):
    if channel_id not in conversaciones_canal:
        conversaciones_canal[channel_id] = []
    return conversaciones_canal[channel_id]

def get_shapes_ids(user_id, channel_id):
    if user_id not in ids_virtuales:
        ids_virtuales[user_id] = {
            "user": f"{user_id}-{uuid.uuid4()}",
            "channel": f"{channel_id}-{uuid.uuid4()}"
        }
    return ids_virtuales[user_id]

def get_alias(user):
    if user.id not in alias_usuarios:
        alias_usuarios[user.id] = f"@User{str(user.id)[-4:]}"
    return alias_usuarios[user.id]

def quitar_duplicados(turnos):
    vistos = set()
    unicos = []
    for t in turnos:
        key = (t["role"], t["content"])
        if key not in vistos:
            unicos.append(t)
            vistos.add(key)
    return unicos

def clean_response(text):
    text = re.sub(r".*traducción de la pregunta[:\-]?.*\n?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\(.*[Tt]raducci[óo]n.*\)", "", text)
    text = re.sub(r"\(.*[Nn]ota del sistema.*\)", "", text)
    text = re.sub(r"\(.*[Tt]raduciendo.*\)", "", text)
    return text.replace("\r", "").strip()

def reemplazar_menciones(content, message):
    for user in message.mentions:
        alias = get_alias(user)
        content = content.replace(f"<@{user.id}>", alias)
        content = content.replace(f"<@!{user.id}>", alias)
    return content

def guardar_memoria_global(channel_id, key, subkey, value):
    if channel_id not in memoria_global:
        memoria_global[channel_id] = {}
    if key not in memoria_global[channel_id]:
        memoria_global[channel_id][key] = {}
    memoria_global[channel_id][key][subkey] = value

# ----------------- MEMORIA GLOBAL COMO HECHOS CONOCIDOS -----------------
def generar_memoria_texto(channel_id):
    if channel_id not in memoria_global:
        return ""
    texto = "Hechos conocidos:\n"
    for key, subdict in memoria_global[channel_id].items():
        for subkey, value in subdict.items():
            # Por ejemplo: "@User1234 se llama Fapu" en lugar de "Apodo de @User1234: Fapu"
            if key.lower() == "apodo":
                texto += f"{subkey} se llama {value}\n"
            else:
                texto += f"{subkey} {key.lower()}: {value}\n"
    return texto.strip()

# ----------------- DETECCIÓN AUTOMÁTICA FLEXIBLE -----------------
def detectar_datos_usuario(texto_usuario, alias, channel_id):
    texto_lower = texto_usuario.lower()

    # Color favorito
    match_color = re.search(r"(mi )?color favorito (es|:)? (\w+)", texto_lower)
    if match_color:
        color = match_color.group(3)
        guardar_memoria_global(channel_id, "Color favorito", alias, color)

    # Edad
    match_edad = re.search(r"(tengo|cumplo) (\d{1,3}) (años|año)", texto_lower)
    if match_edad:
        edad = match_edad.group(2)
        guardar_memoria_global(channel_id, "Edad", alias, edad)

    # Apodo / Nombre
    match_apodo = re.search(r"(me llaman|me dicen|mi apodo es|mi nombre es) (\w+)", texto_lower)
    if match_apodo:
        apodo = match_apodo.group(2)
        guardar_memoria_global(channel_id, "Apodo", alias, apodo)

    # Gustos / hobbies
    match_gustos = re.search(r"me gusta(n)? (.+)", texto_lower)
    if match_gustos:
        gustos = match_gustos.group(2)
        guardar_memoria_global(channel_id, "Gustos", alias, gustos)

# ----------------- CONSTRUIR PAYLOAD -----------------
def construir_payload(system_message, historial_canal, channel_id):
    mensajes_contexto = historial_canal[-MAX_TURNOS:]
    mensajes_contexto = quitar_duplicados(mensajes_contexto)

    memoria_texto = generar_memoria_texto(channel_id)
    if memoria_texto:
        print(f"[DEBUG] Memoria global canal {channel_id}:\n{memoria_texto}")

    mensajes_para_modelo = [system_message]
    if memoria_texto:
        mensajes_para_modelo.append({"role": "system", "content": memoria_texto})
    mensajes_para_modelo.extend(mensajes_contexto)

    payload = {
        "model": SHAPE_MODEL,
        "messages": mensajes_para_modelo,
        "max_tokens": MAX_TOKENS,
    }
    return payload

# -------------------- COMANDOS --------------------
@bot.command(name="reset")
async def reset(ctx):
    conversaciones_canal[ctx.channel.id] = []
    ids_virtuales[ctx.author.id] = {
        "user": f"{ctx.author.id}-{uuid.uuid4()}",
        "channel": f"{ctx.channel.id}-{uuid.uuid4()}"
    }
    memoria_global.pop(ctx.channel.id, None)
    await ctx.send(f"{ctx.author.mention}, la memoria global de este canal ha sido reseteada, sosu!")

@bot.command(name="resetall")
@commands.has_permissions(administrator=True)
async def resetall(ctx):
    conversaciones_canal.clear()
    ids_virtuales.clear()
    memoria_global.clear()
    await ctx.send(f"Todas las memorias globales de Faputa han sido reseteadas por {ctx.author.mention}, sosu!")

@resetall.error
async def resetall_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(f"{ctx.author.mention}, no tienes permisos para usar este comando, sosu!")

# -------------------- EVENTOS --------------------
@bot.event
async def on_ready():
    print(f"Bot conectado como: {bot.user}")
    print("Comandos registrados:", [cmd.name for cmd in bot.commands])

@bot.event
async def on_message(message):
    global last_request_time

    if message.author.bot:
        return
    
    mentioned = bot.user.mentioned_in(message)
    replied = (
        message.reference
        and message.reference.resolved
        and message.reference.resolved.author
        and message.reference.resolved.author.id == bot.user.id
    )

    if not (mentioned or replied):
        await bot.process_commands(message)
        return

    if message.guild and message.guild.id == GUILD_ID and message.channel.id == CHANNEL_ID:
        now = time.time()
        if now - last_request_time < cooldown_seconds:
            await message.channel.send(f"shaan! a Faputa le da vueltas la cabeza, baja el ritmo, sosu! ({cooldown_seconds}s cooldown)")
        else:
            last_request_time = now
            async with message.channel.typing():
                try:
                    historial_canal = get_historial_canal(message.channel.id)
                    texto_usuario = reemplazar_menciones(message.content, message)
                    historial_canal.append({"role": "user", "content": f"[{message.author.id}] {texto_usuario}"})

                    alias = get_alias(message.author)
                    detectar_datos_usuario(texto_usuario, alias, message.channel.id)

                    system_message = {
                        "role": "system",
                        "content": (
                            "Eres Faputa, la Princesa del Abismo. "
                            "Responde siempre de forma natural en español, incluyendo acciones y gestos. "
                            "Nunca traduzcas ni uses palabras extranjeras. "
                            "Nunca digas 'translation of the prompt' ni similares. "
                            "No repitas frases anteriores ni lo que ya dijiste en turnos pasados. "
                            "Distingue quién habla por su ID único, y reconoce menciones por alias (@UserXXXX), "
                            "pero nunca uses nombres reales."
                        )
                    }

                    payload = construir_payload(system_message, historial_canal, message.channel.id)

                    shapes_ids = get_shapes_ids(message.author.id, message.channel.id)
                    url = "https://api.shapes.inc/v1/chat/completions"
                    headers = {
                        "Authorization": f"Bearer {SHAPESINC_API_KEY}",
                        "Content-Type": "application/json",
                        "X-User-Id": shapes_ids["user"],
                        "X-Channel-Id": shapes_ids["channel"],
                    }

                    async with aiohttp.ClientSession() as session:
                        async with session.post(url, headers=headers, json=payload) as resp:
                            status = resp.status
                            text = await resp.text()
                            
                    if status != 200:
                        await message.reply(f"hubo un error al responder (status {status}). revisa la consola.")
                    else:
                        data = json.loads(text)
                        respuesta = ""
                        if "choices" in data and len(data["choices"]) > 0:
                            respuesta = data["choices"][0]["message"]["content"].strip()

                        respuesta = clean_response(respuesta)
                        historial_canal.append({"role": "assistant", "content": respuesta})

                        await message.reply(respuesta or "shapes devolvió respuesta vacía, mira la consola.")
                except Exception as e:
                    print("error al consultar shapes:", repr(e))
                    await message.reply("hubo un error al responder, sosu")

    await bot.process_commands(message)

# -------------------- RUN --------------------
if __name__ == "__main__":
    keep_alive()
    bot.run(DISCORD_TOKEN)
