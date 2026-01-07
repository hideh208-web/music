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
intents = discord.Intents.all()  # Using all intents to ensure message content and member info works
bot = commands.Bot(command_prefix="$", intents=intents, help_command=None)

class MusicControlView(discord.ui.View):
    def __init__(self, player: wavelink.Player):
        super().__init__(timeout=None)
        self.player = player

    @discord.ui.button(label="Pause/Resume", style=discord.ButtonStyle.secondary, emoji="⏯️")
    async def toggle_pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        # Wavelink 3.x uses player.paused and player.pause()
        await self.player.pause(not self.player.paused)
        status = "paused" if self.player.paused else "resumed"
        await interaction.followup.send(f"Music {status}!", ephemeral=True)

    @discord.ui.button(label="Loop", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def loop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        if self.player.queue.mode == wavelink.QueueMode.loop:
            self.player.queue.mode = wavelink.QueueMode.normal
            status = "Loop disabled"
        else:
            self.player.queue.mode = wavelink.QueueMode.loop
            status = "Loop enabled"
        await interaction.followup.send(status, ephemeral=True)

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
        await wavelink.Pool.connect(nodes=[node], client=bot, cache_capacity=100)
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
    
    # Delete the old control panel if it exists from a previous song
    if hasattr(player, 'controller_message'):
        try:
            await player.controller_message.delete()
        except Exception as e:
            logger.debug(f"Failed to delete old controller message: {e}")
        delattr(player, 'controller_message')

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
        except Exception as e:
            logger.debug(f"Failed to delete controller message on track end: {e}")
        delattr(player, 'controller_message')

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

@bot.tree.command(name="role", description="Manage user roles")
@app_commands.checks.has_permissions(manage_roles=True)
@app_commands.describe(action="Add or remove the role", member="The member to manage", role="The role to add or remove")
async def role_cmd(interaction: discord.Interaction, action: str, member: discord.Member, role: discord.Role):
    if action.lower() == "add":
        try:
            await member.add_roles(role)
            await interaction.response.send_message(embed=create_embed("Role Added", f"Added {role.mention} to {member.mention}", discord.Color.green()))
        except Exception as e:
            await interaction.response.send_message(f"Error: {e}", ephemeral=True)
    elif action.lower() == "remove":
        try:
            await member.remove_roles(role)
            await interaction.response.send_message(embed=create_embed("Role Removed", f"Removed {role.mention} from {member.mention}", discord.Color.red()))
        except Exception as e:
            await interaction.response.send_message(f"Error: {e}", ephemeral=True)
    else:
        await interaction.response.send_message("Please specify 'add' or 'remove' as the action.", ephemeral=True)

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
            # Cleanly format title and author
            track_info = f"{t.title} - {t.author}"
            description += f"{i+1}. {track_info}\n"
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
    
    # Member counts
    total_members = guild.member_count
    bots = len([m for m in guild.members if m.bot])
    humans = total_members - bots
    
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
async def rps(interaction: discord.Interaction, choice: str):
    import random
    choices = ["rock", "paper", "scissors"]
    bot_choice = random.choice(choices)
    user_choice = choice.lower()
    
    if user_choice not in choices:
    
