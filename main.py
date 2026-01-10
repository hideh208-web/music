import os
import asyncio
import logging
import json
import discord
import sys
from discord import app_commands
from discord.ext import commands
from groq import Groq
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

# Flask server
app = Flask('')

# Start the bot when the Flask app starts
def run_bot_in_thread():
    # Set up a new event loop for this thread to avoid loop conflicts
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        # Use run_until_complete with the bot's start coroutine
        # but we need to make sure the loop is correctly handled.
        # discord.py's run() actually does this under the hood, 
        # but the error comes from aiohttp/asyncio interaction.
        bot.run(discord_token)
    except Exception as e:
        logger.error(f"Bot thread error: {e}")

bot_thread_started = False

@app.before_request
def start_bot_once():
    global bot_thread_started
    if not bot_thread_started:
        bot_thread_started = True
        t = Thread(target=run_bot_in_thread)
        t.daemon = True
        t.start()

@app.route('/')
def home():
    return "I'm alive!"

def run_flask():
    # Render provides the port via the PORT environment variable
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Starting keep-alive server on port {port}")
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

# Debug: Log Token presence (first 5 chars only for safety)
# logger.info(f"Discord Token present: {discord_token[:5]}...")
# logger.info(f"Groq API Key present: {groq_api_key[:5]}...")

# Groq Client
groq_client = Groq(api_key=groq_api_key)

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True # Required for serverinfo and userinfo
bot = commands.Bot(command_prefix="$", intents=intents)

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

        # Sync state
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
            # Format: Title | Author (Added by: Name)
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

@bot.event
async def on_wavelink_node_closed(node: wavelink.Node, disconnected: bool):
    if disconnected:
        logger.warning(f"Lavalink Node {node.uri} disconnected. Attempting to reconnect...")
        try:
            await node.connect()
        except Exception as e:
            logger.error(f"Reconnection to Lavalink Node {node.uri} failed: {e}")

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
async def on_wavelink_track_start(payload: wavelink.TrackStartEventPayload):
    player: wavelink.Player = payload.player
    track = payload.track

    # We no longer send a separate embed here if it was triggered by /play 
    # as /play now handles the initial response to clear the 'thinking' state.
    # However, for auto-playing next tracks in queue, we might still want it.

    if not hasattr(player, 'controller_message') or player.controller_message is None:
        if hasattr(player, 'home_channel'):
            embed = get_track_embed("Now Playing", track)
            view = MusicControlView(player)
            player.controller_message = await player.home_channel.send(embed=embed, view=view)

@bot.event
async def on_wavelink_track_end(payload: wavelink.TrackEndEventPayload):
    player: wavelink.Player = payload.player

    # Delete the old control panel
    if hasattr(player, 'controller_message') and player.controller_message:
        try:
            await player.controller_message.delete()
        except:
            pass
        player.controller_message = None

    # Wavelink 3.x handles QueueMode automatically. 
    # If a song ends and queue is not empty, it will play the next one if mode is normal/loop_all.
    # If mode is loop, it will replay the current one.
    
    if player.queue.is_empty and player.queue.mode == wavelink.QueueMode.normal:
        # 10 second auto-leave only if nothing else is playing/queued
        await asyncio.sleep(10)
        if not player.playing and player.queue.is_empty:
            await player.disconnect()
            if hasattr(player, 'home_channel'):
                await player.home_channel.send(embed=create_embed("Disconnected", "Queue ended, leaving voice channel after 10 seconds of inactivity.", discord.Color.blue()))

def load_channel_config():
    try:
        if not os.path.exists('channel_config.json'):
            with open('channel_config.json', 'w') as f:
                json.dump({"channels": {}}, f)
            return {"channels": {}}
        with open('channel_config.json', 'r') as f:
            data = json.load(f)
            if "channels" not in data:
                return {"channels": {}}
            return data
    except Exception as e:
        logger.error(f"Error loading config: {e}")
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
            # Fix thinking state
            return await interaction.followup.send(embed=create_embed("Not Found", f"No tracks found for: `{search}`", discord.Color.orange()))

        track = tracks[0]
        # Attach the requester to the track object
        track.requester = interaction.user

        # Deafen the bot when joining
        if not interaction.guild.voice_client:
            try:
                vc: wavelink.Player = await interaction.user.voice.channel.connect(cls=wavelink.Player, self_deaf=True)
            except asyncio.TimeoutError:
                return await interaction.followup.send(embed=create_embed("Connection Timeout", "Unable to connect to the voice channel. Please try again later.", discord.Color.red()))
        else:
            vc: wavelink.Player = interaction.guild.voice_client

        vc.home_channel = interaction.channel

        # Get track embed for immediate response
        embed = get_track_embed("Playing Now", track)
        embed.color = discord.Color.green()

        if vc.playing:
            await vc.queue.put_wait(track)
            embed.title = "Added to Queue"
            await interaction.followup.send(embed=embed)
        else:
            await vc.play(track)
            # Fix thinking state: acknowledge the play command with the actual track info
            await interaction.followup.send(embed=embed)    
    except Exception as e:
        # Fix thinking state
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

@bot.tree.command(name="join", description="Join your current voice channel")
async def join(interaction: discord.Interaction):
    if not interaction.user.voice:
        return await interaction.response.send_message(embed=create_embed("Error", "You need to join a voice channel first!", discord.Color.red()))

    try:
        await interaction.user.voice.channel.connect(cls=wavelink.Player, self_deaf=True)
        await interaction.response.send_message(embed=create_embed("Joined", f"✅ Connected to **{interaction.user.voice.channel.name}** (Deafened)", discord.Color.green()))
    except Exception as e:
        await interaction.response.send_message(embed=create_embed("Error", f"Could not connect: `{e}`", discord.Color.red()))

@bot.tree.command(name="filter", description="Apply audio filters")
@app_commands.describe(name="The filter to apply")
@app_commands.choices(name=[
    app_commands.Choice(name="Bassboost", value="bassboost"),
    app_commands.Choice(name="Nightcore", value="nightcore"),
    app_commands.Choice(name="8D", value="8d"),
    app_commands.Choice(name="Clear", value="clear")
])
async def filter_cmd(interaction: discord.Interaction, name: app_commands.Choice[str]):
    vc: wavelink.Player = interaction.guild.voice_client
    if not vc:
        return await interaction.response.send_message(embed=create_embed("Error", "I'm not connected to a voice channel! Join one first.", discord.Color.red()))

    filters = wavelink.Filters()
    filter_name = name.value

    if filter_name == "bassboost":
        filters.equalizer = wavelink.Equalizer.boost()
        msg = "🎸 **Bassboost** filter applied!"
    elif filter_name == "nightcore":
        filters.timescale.set(pitch=1.2, speed=1.2, rate=1.0)
        msg = "💨 **Nightcore** filter applied!"
    elif filter_name == "8d":
        # Fixed 8D logic: use low frequency oscillation on panning
        # wavelink rotation filter rotates the audio 360 degrees
        filters.rotation.set(rotation_hz=0.2)
        msg = "🌀 **8D Audio** filter applied!"
    elif filter_name == "clear":
        filters = wavelink.Filters()
        msg = "✨ Audio filters **cleared**!"

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
        await vc.stop()
        vc.queue.clear()
        await interaction.response.send_message(embed=create_embed("Stopped", "⏹️ Music has been stopped and the queue has been cleared.", discord.Color.blue()))
    else:
        await interaction.response.send_message(embed=create_embed("Error", "I'm not connected to any voice channel.", discord.Color.red()))

@bot.tree.command(name="leave", description="Make the bot leave the voice channel")
async def leave(interaction: discord.Interaction):
    vc: wavelink.Pla
