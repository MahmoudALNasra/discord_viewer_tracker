import logging
from datetime import datetime
import config

import discord
from discord.ext import commands
import os
import json
import asyncio
import shutil
import base64
import aiohttp

from tracker import VoiceTimeTracker
from database import VoiceTrackerDatabase

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("voice-tracker")

if not config.BOT_TOKEN:
    logger.error("BOT_TOKEN not set. Set it via environment variable or in a .env file.")
    raise SystemExit("Missing BOT_TOKEN")

class GitHubBackup:
    def __init__(self):
        self.github_token = os.getenv('GITHUB_TOKEN')
        self.repo_owner = "MahmoudALNasra"
        self.repo_name = "discord_viewer_tracker"
        self.backup_dir = "backups"
        
    async def upload_to_github(self, file_path, content):
        """Upload backup directly to GitHub using API"""
        if not self.github_token:
            return False, "GITHUB_TOKEN not set"
            
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            github_path = f"{self.backup_dir}/{file_path}_{timestamp}"
            
            url = f"https://api.github.com/repos/{self.repo_owner}/{self.repo_name}/contents/{github_path}"
            
            headers = {
                "Authorization": f"token {self.github_token}",
                "Accept": "application/vnd.github.v3+json"
            }
            
            data = {
                "message": f"🤖 Automated backup {timestamp}",
                "content": base64.b64encode(content).decode('utf-8'),
                "branch": "main"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.put(url, headers=headers, json=data) as response:
                    if response.status in [200, 201]:
                        return True, f"Uploaded to GitHub: {github_path}"
                    else:
                        error_text = await response.text()
                        return False, f"GitHub API error: {response.status} - {error_text}"
                        
        except Exception as e:
            return False, f"GitHub upload failed: {str(e)}"
    
    async def create_backup(self):
        """Create database backup and upload to GitHub"""
        try:
            os.makedirs(self.backup_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            if not os.path.exists('voice_tracker.db'):
                return False, "Database file not found"
            
            with open('voice_tracker.db', 'rb') as f:
                db_content = f.read()
            
            success, message = await self.upload_to_github("voice_tracker_backup.db", db_content)
            if not success:
                return False, message
            
            try:
                db = VoiceTrackerDatabase()
                voice_users = db.get_top_voice_users(limit=1000)
                streamers = db.get_top_streamers(limit=1000)
                
                export_data = {
                    'export_date': datetime.now().isoformat(),
                    'backup_timestamp': timestamp,
                    'voice_users': voice_users,
                    'streamers': streamers,
                    'voice_users_count': len(voice_users),
                    'streamers_count': len(streamers)
                }
                
                json_content = json.dumps(export_data, indent=2, ensure_ascii=False, default=str).encode('utf-8')
                
                success, json_message = await self.upload_to_github("data_export.json", json_content)
                if not success:
                    logger.warning(f"JSON upload failed: {json_message}")
                    return True, f"Database backed up, but JSON failed: {message}"
                
                return True, f"Backup completed!\n• Database: {len(db_content)} bytes\n• JSON: {len(voice_users)} users, {len(streamers)} streamers"
                
            except Exception as e:
                logger.warning(f"JSON export failed: {e}")
                return True, f"Database backed up successfully (JSON failed: {str(e)})"
                
        except Exception as e:
            logger.error(f"❌ Backup failed: {e}")
            return False, f"Backup failed: {str(e)}"
    
    def create_local_backup(self):
        """Create local backup files as fallback"""
        try:
            os.makedirs(self.backup_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            if os.path.exists('voice_tracker.db'):
                backup_file = f"{self.backup_dir}/voice_tracker_backup_{timestamp}.db"
                shutil.copy2('voice_tracker.db', backup_file)
                
                json_file = f"{self.backup_dir}/data_export_{timestamp}.json"
                db = VoiceTrackerDatabase()
                
                export_data = {
                    'export_date': datetime.now().isoformat(),
                    'voice_users': db.get_top_voice_users(limit=1000),
                    'streamers': db.get_top_streamers(limit=1000)
                }
                
                with open(json_file, 'w', encoding='utf-8') as f:
                    json.dump(export_data, f, indent=2, ensure_ascii=False, default=str)
                
                self.cleanup_old_backups(keep_count=5)
                
                return True, f"Local backup created:\n• {backup_file}\n• {json_file}"
            else:
                return False, "Database file not found"
                
        except Exception as e:
            return False, f"Local backup failed: {str(e)}"
    
    def cleanup_old_backups(self, keep_count=5):
        """Keep only the most recent backup files"""
        try:
            if not os.path.exists(self.backup_dir):
                return
                
            db_files = [f for f in os.listdir(self.backup_dir) if f.startswith('voice_tracker_backup_') and f.endswith('.db')]
            json_files = [f for f in os.listdir(self.backup_dir) if f.startswith('data_export_') and f.endswith('.json')]
            
            db_files.sort(reverse=True)
            json_files.sort(reverse=True)
            
            for old_file in db_files[keep_count:]:
                os.remove(os.path.join(self.backup_dir, old_file))
                logger.info(f"🧹 Removed old backup: {old_file}")
                
            for old_file in json_files[keep_count:]:
                os.remove(os.path.join(self.backup_dir, old_file))
                logger.info(f"🧹 Removed old JSON export: {old_file}")
                
        except Exception as e:
            logger.warning(f"⚠️ Cleanup failed: {e}")

class VoiceTrackerBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.voice_states = True
        intents.messages = True
        intents.guilds = True
        intents.message_content = True

        super().__init__(command_prefix='!vt ', intents=intents)

        self.database = VoiceTrackerDatabase(db_path=config.DB_PATH)
        self.tracker = VoiceTimeTracker(self.database)
        self.github_backup = GitHubBackup()

    async def on_ready(self):
        logger.info(f"✅ {self.user} has connected to Discord!")
        logger.info(f"📊 Voice Tracker is monitoring {len(self.guilds)} server(s):")
        for guild in self.guilds:
            logger.info(f"   - {guild.name} (ID: {guild.id})")
        logger.info("🤖 Use !vt bot_help for commands")
        
        self.backup_task = self.loop.create_task(self.auto_backup())

    async def auto_backup(self):
        """Automatically backup every 6 hours"""
        await self.wait_until_ready()
        await asyncio.sleep(60)
        
        while not self.is_closed():
            try:
                logger.info("🕒 Starting automated backup...")
                
                success, message = await self.github_backup.create_backup()
                
                if not success:
                    success, message = await asyncio.get_event_loop().run_in_executor(
                        None, self.github_backup.create_local_backup
                    )
                
                if success:
                    logger.info("✅ Automated backup completed")
                else:
                    logger.warning(f"⚠️ Automated backup failed: {message}")
                    
                await asyncio.sleep(21600)
                    
            except Exception as e:
                logger.error(f"❌ Auto-backup task error: {e}")
                await asyncio.sleep(3600)

    async def on_voice_state_update(self, member, before, after):
        await self.tracker.handle_voice_state_update(member, before, after)

@commands.command()
async def bot_help(ctx):
    """Show available commands"""
    embed = discord.Embed(
        title="🎧 Voice & Stream Tracker Help",
        description="Track voice channel time and streaming statistics",
        color=0x7289DA
    )
    embed.add_field(name="!vt topstreamers", value="Show top 5 streamers", inline=False)
    embed.add_field(name="!vt topvoice", value="Show top 5 voice channel users", inline=False)
    embed.add_field(name="!vt mystats", value="Show your personal statistics", inline=False)
    embed.add_field(name="!vt debug", value="Debug database information", inline=False)
    embed.add_field(name="!vt backup", value="Manual database backup", inline=False)
    embed.add_field(name="!vt backup_status", value="Check backup system status", inline=False)
    embed.add_field(name="!vt bot_help", value="Show this help message", inline=False)

    await ctx.send(embed=embed)

@commands.command()
@commands.is_owner()
async def backup(ctx):
    """Manual database backup (Owner only)"""
    message = await ctx.send("🔄 Starting manual backup...")
    
    try:
        success, result_message = await ctx.bot.github_backup.create_backup()
        
        if not success:
            success, result_message = await asyncio.get_event_loop().run_in_executor(
                None, ctx.bot.github_backup.create_local_backup
            )
        
        if success:
            embed = discord.Embed(
                title="✅ Backup Successful",
                description=result_message,
                color=0x00ff00,
                timestamp=datetime.now()
            )
            if os.path.exists('voice_tracker.db'):
                size = os.path.getsize('voice_tracker.db')
                embed.add_field(name="Database Size", value=f"{size/1024/1024:.2f} MB", inline=True)
        else:
            embed = discord.Embed(
                title="❌ Backup Failed",
                description=result_message,
                color=0xff0000,
                timestamp=datetime.now()
            )
            
        await message.edit(content="", embed=embed)
        
    except Exception as e:
        await message.edit(content=f"❌ Backup error: {str(e)}")

@commands.command()
@commands.is_owner()
async def backup_status(ctx):
    """Check backup status and configuration"""
    embed = discord.Embed(
        title="🔧 Backup System Status",
        color=0x7289DA,
        timestamp=datetime.now()
    )
    
    if ctx.bot.github_backup.github_token:
        embed.add_field(name="GitHub Token", value="✅ Configured", inline=True)
    else:
        embed.add_field(name="GitHub Token", value="❌ Not set", inline=True)
    
    if os.path.exists('voice_tracker.db'):
        size = os.path.getsize('voice_tracker.db')
        embed.add_field(name="Database File", value=f"✅ {size/1024/1024:.2f} MB", inline=True)
        
        try:
            voice_users = ctx.bot.database.get_top_voice_users(limit=1000)
            streamers = ctx.bot.database.get_top_streamers(limit=1000)
            embed.add_field(name="Data Stats", value=f"🎧 {len(voice_users)} users\n🎬 {len(streamers)} streamers", inline=True)
        except:
            embed.add_field(name="Data Stats", value="❌ Error reading", inline=True)
    else:
        embed.add_field(name="Database File", value="❌ Not found", inline=True)
    
    if os.path.exists('backups'):
        backup_files = len([f for f in os.listdir('backups') if f.endswith('.db')])
        embed.add_field(name="Local Backups", value=f"📁 {backup_files} files", inline=True)
    else:
        embed.add_field(name="Local Backups", value="📁 No backups yet", inline=True)
    
    await ctx.send(embed=embed)

@commands.command()
async def topstreamers(ctx):
    """Show top 5 streamers by total stream time"""
    top_streamers = ctx.bot.database.get_top_streamers(5)

    embed = discord.Embed(
        title="🎬 Top 5 Streamers",
        description="Most dedicated streamers by total stream time",
        color=0x9146FF,
        timestamp=datetime.now()
    )

    if top_streamers:
        for i, streamer in enumerate(top_streamers, 1):
            hours = streamer['total_stream_time'] / 3600
            sessions = streamer['sessions']

            user = ctx.bot.get_user(streamer['user_id'])
            if user:
                username = getattr(user, "display_name", getattr(user, "name", "Unknown"))
            else:
                try:
                    user = await ctx.bot.fetch_user(streamer['user_id'])
                    username = getattr(user, "display_name", getattr(user, "name", "Unknown"))
                except Exception:
                    username = "Unknown User"

            embed.add_field(
                name=f"{i}. {username}",
                value=f"⏱️ {hours:.1f} hours • {sessions} streams",
                inline=False
            )
    else:
        embed.description = "No streaming data yet! Start streaming to see stats."

    await ctx.send(embed=embed)

@commands.command()
async def topvoice(ctx):
    """Show top 5 voice channel users by time spent"""
    top_voice_users = ctx.bot.database.get_top_voice_users(5)

    embed = discord.Embed(
        title="🎧 Top 5 Voice Champions",
        description="Most active users in voice channels",
        color=0x00ff00,
        timestamp=datetime.now()
    )

    if top_voice_users:
        for i, user_data in enumerate(top_voice_users, 1):
            hours = user_data['total_voice_time'] / 3600
            sessions = user_data['sessions']

            user = ctx.bot.get_user(user_data['user_id'])
            if user:
                username = getattr(user, "display_name", getattr(user, "name", "Unknown"))
            else:
                try:
                    user = await ctx.bot.fetch_user(user_data['user_id'])
                    username = getattr(user, "display_name", getattr(user, "name", "Unknown"))
                except Exception:
                    username = "Unknown User"

            embed.add_field(
                name=f"{i}. {username}",
                value=f"⏱️ {hours:.1f} hours • {sessions} sessions",
                inline=False
            )
    else:
        embed.description = "No voice channel data yet! Join voice channels to see stats."

    await ctx.send(embed=embed)

@commands.command()
async def mystats(ctx):
    """Show user's personal statistics"""
    user_id = ctx.author.id

    top_streamers = ctx.bot.database.get_top_streamers(100)
    user_stream_rank = None
    user_stream_stats = None

    for i, streamer in enumerate(top_streamers, 1):
        if streamer['user_id'] == user_id:
            user_stream_rank = i
            user_stream_stats = streamer
            break

    top_voice_users = ctx.bot.database.get_top_voice_users(100)
    user_voice_rank = None
    user_voice_stats = None

    for i, user_data in enumerate(top_voice_users, 1):
        if user_data['user_id'] == user_id:
            user_voice_rank = i
            user_voice_stats = user_data
            break

    embed = discord.Embed(
        title=f"📊 {ctx.author.display_name}'s Statistics",
        color=0x7289DA,
        timestamp=datetime.now()
    )

    if user_stream_stats:
        stream_hours = user_stream_stats['total_stream_time'] / 3600
        embed.add_field(
            name="🎬 Streaming",
            value=f"**{stream_hours:.1f} hours**\n#{user_stream_rank} • {user_stream_stats['sessions']} streams",
            inline=True
        )
    else:
        embed.add_field(
            name="🎬 Streaming",
            value="No streaming data",
            inline=True
        )

    if user_voice_stats:
        voice_hours = user_voice_stats['total_voice_time'] / 3600
        embed.add_field(
            name="🎧 Voice Time",
            value=f"**{voice_hours:.1f} hours**\n#{user_voice_rank} • {user_voice_stats['sessions']} sessions",
            inline=True
        )
    else:
        embed.add_field(
            name="🎧 Voice Time",
            value="No voice data",
            inline=True
        )

    await ctx.send(embed=embed)

@commands.command()
async def debug(ctx):
    """Debug command to check database"""
    user_id = ctx.author.id

    top_voice = ctx.bot.database.get_top_voice_users(10)
    top_stream = ctx.bot.database.get_top_streamers(10)

    user_in_voice = any(user['user_id'] == user_id for user in top_voice)
    user_in_stream = any(user['user_id'] == user_id for user in top_stream)

    debug_info = f"**Debug Info for {ctx.author.display_name}**\n"
    debug_info += f"User ID: {user_id}\n"
    debug_info += f"Voice Users in DB: {len(top_voice)}\n"
    debug_info += f"Streamers in DB: {len(top_stream)}\n"
    debug_info += f"You in Voice Data: {user_in_voice}\n"
    debug_info += f"You in Stream Data: {user_in_stream}\n"

    await ctx.send(f"```{debug_info}```")

if __name__ == "__main__":
    bot = VoiceTrackerBot()

    bot.add_command(bot_help)
    bot.add_command(topstreamers)
    bot.add_command(topvoice)
    bot.add_command(mystats)
    bot.add_command(debug)
    bot.add_command(backup)
    bot.add_command(backup_status)

    @bot.event
    async def on_message(message):
        if message.author == bot.user:
            return

        if message.content.startswith('!vt '):
            logger.info(f"📨 Command received: '{message.content}' from {message.author}")

        await bot.process_commands(message)

    logger.info("🚀 Starting Discord Voice & Stream Tracker...")
    try:
        bot.run(config.BOT_TOKEN)
    except Exception as e:
        logger.exception("Bot terminated with an exception: %s", e)
