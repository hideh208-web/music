import os
import asyncio
import logging
import json
import discord
import sys
import google.generativeai as genai
from discord import app_commands
from discord.ext import commands
from flask import Flask
from threading import Thread
import wavelink

# Ensure stdout is unbuffered for cloud logging
sys.stdout.reconfigure(line_buffering=True)

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Load tokens
discord_token = os.environ.get('DISCORD_TOKEN')
gemini_api_key = os.environ.get('GEMINI_API_KEY')

if not discord_token or not gemini_api_key:
    logger.error("Missing DISCORD_TOKEN or GEMINI_API_KEY")
    exit(1)

# Gemini Configuration
genai.configure(api_key=gemini_api_key)
model = genai.GenerativeModel('gemini-pro')

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True # Required for serverinfo and userinfo
bot = commands.Bot(command_prefix="$", intents=intents)

@bot.tree.command(name="chat", description="Chat with Gemini AI")
async def chat(interaction: discord.Interaction, message: str):
    await interaction.response.defer()
    try:
        response = model.generate_content(message)
        if response.text:
            content = response.text
            if len(content) > 1900:
                content = content[:1900] + "..."
            await interaction.followup.send(content)
        else:
            await interaction.followup.send("Gemini couldn't generate a response.")
    except Exception as e:
        logger.error(f"Gemini error: {e}")
        await interaction.followup.send(f"An error occurred: {str(e)}")

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if bot.user.mentioned_in(message):
        async with message.channel.typing():
            try:
                prompt = message.content.replace(f'<@!{bot.user.id}>', '').replace(f'<@{bot.user.id}>', '').strip()
                if not prompt:
                    prompt = "Hello!"
                response = model.generate_content(prompt)
                await message.reply(response.text if response.text else "I'm not sure what to say.")
            except Exception as e:
                logger.error(f"Gemini error: {e}")
                await message.reply("Sorry, I'm having trouble thinking right now.")

    await bot.process_commands(message)

# Music related classes and commands
class MusicControlView(discord.ui.View):
    def __init__(self, player: wavelink.Player):
        super().__init__(timeout=None)
        self.player = player

    @discord.ui.button(label="Pause", style=discord.ButtonStyle.secondary, emoji="⏸️")
    async def toggle_pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not self.player:
            return await interaction.followup.send("Player not found.", ephemeral=True)

        if not self.player.playing and not self.player.paused:
            return await interaction.followup.send("Nothing is playing.", ephemeral=True)

        if self.player.paused:
            await self.player.pause(False)
            button.label = "Pause"
            button.emoji = "⏸️"
            status = "resumed"
        else:
            await self.player.pause(True)
            button.label = "Resume"
            button.emoji = "▶️"
            status = "paused"

        await interaction.message.edit(view=self)
        await interaction.followup.send(f"Music {status}!", ephemeral=True)

    @discord.ui.button(label="Vol -", style=discord.ButtonStyle.secondary, emoji="🔉")
    async def volume_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not self.player:
             return await interaction.followup.send("Player not found.", ephemeral=True)
        current_vol = self.player.volume
        new_vol = max(0, current_vol - 10)
        await self.player.set_volume(new_vol)
        await interaction.followup.send(f"Volume decreased to {new_vol}%", ephemeral=True)

    @discord.ui.button(label="Vol +", style=discord.ButtonStyle.secondary, emoji="🔊")
    async def volume_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not self.player:
             return await interaction.followup.send("Player not found.", ephemeral=True)
        current_vol = self.player.volume
        new_vol = min(100, current_vol + 10)
        await self.player.set_volume(new_vol)
        await interaction.followup.send(f"Volume increased to {new_vol}%", ephemeral=True)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.primary, emoji="⏭️")
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not self.player or not self.player.playing:
            return await interaction.followup.send("Nothing is playing.", ephemeral=True)

        await self.player.skip()
        await interaction.followup.send("Skipped the song!", ephemeral=True)

    @discord.ui.button(label="Loop", style=discord.ButtonStyle.success, emoji="🔁")
    async def loop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not self.player or not self.player.playing:
            return await interaction.followup.send("Nothing is playing.", ephemeral=True)

        if not self.player.queue.mode == wavelink.QueueMode.normal:
            self.player.queue.mode = wavelink.QueueMode.normal
            msg = "Looping: **Off** ❌"
        elif self.player.queue.mode == wavelink.QueueMode.normal:
            self.player.queue.mode = wavelink.QueueMode.loop
            msg = "Looping: **Track** 🔂"
        elif self.player.queue.mode == wavelink.QueueMode.loop:
            self.player.queue.mode = wavelink.QueueMode.loop_all
            msg = "Looping: **Queue** 🔁"
        else:
            self.player.queue.mode = wavelink.QueueMode.normal
            msg = "Looping: **Off** ❌"

        await interaction.followup.send(msg, ephemeral=True)

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, emoji="⏹️")
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await self.player.disconnect()
        await interaction.followup.send("Stopped and disconnected!", ephemeral=True)

    @discord.ui.button(label="Queue", style=discord.ButtonStyle.secondary, emoji="📜")
    async def queue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if self.player.queue.is_empty:
            return await interaction.followup.send("The queue is empty.", ephemeral=True)

        upcoming = list(self.player.queue)[:10]
        queue_list = []
        for i, t in enumerate(upcoming):
            requester = getattr(t, 'requester', None)
            req_name = requester.name if requester else "Unknown"
            queue_list.append(f"`{i+1}.` **{t.title}** | {t.author} (Added by: {req_name})")

        final_list = "\n".join(queue_list)

        embed = discord.Embed(
            title="📜 Current Music Queue",
            description=f"Showing next {len(upcoming)} tracks:\n\n{final_list}",
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Total tracks in queue: {len(self.player.queue)}")

        await interaction.followup.send(embed=embed, ephemeral=True)

async def setup_hook():
    node = wavelink.Node(
        uri='http://ishaan.hidencloud.com:24590',
        password='KaAs',
        inactive_player_timeout=300
    )
    try:
        logger.info(f"Connecting to Lavalink: {node.uri}")
        await wavelink.Pool.connect(nodes=[node], client=bot)
        logger.info(f"Successfully connected to Lavalink Node: {node.uri}")
    except Exception as e:
        logger.error(f"Lavalink Connection Failed for {node.uri}: {e}")

    logger.info("Syncing slash commands...")
    try:
        await bot.tree.sync()
        logger.info("Slash commands synced!")
    except Exception as e:
        logger.error(f"Failed to sync slash commands: {e}")

bot.setup_hook = setup_hook

def create_embed(title, description, color=discord.Color.blue()):
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text="Powered by Hideout Team")
    return embed

def get_track_embed(title, track):
    seconds = track.length // 1000
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    duration = f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours > 0 else f"{minutes:02d}:{seconds:02d}"

    embed = discord.Embed(title=title, description=f"🎶 **{track.title}**", color=discord.Color.blue())
    embed.add_field(name="Author", value=track.author, inline=True)
    embed.add_field(name="Duration", value=duration, inline=True)
    if hasattr(track, 'artwork'):
        embed.set_thumbnail(url=track.artwork)
    embed.set_footer(text="Powered by Hideout Team")
    return embed

@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user}")
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="AI & Music"))

@bot.tree.command(name="play", description="Play music or add to queue")
async def play(interaction: discord.Interaction, search: str):
    if not interaction.user.voice:
        return await interaction.response.send_message(embed=create_embed("Error", "You need to join a voice channel first!", discord.Color.red()))

    await interaction.response.defer()
    try:
        vc: wavelink.Player = interaction.guild.voice_client or await interaction.user.voice.channel.connect(cls=wavelink.Player, self_deaf=True)
        vc.home_channel = interaction.channel

        tracks = await wavelink.Playable.search(search)
        if not tracks:
            return await interaction.followup.send(embed=create_embed("Not Found", f"No tracks found for: `{search}`", discord.Color.orange()))

        track = tracks[0]
        track.requester = interaction.user

        embed = get_track_embed("Playing Now", track)
        embed.color = discord.Color.green()

        if vc.playing:
            await vc.queue.put_wait(track)
            embed.title = "Added to Queue"
            await interaction.followup.send(embed=embed)
        else:
            await vc.play(track)
            await interaction.followup.send(embed=embed)    
    except Exception as e:
        await interaction.followup.send(embed=create_embed("Error", f"An error occurred: `{str(e)}`", discord.Color.red()))

@bot.tree.command(name="volume", description="Adjust music volume (0-100)")
async def volume(interaction: discord.Interaction, level: int):
    vc: wavelink.Player = interaction.guild.voice_client
    if not vc:
        return await interaction.response.send_message(embed=create_embed("Error", "I'm not connected to any voice channel.", discord.Color.red()))

    if not 0 <= level <= 100:
        return await interaction.response.send_message(embed=create_embed("Invalid Volume", "Please provide a volume level between 0 and 100.", discord.Color.orange()))

    await vc.set_volume(level)
    await interaction.response.send_message(embed=create_embed("Volume Updated", f"🔊 Volume has been set to **{level}%**", discord.Color.blue()))

@bot.tree.command(name="stop", description="Stop music and clear queue")
async def stop(interaction: discord.Interaction):
    vc: wavelink.Player = interaction.guild.voice_client
    if vc:
        await vc.stop()
        vc.queue.clear()
        await interaction.response.send_message(embed=create_embed("Stopped", "⏹️ Music has been stopped and the queue has been cleared.", discord.Color.blue()))
    else:
        await interaction.response.send_message(embed=create_embed("Error", "I'm not connected to any voice channel.", discord.Color.red()))

# Flask server
app = Flask('')

@app.route('/')
def home():
    return "I'm alive!"

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Starting keep-alive server on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

def run_bot():
    bot.run(discord_token)

if __name__ == "__main__":
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()
    run_bot()
