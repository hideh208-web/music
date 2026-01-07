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
        return await interaction.response.send_message("Please choose either Rock, Paper, or Scissors!", ephemeral=True)
    
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
async def coinflip(interaction: discord.Interaction):
    import random
    result = random.choice(["Heads", "Tails"])
    
    embed = discord.Embed(title="🪙 Coin Flip", color=discord.Color.gold())
    
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
