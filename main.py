import os
import asyncio
import logging
import json
import discord
from discord import app_commands
from discord.ext import commands
from groq import Groq
from flask import Flask
from threading import Thread
import wavelink

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Flask server
app = Flask('')

@app.route('/')
def home():
    return "I'm alive!"

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

# Load tokens
discord_token = os.environ.get('DISCORD_TOKEN')
groq_api_key = os.environ.get('GROQ_API_KEY')

if not discord_token or not groq_api_key:
    logger.error("Missing DISCORD_TOKEN or GROQ_API_KEY")
    exit(1)

# Groq Client
groq_client = Groq(api_key=groq_api_key)

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="?", intents=intents)

async def setup_hook():
    node = wavelink.Node(
        uri='http://ishaan.hidencloud.com:24590',
        password='KaAs',
        inactive_player_timeout=300
    )
    try:
        logger.info(f"Connecting to Lavalink: {node.uri}")
        await wavelink.Pool.connect(nodes=[node], client=bot)
        logger.info("Successfully connected to D-Radio Lavalink")
    except Exception as e:
        logger.error(f"Lavalink Error: {e}")
    
    # Sync slash commands globally
    logger.info("Syncing slash commands...")
    await bot.tree.sync()
    logger.info("Slash commands synced!")

bot.setup_hook = setup_hook

def load_channel_config():
    try:
        with open('channel_config.json', 'r') as f:
            return json.load(f)
    except:
        return {"channels": {}}

def save_channel_config(guild_id, channel_id):
    config = load_channel_config()
    if channel_id is None:
        config["channels"].pop(str(guild_id), None)
    else:
        config["channels"][str(guild_id)] = channel_id
    with open('channel_config.json', 'w') as f:
        json.dump(config, f)

@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user}")
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="AI & Music"))

@bot.tree.command(name="play", description="Play music")
async def play(interaction: discord.Interaction, search: str):
    if not interaction.user.voice:
        return await interaction.response.send_message("Join a voice channel first!")
    
    await interaction.response.defer()
    try:
        vc: wavelink.Player = interaction.guild.voice_client or await interaction.user.voice.channel.connect(cls=wavelink.Player)
        tracks = await wavelink.Playable.search(search)
        if not tracks:
            return await interaction.followup.send("No tracks found.")
        
        track = tracks[0]
        await vc.play(track)
        await interaction.followup.send(f"Playing: **{track.title}**")
    except Exception as e:
        await interaction.followup.send(f"Error: {e}")

@bot.tree.command(name="skip", description="Skip song")
async def skip(interaction: discord.Interaction):
    vc: wavelink.Player = interaction.guild.voice_client
    if vc and (vc.playing or not vc.queue.is_empty):
        await vc.skip()
        await interaction.response.send_message("Skipped.")
    else:
        await interaction.response.send_message("Nothing playing.")

@bot.tree.command(name="queue", description="Show queue")
async def queue(interaction: discord.Interaction):
    vc: wavelink.Player = interaction.guild.voice_client
    if not vc or vc.queue.is_empty:
        return await interaction.response.send_message("Queue empty.")
    queue_list = "\n".join([f"{i+1}. {t.title}" for i, t in enumerate(vc.queue)])
    await interaction.response.send_message(f"**Queue:**\n{queue_list[:1900]}")

@bot.tree.command(name="stop", description="Stop music")
async def stop(interaction: discord.Interaction):
    vc: wavelink.Player = interaction.guild.voice_client
    if vc:
        await vc.disconnect()
        await interaction.response.send_message("Stopped.")
    else:
        await interaction.response.send_message("Not playing.")

@bot.tree.command(name="setup", description="Setup AI channel")
async def setup_channel(interaction: discord.Interaction):
    save_channel_config(interaction.guild_id, interaction.channel_id)
    await interaction.response.send_message("AI channel set!", ephemeral=True)

async def get_ai_response(content):
    try:
        completion = await asyncio.to_thread(
            groq_client.chat.completions.create,
            messages=[{"role": "user", "content": content}],
            model="llama-3.3-70b-versatile",
        )
        return completion.choices[0].message.content
    except Exception as e:
        return f"AI Error: {e}"

@bot.event
async def on_message(message):
    if message.author.bot: return
    config = load_channel_config()
    channel_id = config["channels"].get(str(message.guild.id))
    if (channel_id and message.channel.id == channel_id) or bot.user.mentioned_in(message):
        async with message.channel.typing():
            content = message.content.replace(f'<@!{bot.user.id}>', '').replace(f'<@{bot.user.id}>', '').strip()
            response = await get_ai_response(content)
            for i in range(0, len(response), 2000):
                await message.reply(response[i:i+2000])
    await bot.process_commands(message)

if __name__ == "__main__":
    keep_alive()
    bot.run(discord_token)
