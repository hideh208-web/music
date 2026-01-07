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
bot = commands.Bot(command_prefix="$", intents=intents)

class MusicControlView(discord.ui.View):
    def __init__(self, player: wavelink.Player):
        super().__init__(timeout=None)
        self.player = player

    @discord.ui.button(label="Pause/Resume", style=discord.ButtonStyle.secondary, emoji="⏯️")
    async def toggle_pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        if not self.player.playing:
            return await interaction.followup.send("Nothing is playing.", ephemeral=True)
        
        await self.player.set_pause(not self.player.paused)
        status = "paused" if self.player.paused else "resumed"
        await interaction.followup.send(f"Music {status}!", ephemeral=True)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.primary, emoji="⏭️")
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        if not self.player.playing:
            return await interaction.followup.send("Nothing is playing.", ephemeral=True)
        
        await self.player.skip()
        await interaction.followup.send("Skipped the song!", ephemeral=True)

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, emoji="⏹️")
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self.player.disconnect()
        await interaction.followup.send("Stopped and disconnected!", ephemeral=True)

    @discord.ui.button(label="Queue", style=discord.ButtonStyle.secondary, emoji="📜")
    async def queue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        if self.player.queue.is_empty:
            return await interaction.followup.send("The queue is empty.", ephemeral=True)
        
        queue_list = "\n".join([f"{i+1}. {t.title}" for i, t in enumerate(self.player.queue)])
        await interaction.followup.send(f"**Current Queue:**\n{queue_list[:1900]}", ephemeral=True)

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
    
    logger.info("Syncing slash commands...")
    await bot.tree.sync()
    logger.info("Slash commands synced!")

bot.setup_hook = setup_hook

def create_embed(title, description, color=discord.Color.blue()):
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text="AI Music Bot • Powered by Groq & Wavelink")
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
    embed.set_footer(text="AI Music Bot • Powered by Groq & Wavelink")
    return embed

@bot.event
async def on_wavelink_track_start(payload: wavelink.TrackStartEventPayload):
    player: wavelink.Player = payload.player
    track = payload.track
    
    if hasattr(player, 'home_channel'):
        embed = get_track_embed("Now Playing", track)
        view = MusicControlView(player)
        player.controller_message = await player.home_channel.send(embed=embed, view=view)

@bot.event
async def on_wavelink_track_end(payload: wavelink.TrackEndEventPayload):
    player: wavelink.Player = payload.player
    
    # Delete the old control panel
    if hasattr(player, 'controller_message'):
        try:
            await player.controller_message.delete()
        except:
            pass

    if not player.queue.is_empty:
        next_track = await player.queue.get_wait()
        await player.play(next_track)

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

@bot.tree.command(name="play", description="Play music or add to queue")
async def play(interaction: discord.Interaction, search: str):
    if not interaction.user.voice:
        return await interaction.response.send_message(embed=create_embed("Error", "You need to join a voice channel first!", discord.Color.red()))
    
    await interaction.response.defer()
    try:
        vc: wavelink.Player = interaction.guild.voice_client or await interaction.user.voice.channel.connect(cls=wavelink.Player)
        vc.home_channel = interaction.channel
        
        tracks = await wavelink.Playable.search(search)
        if not tracks:
            return await interaction.followup.send(embed=create_embed("Not Found", f"No tracks found for: `{search}`", discord.Color.orange()))
        
        track = tracks[0]
        
        if vc.playing:
            await vc.queue.put_wait(track)
            embed = get_track_embed("Added to Queue", track)
            embed.color = discord.Color.green()
            await interaction.followup.send(embed=embed)
        else:
            await vc.play(track)
            # Silent background load, panel sent in track_start event
            pass
            
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

@bot.tree.command(name="filter", description="Apply audio filters (bassboost, nightcore, clear)")
async def filter_cmd(interaction: discord.Interaction, name: str):
    vc: wavelink.Player = interaction.guild.voice_client
    if not vc:
        return await interaction.response.send_message(embed=create_embed("Error", "I'm not connected to any voice channel.", discord.Color.red()))
    
    filters = wavelink.Filters()
    name = name.lower()
    
    if name == "bassboost":
        filters.equalizer = wavelink.Equalizer.boost()
        msg = "🎸 **Bassboost** filter applied!"
    elif name == "nightcore":
        filters.timescale.set(pitch=1.2, speed=1.2, rate=1.0)
        msg = "💨 **Nightcore** filter applied!"
    elif name == "clear":
        filters = wavelink.Filters()
        msg = "✨ Audio filters **cleared**!"
    else:
        return await interaction.response.send_message(embed=create_embed("Unknown Filter", "Available filters: `bassboost`, `nightcore`, `clear`", discord.Color.orange()))
    
    await vc.set_filters(filters)
    await interaction.response.send_message(embed=create_embed("Filter Applied", msg, discord.Color.blue()))

@bot.tree.command(name="skip", description="Skip the current song")
async def skip(interaction: discord.Interaction):
    vc: wavelink.Player = interaction.guild.voice_client
    if vc and (vc.playing or not vc.queue.is_empty):
        await vc.skip()
        await interaction.response.send_message(embed=create_embed("Skipped", "⏭️ The current track has been skipped."))
    else:
        await interaction.response.send_message(embed=create_embed("Nothing Playing", "There are no tracks to skip.", discord.Color.orange()))

@bot.tree.command(name="queue", description="Show the current music queue")
async def queue(interaction: discord.Interaction):
    vc: wavelink.Player = interaction.guild.voice_client
    if not vc or (not vc.playing and vc.queue.is_empty):
        return await interaction.response.send_message(embed=create_embed("Queue Empty", "The queue is currently empty.", discord.Color.orange()))
    
    description = ""
    if vc.playing:
        description += f"**Currently Playing:**\n{vc.current.title}\n\n"
    
    if not vc.queue.is_empty:
        description += "**Up Next:**\n"
        for i, t in enumerate(vc.queue):
            description += f"{i+1}. {t.title}\n"
            if i >= 9:
                description += f"... and {len(vc.queue) - 10} more"
                break
    
    await interaction.response.send_message(embed=create_embed("Music Queue", description or "Nothing in queue."))

@bot.tree.command(name="stop", description="Stop music and clear queue")
async def stop(interaction: discord.Interaction):
    vc: wavelink.Player = interaction.guild.voice_client
    if vc:
        await vc.disconnect()
        await interaction.response.send_message(embed=create_embed("Stopped", "⏹️ Music stopped and disconnected from voice channel.", discord.Color.blue()))
    else:
        await interaction.response.send_message(embed=create_embed("Error", "I'm not connected to any voice channel.", discord.Color.red()))

@bot.tree.command(name="setup", description="Configure this channel for AI interaction")
async def setup_channel(interaction: discord.Interaction):
    save_channel_config(interaction.guild_id, interaction.channel_id)
    await interaction.response.send_message(embed=create_embed("Setup Complete", "✅ This channel has been successfully configured for AI responses.", discord.Color.green()), ephemeral=True)

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
