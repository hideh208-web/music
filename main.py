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
        if not self.player or not self.player.playing:
            return await interaction.followup.send("Nothing is playing.", ephemeral=True)
        
        await self.player.set_pause(not self.player.paused)
        if self.player.paused:
            button.label = "Resume"
            button.emoji = "▶️"
            status = "paused"
        else:
            button.label = "Pause"
            button.emoji = "⏸️"
            status = "resumed"
        
        await interaction.message.edit(view=self)
        await interaction.followup.send(f"Music {status}!", ephemeral=True)

    @discord.ui.button(label="Vol -", style=discord.ButtonStyle.secondary, emoji="🔉")
    async def volume_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        current_vol = self.player.volume
        new_vol = max(0, current_vol - 10)
        await self.player.set_volume(new_vol)
        await interaction.followup.send(f"Volume decreased to {new_vol}%", ephemeral=True)

    @discord.ui.button(label="Vol +", style=discord.ButtonStyle.secondary, emoji="🔊")
    async def volume_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
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
        
        if not hasattr(self.player.queue, 'loop_all'):
            self.player.queue.loop_all = False
            
        if not self.player.queue.loop and not self.player.queue.loop_all:
            self.player.queue.loop = True
            msg = "Looping: **Track** 🔂"
        elif self.player.queue.loop:
            self.player.queue.loop = False
            self.player.queue.loop_all = True
            msg = "Looping: **Queue** 🔁"
        else:
            self.player.queue.loop = False
            self.player.queue.loop_all = False
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
            # Fix thinking state
            return await interaction.followup.send(embed=create_embed("Not Found", f"No tracks found for: `{search}`", discord.Color.orange()))
        
        track = tracks[0]
        # Attach the requester to the track object
        track.requester = interaction.user
        
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
        await interaction.user.voice.channel.connect(cls=wavelink.Player)
        await interaction.response.send_message(embed=create_embed("Joined", f"✅ Connected to **{interaction.user.voice.channel.name}**", discord.Color.green()))
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
        await vc.disconnect()
        await interaction.response.send_message(embed=create_embed("Stopped", "⏹️ Music stopped and disconnected from voice channel.", discord.Color.blue()))
    else:
        await interaction.response.send_message(embed=create_embed("Error", "I'm not connected to any voice channel.", discord.Color.red()))

@bot.tree.command(name="loop", description="Toggle loop mode for the current track or queue")
@app_commands.describe(mode="Loop mode (off, track, queue)")
@app_commands.choices(mode=[
    app_commands.Choice(name="Off", value="off"),
    app_commands.Choice(name="Track", value="track"),
    app_commands.Choice(name="Queue", value="queue")
])
async def loop(interaction: discord.Interaction, mode: app_commands.Choice[str]):
    vc: wavelink.Player = interaction.guild.voice_client
    if not vc:
        return await interaction.response.send_message(embed=create_embed("Error", "I'm not connected to any voice channel.", discord.Color.red()))
    
    if mode.value == "off":
        vc.queue.loop = False
        vc.queue.loop_all = False
        msg = "Looping is now **disabled**."
    elif mode.value == "track":
        vc.queue.loop = True
        vc.queue.loop_all = False
        msg = "Now looping the **current track**."
    elif mode.value == "queue":
        vc.queue.loop = False
        vc.queue.loop_all = True
        msg = "Now looping the **entire queue**."
    
    await interaction.response.send_message(embed=create_embed("Loop Updated", f"🔁 {msg}", discord.Color.blue()))

@bot.tree.command(name="stay", description="Toggle 24/7 mode (prevent bot from leaving)")
async def stay(interaction: discord.Interaction):
    vc: wavelink.Player = interaction.guild.voice_client
    if not vc:
        return await interaction.response.send_message(embed=create_embed("Error", "I'm not connected to any voice channel.", discord.Color.red()))
    
    # Simple toggle for wavelink player's behavior if supported or just simulate
    if hasattr(vc, 'stay_247') and vc.stay_247:
        vc.stay_247 = False
        msg = "24/7 mode **disabled**."
    else:
        vc.stay_247 = True
        msg = "24/7 mode **enabled**."
        
    await interaction.response.send_message(embed=create_embed("24/7 Mode", f"🕒 {msg}", discord.Color.blue()))

@bot.tree.command(name="kick", description="Kick a member from the server")
@app_commands.checks.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    try:
        await member.kick(reason=reason)
        embed = create_embed("Member Kicked", f"**{member}** has been kicked.\n**Reason:** {reason}", discord.Color.red())
        embed.set_thumbnail(url=member.display_avatar.url)
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        await interaction.response.send_message(embed=create_embed("Error", f"Failed to kick member: {e}", discord.Color.red()), ephemeral=True)

@bot.tree.command(name="ban", description="Ban a member from the server")
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    try:
        await member.ban(reason=reason)
        embed = create_embed("Member Banned", f"**{member}** has been banned.\n**Reason:** {reason}", discord.Color.dark_red())
        embed.set_thumbnail(url=member.display_avatar.url)
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        await interaction.response.send_message(embed=create_embed("Error", f"Failed to ban member: {e}", discord.Color.red()), ephemeral=True)

@bot.tree.command(name="clear", description="Clear a specified amount of messages")
@app_commands.checks.has_permissions(manage_messages=True)
async def clear(interaction: discord.Interaction, amount: int):
    if amount < 1:
        return await interaction.response.send_message("Please specify an amount greater than 0.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(embed=create_embed("Messages Cleared", f"Successfully cleared **{len(deleted)}** messages.", discord.Color.green()))

@bot.tree.command(name="serverinfo", description="Display detailed information about this server")
async def serverinfo(interaction: discord.Interaction):
    guild = interaction.guild
    embed = discord.Embed(title=f"🏰 {guild.name}", description=guild.description or "No server description set.", color=discord.Color.blue())
    
    # Members count
    total_members = guild.member_count
    bots = len([m for m in guild.members if m.bot])
    humans = total_members - bots
    
    # Check if members list is populated (requires intents.members)
    if total_members > 0 and not guild.members:
         # Fallback if members are not cached
         bots = "Unknown (Enable Intents)"
         humans = "Unknown (Enable Intents)"
    
    # Formatting bits
    created_at = guild.created_at.strftime("%B %d, %Y")
    
    embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
    embed.add_field(name="👑 Owner", value=guild.owner.mention, inline=True)
    embed.add_field(name="🆔 ID", value=guild.id, inline=True)
    embed.add_field(name="📅 Created On", value=created_at, inline=True)
    
    embed.add_field(name="👥 Members", value=f"**Total:** {total_members}\n👤 **Humans:** {humans}\n🤖 **Bots:** {bots}", inline=True)
    embed.add_field(name="✨ Features", value="\n".join([f"• {f.replace('_', ' ').title()}" for f in guild.features[:5]]) or "None", inline=True)
    embed.add_field(name="📊 Stats", value=f"🎭 **Roles:** {len(guild.roles)}\n📁 **Categories:** {len(guild.categories)}\n💬 **Text:** {len(guild.text_channels)}\n🔊 **Voice:** {len(guild.voice_channels)}", inline=True)
    
    if guild.banner:
        embed.set_image(url=guild.banner.url)
    
    embed.set_footer(text=f"Requested by {interaction.user.name}", icon_url=interaction.user.display_avatar.url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="userinfo", description="Display detailed information about a member")
async def userinfo(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    
    roles = [role.mention for role in member.roles[1:]] # Exclude @everyone
    roles.reverse()
    
    embed = discord.Embed(title=f"👤 User Profile - {member.name}", color=member.color)
    embed.set_thumbnail(url=member.display_avatar.url)
    
    embed.add_field(name="📝 Name", value=f"**{member.name}**#{member.discriminator}", inline=True)
    embed.add_field(name="🆔 ID", value=member.id, inline=True)
    embed.add_field(name="🏷️ Nickname", value=member.nick or "None", inline=True)
    
    embed.add_field(name="📅 Joined Discord", value=member.created_at.strftime("%B %d, %Y"), inline=True)
    embed.add_field(name="📥 Joined Server", value=member.joined_at.strftime("%B %d, %Y") if member.joined_at else "Unknown", inline=True)
    embed.add_field(name="⭐ Top Role", value=member.top_role.mention, inline=True)
    
    embed.add_field(name=f"🎭 Roles ({len(roles)})", value=" ".join(roles[:10]) + ("..." if len(roles) > 10 else ""), inline=False)
    
    embed.set_footer(text=f"Requested by {interaction.user.name}", icon_url=interaction.user.display_avatar.url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="rps", description="Play Rock, Paper, Scissors!")
@app_commands.describe(choice="Choose your weapon!")
@app_commands.choices(choice=[
    app_commands.Choice(name="Rock", value="rock"),
    app_commands.Choice(name="Paper", value="paper"),
    app_commands.Choice(name="Scissors", value="scissors")
])
async def rps(interaction: discord.Interaction, choice: app_commands.Choice[str]):
    import random
    choices = ["rock", "paper", "scissors"]
    bot_choice = random.choice(choices)
    user_choice = choice.value
    
    result = ""
    if user_choice == bot_choice:
        result = "It's a **Draw**! 🤝"
        color = discord.Color.light_grey()
    elif (user_choice == "rock" and bot_choice == "scissors") or \
         (user_choice == "paper" and bot_choice == "rock") or \
         (user_choice == "scissors" and bot_choice == "paper"):
        result = "You **Won**! 🎉"
        color = discord.Color.green()
    else:
        result = "You **Lost**! 💀"
        color = discord.Color.red()
        
    emojis = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
    
    embed = discord.Embed(title="Rock Paper Scissors", color=color)
    embed.add_field(name="You", value=f"{emojis[user_choice]} {user_choice.title()}", inline=True)
    embed.add_field(name="AI Bot", value=f"{emojis[bot_choice]} {bot_choice.title()}", inline=True)
    embed.add_field(name="Result", value=result, inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="8ball", description="Ask the magic 8-ball a question")
async def eightball(interaction: discord.Interaction, question: str):
    import random
    responses = [
        "It is certain.", "It is decidedly so.", "Without a doubt.", "Yes - definitely.",
        "You may rely on it.", "As I see it, yes.", "Most likely.", "Outlook good.",
        "Yes.", "Signs point to yes.", "Reply hazy, try again.", "Ask again later.",
        "Better not tell you now.", "Cannot predict now.", "Concentrate and ask again.",
        "Don't count on it.", "My reply is no.", "My sources say no.",
        "Outlook not so good.", "Very doubtful."
    ]
    
    embed = discord.Embed(title="🔮 Magic 8-Ball", color=discord.Color.purple())
    embed.add_field(name="Question", value=question, inline=False)
    embed.add_field(name="Answer", value=random.choice(responses), inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="coinflip", description="Flip a coin and see the result!")
@app_commands.describe(choice="Heads or Tails?")
@app_commands.choices(choice=[
    app_commands.Choice(name="Heads", value="Heads"),
    app_commands.Choice(name="Tails", value="Tails")
])
async def coinflip(interaction: discord.Interaction, choice: app_commands.Choice[str] = None):
    import random
    result = random.choice(["Heads", "Tails"])
    
    embed = discord.Embed(title="🪙 Coin Flip", color=discord.Color.gold())
    
    if choice:
        user_guess = choice.value
        if user_guess == result:
            prediction = f"✅ You guessed right! It's **{result}**."
            embed.color = discord.Color.green()
        else:
            prediction = f"❌ Better luck next time! It's **{result}**."
            embed.color = discord.Color.red()
        embed.add_field(name="Prediction", value=prediction, inline=False)
    
    if result == "Heads":
        embed.description = "The coin spins in the air and lands on... **Heads**!"
        embed.set_thumbnail(url="https://i.imgur.com/vHshU7f.png") # Generic heads icon
    else:
        embed.description = "The coin spins in the air and lands on... **Tails**!"
        embed.set_thumbnail(url="https://i.imgur.com/nCHpEWy.png") # Generic tails icon
        
    embed.add_field(name="Result", value=f"✨ It's **{result}**!", inline=False)
    embed.set_footer(text=f"Flipped by {interaction.user.name}", icon_url=interaction.user.display_avatar.url)
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="avatar", description="View a member's avatar in full size")
async def avatar(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    
    embed = discord.Embed(title=f"🖼️ {member.name}'s Avatar", color=member.color or discord.Color.blue())
    embed.set_image(url=member.display_avatar.url)
    
    # Add links for different formats if possible
    avatar_url = member.display_avatar.url
    embed.description = f"[Download Avatar]({avatar_url})"
    
    embed.set_footer(text=f"Requested by {interaction.user.name}", icon_url=interaction.user.display_avatar.url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="meme", description="Get a random meme from AI")
async def meme(interaction: discord.Interaction):
    await interaction.response.defer()
    prompt = "Generate a short, funny meme caption or a quick joke related to gaming or discord bots."
    response = await get_ai_response(prompt)
    embed = create_embed("AI Meme / Joke", response, discord.Color.random())
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="poll", description="Create a simple poll")
async def poll(interaction: discord.Interaction, question: str, option1: str, option2: str):
    embed = create_embed("Server Poll", f"**{question}**")
    embed.add_field(name="Option 1", value=f"1️⃣ {option1}", inline=False)
    embed.add_field(name="Option 2", value=f"2️⃣ {option2}", inline=False)
    embed.set_footer(text=f"Poll created by {interaction.user.name}")
    
    await interaction.response.send_message(embed=embed)
    message = await interaction.original_response()
    await message.add_reaction("1️⃣")
    await message.add_reaction("2️⃣")

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

@bot.tree.command(name="steal", description="Steal an emoji from another server")
@app_commands.checks.has_permissions(manage_expressions=True)
async def steal(interaction: discord.Interaction, emoji: str, name: str = None):
    try:
        await interaction.response.defer()
        
        # Parse emoji URL
        import re
        import aiohttp
        
        # Match standard discord emoji format <:name:id> or <a:name:id>
        match = re.search(r"<(a?):(\w+):(\d+)>", emoji)
        if match:
            is_animated = bool(match.group(1))
            emoji_id = match.group(3)
            ext = "gif" if is_animated else "png"
            url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}"
            emoji_name = name or match.group(2)
        else:
            # Try to treat as a direct URL
            if emoji.startswith("http"):
                url = emoji
                emoji_name = name or "stolen_emoji"
            else:
                return await interaction.followup.send("Invalid emoji or URL provided.")

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return await interaction.followup.send("Failed to download emoji image.")
                image_data = await resp.read()
                
        new_emoji = await interaction.guild.create_custom_emoji(name=emoji_name, image=image_data)
        await interaction.followup.send(f"Successfully stolen {new_emoji} as **{emoji_name}**!")
        
    except Exception as e:
        await interaction.followup.send(f"Error: {e}")

def get_prefix(bot, message):
    if not message.guild:
        return "$"
    try:
        with open("prefixes.json", "r") as f:
            prefixes = json.load(f)
        return prefixes.get(str(message.guild.id), "$")
    except:
        return "$"

bot.command_prefix = get_prefix

@bot.tree.command(name="prefix", description="Change the bot's prefix for this server")
@app_commands.checks.has_permissions(manage_guild=True)
async def prefix(interaction: discord.Interaction, new_prefix: str):
    """Changes the command prefix for this server. Requires Manage Server permission."""
    try:
        prefixes = {}
        try:
            with open("prefixes.json", "r") as f:
                prefixes = json.load(f)
        except:
            pass
            
        prefixes[str(interaction.guild.id)] = new_prefix
        with open("prefixes.json", "w") as f:
            json.dump(prefixes, f)
            
        await interaction.response.send_message(f"✅ Success! The bot's prefix for this server has been changed to: `{new_prefix}`", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)

@bot.tree.command(name="automod", description="Setup basic automod (anti-spam, anti-invite, blacklist)")
@app_commands.describe(type="Type of protection", action="Action to take", words="Comma separated words for blacklist (only for Blacklist type)")
@app_commands.choices(type=[
    app_commands.Choice(name="Anti-Invite", value="anti_invite"),
    app_commands.Choice(name="Anti-Spam", value="anti_spam"),
    app_commands.Choice(name="Blacklist", value="blacklist")
], action=[
    app_commands.Choice(name="Warn", value="warn"),
    app_commands.Choice(name="Delete", value="delete"),
    app_commands.Choice(name="Both", value="both")
])
@app_commands.checks.has_permissions(administrator=True)
async def automod(interaction: discord.Interaction, type: app_commands.Choice[str], action: app_commands.Choice[str], words: str = None):
    try:
        config = {}
        try:
            with open("automod.json", "r") as f:
                config = json.load(f)
        except:
            pass
            
        if str(interaction.guild.id) not in config:
            config[str(interaction.guild.id)] = {}
            
        config[str(interaction.guild.id)][type.value] = action.value
        if type.value == "blacklist" and words:
            config[str(interaction.guild.id)]["blacklisted_words"] = [w.strip().lower() for w in words.split(",")]
        
        with open("automod.json", "w") as f:
            json.dump(config, f)
            
        msg = f"AutoMod `{type.name}` set to `{action.name}`!"
        if type.value == "blacklist" and words:
            msg += f" Words: `{words}`"
        await interaction.response.send_message(msg)
    except Exception as e:
        await interaction.response.send_message(f"Error: {e}")

# Anti-spam basic implementation
user_messages = {}

@bot.event
async def on_message(message):
    if message.author.bot: return
    
    # AutoMod Logic
    if message.guild:
        try:
            with open("automod.json", "r") as f:
                config = json.load(f).get(str(message.guild.id), {})
            
            # Anti-Invite
            if config.get("anti_invite"):
                if "discord.gg/" in message.content or "discord.com/invite/" in message.content:
                    action = config["anti_invite"]
                    if action in ["delete", "both"]:
                        await message.delete()
                    if action in ["warn", "both"]:
                        await message.channel.send(f"{message.author.mention}, invites are not allowed!", delete_after=5)
                    return

            # Anti-Spam (Very basic)
            if config.get("anti_spam"):
                user_id = message.author.id
                now = asyncio.get_event_loop().time()
                if user_id not in user_messages:
                    user_messages[user_id] = []
                user_messages[user_id].append(now)
                # Filter to last 5 seconds
                user_messages[user_id] = [t for t in user_messages[user_id] if now - t < 5]
                if len(user_messages[user_id]) > 5:
                    action = config["anti_spam"]
                    if action in ["delete", "both"]:
                        await message.delete()
                    if action in ["warn", "both"]:
                        await message.channel.send(f"{message.author.mention}, stop spamming!", delete_after=5)
                    return

            # Blacklist Words
            if config.get("blacklist"):
                blacklisted = config.get("blacklisted_words", [])
                content_lower = message.content.lower()
                if any(word in content_lower for word in blacklisted):
                    action = config["blacklist"]
                    if action in ["delete", "both"]:
                        await message.delete()
                    if action in ["warn", "both"]:
                        await message.channel.send(f"{message.author.mention}, your message contained a blacklisted word!", delete_after=5)
                    return
        except:
            pass

    # Rest of the on_message logic...
    config = load_channel_config()
    channel_id = config["channels"].get(str(message.guild.id))
    
    # Handle song play on mention
    content = message.content.replace(f'<@!{bot.user.id}>', '').replace(f'<@{bot.user.id}>', '').strip()
    if bot.user.mentioned_in(message) and content.lower().startswith('play '):
        search = content[5:].strip()
        if search:
            # Get the command and invoke it
            ctx = await bot.get_context(message)
            # We can't easily invoke a slash command as a prefix command without refactoring,
            # so we'll implement the logic directly or call the function.
            # However, for simplicity and since it's a small edit, let's trigger the play logic.
            if not message.author.voice:
                return await message.channel.send(embed=create_embed("Error", "You need to join a voice channel first!", discord.Color.red()))
            
            try:
                vc: wavelink.Player = message.guild.voice_client or await message.author.voice.channel.connect(cls=wavelink.Player)
                vc.home_channel = message.channel
                
                tracks = await wavelink.Playable.search(search)
                if not tracks:
                    return await message.channel.send(embed=create_embed("Not Found", f"No tracks found for: `{search}`", discord.Color.orange()))
                
                track = tracks[0]
                # Attach the requester to the track object
                track.requester = message.author
                
                embed = get_track_embed("Playing Now", track)
                embed.color = discord.Color.green()
                
                if vc.playing:
                    await vc.queue.put_wait(track)
                    embed.title = "Added to Queue"
                    await message.channel.send(embed=embed)
                else:
                    await vc.play(track)
                    await message.channel.send(embed=embed)
                return
            except Exception as e:
                return await message.channel.send(embed=create_embed("Error", f"An error occurred: `{str(e)}`", discord.Color.red()))

    if (channel_id and message.channel.id == channel_id) or bot.user.mentioned_in(message):
        async with message.channel.typing():
            response = await get_ai_response(content)
            for i in range(0, len(response), 2000):
                await message.reply(response[i:i+2000])
    await bot.process_commands(message)

if __name__ == "__main__":
    keep_alive()
    bot.run(discord_token)
